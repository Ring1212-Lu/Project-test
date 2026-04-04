#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自動交易機器人 — 結合監控系統 + Pionex API
==========================================
安全機制：
1. 預設模擬交易模式（paper_mode=True）
2. 每日最大虧損限制（觸發後自動停止）
3. 單次最大倉位限制
4. 連續虧損自動停機
5. 最大同時持倉數限制
6. 所有交易記錄寫入日誌
7. 需手動輸入 "LIVE" 才能切換真實交易

用法：
    python trading_bot.py                     # 模擬交易（預設）
    python trading_bot.py --live              # 真實交易（需要 API key）
    python trading_bot.py --balance 100       # 設定初始模擬資金
    python trading_bot.py --max-loss-pct 10   # 每日最大虧損 10%
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed

from pionex_client import PionexClient
from crypto_monitor_v2 import (
    fetch_tickers, fetch_klines, fetch_and_analyze, get_btc_trend,
    calc_rsi_wilder, calc_ema,
    TOP15_PCT, TOP15_MAX, TOP_N, INTERVAL, MAX_WORKERS,
)
from learning_engine import LearningEngine

try:
    import colorama
    colorama.init()
    def color(text, c):
        codes = {'red': '\033[91m', 'green': '\033[92m', 'yellow': '\033[93m',
                 'cyan': '\033[96m', 'end': '\033[0m'}
        return f"{codes.get(c, '')}{text}{codes['end']}"
except ImportError:
    def color(text, c):
        return text


# ===== 安全設定 =====
class RiskManager:
    """風控管理器"""

    def __init__(self, initial_balance, config=None):
        if config is None:
            config = {}
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        self.daily_start_balance = initial_balance

        # 風控參數
        self.max_loss_pct = config.get("max_loss_pct", 10)         # 每日最大虧損 %
        self.max_position_pct = config.get("max_position_pct", 20) # 單次最大倉位 %
        self.max_positions = config.get("max_positions", 2)        # 最大同時持倉
        self.max_consecutive_loss = config.get("max_consecutive_loss", 3)
        self.min_signal_strength = config.get("min_signal_strength", "STRONG")
        self.min_score = config.get("min_score", 60)
        self.min_win_rate = config.get("min_win_rate", 55)
        self.min_rr = config.get("min_rr", 1.3)

        # 狀態追蹤
        self.open_positions = []
        self.closed_trades = []
        self.consecutive_losses = 0
        self.daily_pnl = 0
        self.today = date.today()
        self.halted = False
        self.halt_reason = ""

    def new_day_check(self):
        """檢查是否新的一天，重置日計數器"""
        if date.today() != self.today:
            self.today = date.today()
            self.daily_start_balance = self.current_balance
            self.daily_pnl = 0
            if self.halted and "daily" in self.halt_reason:
                self.halted = False
                self.halt_reason = ""
                print(color("[RISK] 新的一天，解除每日虧損停機", 'green'))

    def can_open_position(self, signal):
        """檢查是否允許開倉"""
        self.new_day_check()

        if self.halted:
            return False, f"已停機: {self.halt_reason}"

        # 每日虧損檢查
        daily_loss_pct = abs(self.daily_pnl) / self.daily_start_balance * 100 if self.daily_start_balance > 0 else 0
        if self.daily_pnl < 0 and daily_loss_pct >= self.max_loss_pct:
            self.halted = True
            self.halt_reason = f"daily loss {daily_loss_pct:.1f}% >= {self.max_loss_pct}%"
            return False, f"每日虧損已達 {daily_loss_pct:.1f}%，停止交易"

        # 連續虧損檢查
        if self.consecutive_losses >= self.max_consecutive_loss:
            self.halted = True
            self.halt_reason = f"consecutive losses: {self.consecutive_losses}"
            return False, f"連續虧損 {self.consecutive_losses} 次，停止交易"

        # 持倉數檢查
        if len(self.open_positions) >= self.max_positions:
            return False, f"持倉數已達上限 {self.max_positions}"

        # 信號品質檢查
        strength = signal.get("signal_strength", "WEAK")
        strength_order = {"STRONG": 3, "MEDIUM": 2, "WEAK": 1}
        min_order = strength_order.get(self.min_signal_strength, 3)
        if strength_order.get(strength, 0) < min_order:
            return False, f"信號強度 {strength} 不足（需要 {self.min_signal_strength}）"

        if signal.get("best_score", 0) < self.min_score:
            return False, f"分數 {signal['best_score']} < {self.min_score}"

        if signal.get("best_rate", 0) < self.min_win_rate:
            return False, f"勝率 {signal['best_rate']}% < {self.min_win_rate}%"

        if signal.get("rr", 0) < self.min_rr:
            return False, f"風報比 {signal['rr']} < {self.min_rr}"

        return True, "OK"

    def calc_position_size(self, signal):
        """計算開倉大小（基於 Kelly 和風控限制）"""
        kelly = signal.get("kelly_pct", 10)
        max_pct = min(kelly, self.max_position_pct)
        size_usd = self.current_balance * (max_pct / 100)
        return round(size_usd, 2), max_pct

    def record_open(self, position):
        """記錄開倉"""
        self.open_positions.append(position)

    def record_close(self, position, pnl):
        """記錄平倉"""
        self.open_positions = [p for p in self.open_positions if p["id"] != position["id"]]
        position["pnl"] = pnl
        position["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.closed_trades.append(position)

        self.current_balance += pnl
        self.daily_pnl += pnl

        if pnl >= 0:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

    def get_status(self):
        """取得風控狀態摘要"""
        daily_pnl_pct = (self.daily_pnl / self.daily_start_balance * 100) if self.daily_start_balance > 0 else 0
        total_pnl = self.current_balance - self.initial_balance
        total_pnl_pct = (total_pnl / self.initial_balance * 100) if self.initial_balance > 0 else 0
        wins = sum(1 for t in self.closed_trades if t.get("pnl", 0) > 0)
        total = len(self.closed_trades)
        wr = round(wins / total * 100, 1) if total > 0 else 0

        return {
            "balance": round(self.current_balance, 2),
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_pnl_pct": round(daily_pnl_pct, 1),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 1),
            "open_positions": len(self.open_positions),
            "total_trades": total,
            "win_rate": wr,
            "consecutive_losses": self.consecutive_losses,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
        }


# ===== 交易日誌 =====

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_log.json")

def save_trade_log(risk_mgr):
    """儲存交易記錄"""
    data = {
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": risk_mgr.get_status(),
        "open_positions": risk_mgr.open_positions,
        "closed_trades": risk_mgr.closed_trades[-200:],  # 最多保留 200 筆
    }
    try:
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except IOError:
        pass


# ===== 持倉監控 =====

def check_positions(risk_mgr, client):
    """檢查持倉是否觸及止盈/止損"""
    if not risk_mgr.open_positions:
        return

    # 取得當前價格
    from crypto_monitor_v2 import _session, TICK_URL
    try:
        r = _session.get(TICK_URL, params={"type": "PERP"}, timeout=10)
        if r.status_code != 200:
            return
        tickers = r.json().get("data", {}).get("tickers", [])
        prices = {}
        for t in tickers:
            try:
                prices[t["symbol"]] = float(t["close"])
            except (KeyError, ValueError):
                pass
    except Exception:
        return

    for pos in list(risk_mgr.open_positions):
        sym = pos["symbol"]
        current = prices.get(sym)
        if current is None:
            continue

        entry = pos["entry_price"]
        tp = pos["tp_price"]
        sl = pos["sl_price"]
        side = pos["side"]  # "BUY" or "SELL"
        size = pos["size"]

        should_close = False
        reason = ""

        if side == "SELL":  # 做空
            pnl_pct = (entry - current) / entry * 100
            if current <= tp:
                should_close = True
                reason = "TP"
            elif current >= sl:
                should_close = True
                reason = "SL"
        else:  # 做多
            pnl_pct = (current - entry) / entry * 100
            if current >= tp:
                should_close = True
                reason = "TP"
            elif current <= sl:
                should_close = True
                reason = "SL"

        if should_close:
            # 計算 PnL
            if side == "SELL":
                pnl = (entry - current) / entry * size
            else:
                pnl = (current - entry) / entry * size

            # 平倉
            close_side = "BUY" if side == "SELL" else "SELL"
            result = client.place_order(sym, close_side, "MARKET", pos["quantity"])

            risk_mgr.record_close(pos, round(pnl, 4))
            pnl_str = f"+{pnl:.4f}" if pnl >= 0 else f"{pnl:.4f}"
            pnl_color = 'green' if pnl >= 0 else 'red'

            print(color(f"\n[CLOSE] {sym} {reason} | PnL: {pnl_str}U ({pnl_pct:+.2f}%)", pnl_color))
            print(f"   Entry: {entry} -> Exit: {current}")
            save_trade_log(risk_mgr)


# ===== 主交易循環 =====

LEARNING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "learning_data.json")


def run_trading_loop(client, risk_mgr, learner):
    """主交易循環"""
    round_num = 0
    prev_strat = {}  # 記錄上一輪策略，用於一致性檢查

    while True:
        round_num += 1
        status = risk_mgr.get_status()

        print(color(f"\n{'='*62}", 'cyan'))
        mode_str = color("PAPER", 'yellow') if client.paper_mode else color("LIVE", 'red')
        print(f" [{mode_str}] 自動交易機器人 | 第 {round_num} 輪")
        print(f" Balance: {status['balance']}U | "
              f"Daily: {status['daily_pnl']:+.2f}U ({status['daily_pnl_pct']:+.1f}%) | "
              f"Total: {status['total_pnl']:+.2f}U ({status['total_pnl_pct']:+.1f}%)")
        print(f" Trades: {status['total_trades']} | "
              f"WR: {status['win_rate']}% | "
              f"Open: {status['open_positions']} | "
              f"ConsLoss: {status['consecutive_losses']}")
        if status['halted']:
            print(color(f" HALTED: {status['halt_reason']}", 'red'))
        print(color(f"{'='*62}", 'cyan'))

        # 檢查現有持倉
        check_positions(risk_mgr, client)

        # 驗證學習資料
        validated = learner.validate_pending_predictions()
        if validated > 0:
            print(f"[LEARN] 驗證了 {validated} 筆預測")

        # 取得優化參數
        opt_params = learner.get_optimized_params()

        # BTC 大盤
        btc_trend = get_btc_trend()
        btc_str = {1: "UP", -1: "DOWN", 0: "NEUTRAL"}.get(btc_trend, "?")
        btc_color = 'green' if btc_trend == 1 else 'red' if btc_trend == -1 else 'yellow'
        print(f"\n[BTC] {color(btc_str, btc_color)}")

        # 抓行情
        tickers = fetch_tickers()
        perps = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("_USDT_PERP"):
                continue
            try:
                o = float(t.get("open", 0))
                c = float(t.get("close", 0))
                vol = float(t.get("amount", 0))
                if o <= 0 or c <= 0 or vol < 5000:
                    continue
                change = (c - o) / o * 100
                perps.append({"symbol": sym, "change": change, "price": c})
            except (ValueError, ZeroDivisionError):
                continue

        if not perps:
            print("[ERROR] 無行情資料")
            time.sleep(INTERVAL)
            continue

        perps.sort(key=lambda x: x["change"], reverse=True)
        n15 = max(1, int(len(perps) * TOP15_PCT))
        gainers = perps[:n15][:TOP15_MAX]
        losers = list(reversed(perps[-n15:]))[:TOP15_MAX]
        pool = gainers + losers

        seen = set()
        pool = [c for c in pool if c["symbol"] not in seen and not seen.add(c["symbol"])]

        # 不重複分析已持有的幣種
        held_symbols = {p["symbol"] for p in risk_mgr.open_positions}
        pool = [c for c in pool if c["symbol"] not in held_symbols]

        print(f"[INFO] 分析池 {len(pool)} 個（排除已持倉）")

        # 並行分析
        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_map = {
                executor.submit(fetch_and_analyze, coin, learner, opt_params, btc_trend): coin
                for coin in pool
            }
            for future in as_completed(future_map):
                try:
                    res = future.result()
                    if res:
                        results.append(res)
                except Exception:
                    pass

        if not results:
            print("[INFO] 無有效信號")
            save_trade_log(risk_mgr)
            time.sleep(INTERVAL)
            continue

        results.sort(key=lambda x: x["best_score"], reverse=True)

        # 記錄本輪策略
        current_strat = {r["symbol"]: r["best_strat"] for r in results}

        # 嘗試開倉（只取最好的幾個）
        opened = 0
        for r in results[:TOP_N]:
            sym_short = r['symbol'].replace('_USDT_PERP', '')

            # 安全過濾 1：策略方向必須連續 2 輪一致
            prev = prev_strat.get(r["symbol"])
            if prev is None or prev != r["best_strat"]:
                print(f"  {sym_short}: SKIP (策略不一致，上輪:{prev or '無'} 本輪:{r['best_strat']})")
                continue

            # 安全過濾 2：24h 跌幅超過 15% 不做追多
            if r["best_strat"] in ("追多", "抄底") and r.get("change24h", 0) < -15:
                print(f"  {sym_short}: SKIP (24h跌{r['change24h']:.1f}%，暴跌中不做多)")
                continue

            can_open, reason = risk_mgr.can_open_position(r)
            if not can_open:
                print(f"  {sym_short}: SKIP ({reason})")
                continue

            # 計算倉位
            size_usd, pct = risk_mgr.calc_position_size(r)
            if size_usd < 1:
                print(f"  {r['symbol']}: SKIP (倉位太小 {size_usd}U)")
                continue

            # 決定方向
            strat = r["best_strat"]
            if strat == "做空":
                side = "SELL"
            else:
                side = "BUY"

            # 計算數量
            price = r["price"]
            quantity = round(size_usd / price, 6) if price > 0 else 0
            if quantity <= 0:
                continue

            # 下單
            print(color(f"\n[OPEN] {r['symbol']} | {strat} ({side}) | "
                        f"{size_usd}U ({pct:.1f}%) | Score: {r['best_score']}", 'green'))
            print(f"   Price: {price} | TP: {r['tp']} | SL: {r['sl']} | RR: 1:{r['rr']}")

            order_result = client.place_order(r["symbol"], side, "MARKET", quantity)

            if order_result.get("result") or order_result.get("paper_mode"):
                pos = {
                    "id": f"pos_{int(time.time()*1000)}",
                    "symbol": r["symbol"],
                    "side": side,
                    "strategy": strat,
                    "entry_price": price,
                    "tp_price": r["tp"],
                    "sl_price": r["sl"],
                    "size": size_usd,
                    "quantity": quantity,
                    "score": r["best_score"],
                    "signal_strength": r["signal_strength"],
                    "opened_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                risk_mgr.record_open(pos)

                # 記錄學習預測
                learner.record_prediction(
                    symbol=r["symbol"], strategy=strat,
                    entry_price=price, tp_price=r["tp"], sl_price=r["sl"],
                    rate=r["best_rate"], score=r["best_score"],
                    regime=r["regime"],
                )
                opened += 1
            else:
                print(color(f"   ORDER FAILED: {order_result}", 'red'))

        if opened > 0:
            print(color(f"\n[INFO] 本輪開倉 {opened} 筆", 'green'))

        # 更新上輪策略記錄
        prev_strat = current_strat

        # 儲存
        save_trade_log(risk_mgr)
        learner.save()

        # 印出帳戶狀態
        status = risk_mgr.get_status()
        print(f"\n[STATUS] Balance: {status['balance']}U | "
              f"Open: {status['open_positions']} | "
              f"Today: {status['daily_pnl']:+.2f}U")

        time.sleep(INTERVAL)


def main():
    parser = argparse.ArgumentParser(description="自動交易機器人")
    parser.add_argument("--live", action="store_true", help="啟用真實交易（危險！）")
    parser.add_argument("--balance", type=float, default=100, help="初始資金（模擬模式，預設 100U）")
    parser.add_argument("--max-loss-pct", type=float, default=10, help="每日最大虧損百分比（預設 10%%）")
    parser.add_argument("--max-position-pct", type=float, default=20, help="單次最大倉位百分比（預設 20%%）")
    parser.add_argument("--max-positions", type=int, default=2, help="最大同時持倉數（預設 2）")
    parser.add_argument("--min-signal", choices=["STRONG", "MEDIUM", "WEAK"], default="STRONG",
                        help="最低信號強度（預設 STRONG）")
    args = parser.parse_args()

    print(color("=" * 62, 'cyan'))
    print(color(" 自動交易機器人 — Pionex API", 'cyan'))
    print(color("=" * 62, 'cyan'))

    # API 設定
    api_key = os.environ.get("PIONEX_API_KEY", "")
    api_secret = os.environ.get("PIONEX_API_SECRET", "")
    paper_mode = not args.live

    if args.live:
        if not api_key or not api_secret:
            print(color("[ERROR] 真實交易需要設定環境變數：", 'red'))
            print("  set PIONEX_API_KEY=your_key")
            print("  set PIONEX_API_SECRET=your_secret")
            sys.exit(1)

        print(color("\n  *** 警告：即將啟用真實交易 ***", 'red'))
        print(f"  初始資金將從帳戶餘額讀取")
        print(f"  每日最大虧損: {args.max_loss_pct}%")
        print(f"  單次最大倉位: {args.max_position_pct}%")
        confirm = input(color("\n  輸入 LIVE 確認啟動真實交易: ", 'red'))
        if confirm.strip() != "LIVE":
            print("已取消")
            sys.exit(0)
    else:
        print(f"\n  模式: {color('PAPER (模擬交易)', 'yellow')}")
        print(f"  初始資金: {args.balance}U")

    print(f"  每日最大虧損: {args.max_loss_pct}%")
    print(f"  單次最大倉位: {args.max_position_pct}%")
    print(f"  最大同時持倉: {args.max_positions}")
    print(f"  最低信號強度: {args.min_signal}")

    # 初始化
    client = PionexClient(api_key, api_secret, paper_mode=paper_mode)
    if paper_mode:
        client.paper_balance = args.balance

    risk_config = {
        "max_loss_pct": args.max_loss_pct,
        "max_position_pct": args.max_position_pct,
        "max_positions": args.max_positions,
        "max_consecutive_loss": 3,
        "min_signal_strength": args.min_signal,
        "min_score": 60,
        "min_win_rate": 55,
        "min_rr": 1.3,
    }

    balance = args.balance
    if not paper_mode:
        bal_result = client.get_balance()
        # 從真實帳戶取得 USDT 餘額
        if "data" in bal_result:
            for b in bal_result["data"].get("balances", []):
                if b.get("coin") == "USDT":
                    balance = float(b.get("free", 0))
                    break

    risk_mgr = RiskManager(balance, risk_config)
    learner = LearningEngine(LEARNING_FILE)

    print(color(f"\n  機器人啟動！按 Ctrl+C 停止\n", 'green'))

    try:
        run_trading_loop(client, risk_mgr, learner)
    except KeyboardInterrupt:
        print(color("\n\n 機器人已停止", 'yellow'))
        status = risk_mgr.get_status()
        print(f"\n  === 最終統計 ===")
        print(f"  餘額: {status['balance']}U")
        print(f"  總盈虧: {status['total_pnl']:+.2f}U ({status['total_pnl_pct']:+.1f}%)")
        print(f"  交易次數: {status['total_trades']}")
        print(f"  勝率: {status['win_rate']}%")
        save_trade_log(risk_mgr)
        learner.save()
        print(f"  交易記錄已儲存至: {LOG_FILE}")


if __name__ == "__main__":
    main()
