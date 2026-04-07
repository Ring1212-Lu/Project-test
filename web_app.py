#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
幣圈監控 v2 — Web 儀表板
========================
Flask 網頁版，瀏覽器即時查看監控結果。
背景執行掃描，前端自動刷新。
"""

import os
import json
import threading
import time
import traceback
import requests as req_lib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request

from crypto_monitor_v2 import (
    fetch_tickers, fetch_klines, fetch_and_analyze, get_btc_trend,
    fetch_trend_candidates, analyze_trend,
    TOP15_PCT, TOP15_MAX, TOP_N, MAX_WORKERS
)
from learning_engine import LearningEngine
from trading_bot import RiskManager, check_positions, save_trade_log, load_trade_log
from pionex_client import PionexClient

app = Flask(__name__, template_folder="templates", static_folder="static")

# === 掃描間隔設定 ===
SCAN_INTERVAL = 45       # 短線掃描間隔（秒），對齊 1M K 線閉合週期
TREND_EVERY_N = 15       # 每 N 輪短線後跑一次趨勢掃描（≈ 45*15 = 675 秒 ≈ 11 分鐘）
BOT_CHECK_INTERVAL = 30  # 交易 bot 讀取信號的間隔（秒）

# Same-coin cooldown after stop-loss
_symbol_cooldown = {}  # {symbol: timestamp_of_last_stoploss}
COOLDOWN_SECONDS = 3600  # 60 minutes

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LEARNING_FILE = os.path.join(BASE_DIR, "learning_data.json")
TRADE_LOG_FILE = os.path.join(BASE_DIR, "trade_log.json")

# 共用 LearningEngine（所有線程共用同一實例，避免檔案寫入競爭）
_shared_learner = None
_shared_learner_lock = threading.Lock()

def get_shared_learner():
    global _shared_learner
    with _shared_learner_lock:
        if _shared_learner is None:
            _shared_learner = LearningEngine(LEARNING_FILE)
        return _shared_learner

# 共享 ticker 快取（監控和機器人共用，減少 API 呼叫）
_ticker_cache = {"data": [], "time": 0}
_ticker_cache_lock = threading.Lock()
TICKER_CACHE_TTL = 30  # 快取 30 秒

def get_shared_tickers():
    """取得共享的 ticker 資料，避免重複呼叫 API"""
    with _ticker_cache_lock:
        now = time.time()
        if now - _ticker_cache["time"] < TICKER_CACHE_TTL and _ticker_cache["data"]:
            return _ticker_cache["data"]
    # 快取過期，重新抓
    tickers = fetch_tickers()
    with _ticker_cache_lock:
        _ticker_cache["data"] = tickers
        _ticker_cache["time"] = time.time()
    return tickers

# ===== Discord Webhook 通知 =====
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

def send_discord_message(message):
    """透過 Discord Webhook 發送訊息"""
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        req_lib.post(
            DISCORD_WEBHOOK_URL,
            json={"content": message},
            timeout=10,
        )
    except Exception:
        pass  # 通知失敗不影響主流程


def notify_strong_signals(results, tag="短線"):
    """掃描完成後，將 STRONG 訊號透過 Discord 通知"""
    strong = [r for r in results if r.get("signal_strength") == "STRONG"]
    if not strong:
        return
    lines = ["🔔 **{} 強訊號通知 ({} 個)**".format(tag, len(strong))]
    for r in strong[:5]:  # 最多通知 5 個
        sym = r["symbol"].replace("_USDT_PERP", "")
        hold = f"持倉 {r.get('hold_days', '?')} 天" if r.get("strategy_type") == "trend" else ""
        lines.append(
            f"```\n[{tag}] {sym} | {r['best_strat']} | "
            f"分數:{r['best_score']} 勝率:{r['best_rate']}%\n"
            f"價格:{r['price']}  TP:{r['tp']}  SL:{r['sl']}  {hold}\n```"
        )
    send_discord_message("\n".join(lines))


# 做空策略集合（用於判斷 side）
SELL_STRATEGIES = {"做空", "做空(寬)", "趨勢做空"}

# 全域狀態
state = {
    "round": 0,
    "status": "idle",
    "last_update": None,
    "top_results": [],
    "all_results": [],
    "trend_results": [],
    "trend_scan_time": "",
    "scan_timestamp": 0,          # scan 最後更新時間戳（用於過期檢測）
    "pool_size": 0,
    "total_perps": 0,
    "gainer_top": None,
    "loser_top": None,
    "learn_stats": {},
    "top_performers": [],
    "logs": [],
}
state_lock = threading.RLock()  # RLock: 允許同一執行緒重複取鎖，避免死鎖

# 交易機器人全域狀態
trading_state = {
    "enabled": False,
    "round": 0,
    "status": {},
    "open_positions": [],
    "closed_trades": [],
    "logs": [],
}
trading_lock = threading.RLock()  # RLock: 避免死鎖
trading_risk_mgr = None
trading_client = None

MAX_LOGS = 200


def add_log(msg):
    with state_lock:
        state["logs"].append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "msg": msg,
        })
        if len(state["logs"]) > MAX_LOGS:
            state["logs"] = state["logs"][-MAX_LOGS:]


def add_trading_log(msg):
    with trading_lock:
        trading_state["logs"].append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "msg": msg,
        })
        if len(trading_state["logs"]) > MAX_LOGS:
            trading_state["logs"] = trading_state["logs"][-MAX_LOGS:]


def _check_positions(risk_mgr, client):
    """Check positions with cooldown tracking for stop-losses"""
    # Snapshot open positions before check
    before_ids = {p["id"]: p for p in list(risk_mgr.open_positions)}
    n_positions = len(before_ids)
    check_positions(risk_mgr, client)
    # Detect closed positions
    after_ids = {p["id"] for p in risk_mgr.open_positions}
    for pid, pos in before_ids.items():
        if pid not in after_ids:
            # Position was closed — log details
            pnl = pos.get("pnl", 0)
            sym_short = pos.get("symbol", "").replace("_USDT_PERP", "")
            strat = pos.get("strategy", "")
            tag = "趨勢" if pos.get("strategy_type") == "trend" else "短線"
            pnl_str = f"+{pnl:.4f}" if pnl >= 0 else f"{pnl:.4f}"
            add_trading_log(f"[{tag}] CLOSE {sym_short} {strat} | PnL: {pnl_str}U | "
                            f"進場:{pos.get('entry_price',0)} 出場:{pos.get('closed_at','')}")
            if pnl < 0:
                _symbol_cooldown[pos["symbol"]] = time.time() + COOLDOWN_SECONDS
                add_trading_log(f"[COOLDOWN] {sym_short} 冷卻 {COOLDOWN_SECONDS}s（止損後）")


def run_position_checker(risk_mgr, client):
    """自適應間隔檢查持倉 TP/SL（有短線 30s，僅趨勢 300s，無倉 60s）"""
    while True:
        try:
            if risk_mgr.open_positions:
                _check_positions(risk_mgr, client)
                has_short = any(p.get("strategy_type") != "trend" for p in risk_mgr.open_positions)
                interval = 30 if has_short else 300
            else:
                interval = 60
        except Exception as e:
            print(f"[POS-CHECK] Error: {e}")
            interval = 30
        time.sleep(interval)


def run_trading_bot(initial_balance=100):
    """背景交易機器人執行緒"""
    global trading_risk_mgr, trading_client

    client = PionexClient(paper_mode=True)
    client.paper_balance = initial_balance

    risk_config = {
        "max_loss_pct": 10,
        "max_position_pct": 20,
        "max_positions": 2,
        "max_consecutive_loss": 3,
        "min_signal_strength": "MEDIUM",
        "min_score": 80,
        "min_win_rate": 55,
        "min_rr": 1.3,
    }
    risk_mgr = RiskManager(initial_balance, risk_config)
    learner = get_shared_learner()

    with trading_lock:
        trading_risk_mgr = risk_mgr
        trading_client = client
        trading_state["enabled"] = True

    # 恢復上次的持倉和餘額
    restored = load_trade_log(risk_mgr)
    if restored > 0:
        add_trading_log(f"從記錄恢復 {restored} 個未平倉位，餘額: {risk_mgr.current_balance:.2f}U")
    else:
        add_trading_log(f"機器人啟動 | 初始資金: {initial_balance}U (模擬模式)")

    # Start position checker thread (every 30 seconds)
    pos_checker = threading.Thread(target=run_position_checker, args=(risk_mgr, client), daemon=True)
    pos_checker.start()
    add_trading_log("持倉檢查執行緒已啟動（每 30 秒）")

    # 等待監控先跑一輪再開始讀取信號
    add_trading_log("等待監控掃描產生信號（15 秒後開始）...")
    time.sleep(15)
    round_num = 0
    trend_check_counter = 0  # 用於控制趨勢信號檢查頻率

    while True:
        round_num += 1
        trend_check_counter += 1
        with trading_lock:
            trading_state["round"] = round_num

        # 每輪檢查短線信號，每 TREND_BOT_EVERY 輪額外檢查趨勢信號
        TREND_BOT_EVERY = 4  # 每 4 輪（≈2 分鐘）檢查一次趨勢
        is_trend_check = (trend_check_counter % TREND_BOT_EVERY == 0)
        round_type = "短線+趨勢" if is_trend_check else "短線"
        add_trading_log(f"=== 交易第 {round_num} 輪 ({round_type}) ===")

        # 持倉由 position_checker 執行緒獨立檢查（每 30s/300s），
        # 避免與 bot 主迴圈雙重平倉導致餘額錯誤

        # === 從監控共享結果讀取信號（不再自己掃描，節省 API）===
        with state_lock:
            results = list(state.get("all_results", []))
            scan_ts = state.get("scan_timestamp", 0)
            btc_trend = state.get("btc_trend", 0)

        # 過期檢測：若 scan 超過 3 輪未更新，跳過本輪避免用陳舊信號開倉
        scan_age = time.time() - scan_ts if scan_ts > 0 else float('inf')
        if scan_age > SCAN_INTERVAL * 3:
            if scan_ts > 0:
                add_trading_log(f"[警告] 監控數據已過期 {int(scan_age)}s，跳過本輪")
            else:
                add_trading_log("等待監控產生第一輪數據...")
            time.sleep(BOT_CHECK_INTERVAL)
            continue

        btc_str = {1: "UP", -1: "DOWN", 0: "NEUTRAL"}.get(btc_trend, "?")
        add_trading_log(f"BTC: {btc_str} | 信號年齡: {int(scan_age)}s")

        # 排除已持倉的幣種
        held_symbols = {p["symbol"] for p in risk_mgr.open_positions}
        results = [r for r in results if r["symbol"] not in held_symbols]

        add_trading_log(f"[短線] 讀取監控信號 {len(results)} 個（排除已持倉 {len(held_symbols)}）")

        # === 趨勢信號（每 TREND_BOT_EVERY 輪檢查一次）===
        if is_trend_check:
            with state_lock:
                trend_results_bot = list(state.get("trend_results", []))

            # 排除已持倉幣種（防止同一信號重複開倉）
            trend_results_bot = [tr for tr in trend_results_bot if tr["symbol"] not in held_symbols]

            if trend_results_bot:
                add_trading_log(f"[趨勢] 檢查 {len(trend_results_bot)} 個趨勢訊號...")
                for tr in trend_results_bot[:3]:
                    sym_short = tr["symbol"].replace("_USDT_PERP", "")
                    add_trading_log(f"[TREND] 評估: {sym_short} {tr['best_strat']} "
                                    f"分數:{tr['best_score']} 勝率:{tr['best_rate']}% "
                                    f"強度:{tr.get('signal_strength','?')} R:R:{tr.get('rr',0)}")
                    cooldown_until = _symbol_cooldown.get(tr["symbol"], 0)
                    if time.time() < cooldown_until:
                        remaining = int(cooldown_until - time.time())
                        add_trading_log(f"[TREND] {sym_short}: SKIP (cooldown {remaining}s)")
                        continue
                    size_usd = round(risk_mgr.current_balance * 0.10, 2)
                    if size_usd < 1:
                        add_trading_log(f"[TREND] {sym_short}: SKIP (倉位太小 {size_usd}U)")
                        continue
                    strat = tr["best_strat"]
                    side = "SELL" if strat in SELL_STRATEGIES else "BUY"
                    price = tr["price"]
                    quantity = round(size_usd / price, 6) if price > 0 else 0
                    if quantity <= 0:
                        add_trading_log(f"[TREND] {sym_short}: SKIP (quantity=0, price={price})")
                        continue
                    add_trading_log(f"[TREND] {sym_short}: 嘗試開倉 {strat}({side}) "
                                    f"{size_usd}U qty={quantity} price={price}")
                    slippage = 0.001
                    entry_price = price * (1 + slippage) if side == "BUY" else price * (1 - slippage)
                    pos = {
                        "id": f"trend_{int(time.time()*1000)}",
                        "symbol": tr["symbol"],
                        "side": side,
                        "strategy": strat,
                        "strategy_type": "trend",
                        "entry_price": entry_price,
                        "tp_price": tr["tp"],
                        "sl_price": tr["sl"],
                        "size": size_usd,
                        "quantity": quantity,
                        "score": tr["best_score"],
                        "signal_strength": tr.get("signal_strength", "WEAK"),
                        "atr": tr.get("atr", 0),
                        "opened_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "trailing_sl": tr["sl"],
                    }
                    trend_signal = {**tr, "strategy_type": "trend"}
                    ok, reason = risk_mgr.try_open_position(trend_signal, pos)
                    if not ok:
                        add_trading_log(f"[TREND] 無法開倉: {reason}")
                        if "持倉數已達上限" in reason:
                            break
                        continue
                    order_result = client.place_order(tr["symbol"], side, "MARKET", quantity)
                    if order_result.get("result") or order_result.get("paper_mode"):
                        learner.record_prediction(
                            symbol=tr["symbol"], strategy=strat,
                            entry_price=price, tp_price=tr["tp"], sl_price=tr["sl"],
                            rate=tr["best_rate"], score=tr["best_score"],
                            regime=tr.get("regime", "unknown"),
                            ttl=72 * 3600,
                        )
                        add_trading_log(f"[TREND] OPEN {sym_short} {strat}({side}) "
                                        f"{size_usd}U | Score:{tr['best_score']} | TP:{tr['tp']} SL:{tr['sl']}")
                        break
                    else:
                        with risk_mgr._lock:
                            risk_mgr.open_positions = [p for p in risk_mgr.open_positions if p["id"] != pos["id"]]
                        add_trading_log(f"[TREND] ORDER FAILED: {tr['symbol']}")
            else:
                add_trading_log("[趨勢] 暫無趨勢訊號（等待監控掃描）")

        # === 短線開倉決策 ===
        if not results:
            add_trading_log("無有效短線信號，跳過")
            _update_trading_state(risk_mgr)
            save_trade_log(risk_mgr)
            time.sleep(BOT_CHECK_INTERVAL)
            continue

        results.sort(key=lambda x: x["best_score"], reverse=True)

        # 嘗試開倉
        opened = 0
        # 日誌：顯示所有候選訊號
        for r in results[:TOP_N]:
            sym_short = r['symbol'].replace('_USDT_PERP', '')
            add_trading_log(f"候選: {sym_short} {r['best_strat']} "
                           f"分數:{r['best_score']} 勝率:{r['best_rate']}% "
                           f"強度:{r['signal_strength']} 風報比:{r.get('rr',0)}")

        for r in results[:TOP_N]:
            sym_short = r['symbol'].replace('_USDT_PERP', '')

            # 安全過濾：24h 跌幅超過 15% 不做追多
            if r["best_strat"] in ("追多", "抄底") and r.get("change24h", 0) < -15:
                add_trading_log(f"{sym_short}: SKIP (24h跌{r['change24h']:.1f}%，暴跌中不做多)")
                continue

            # Same-coin cooldown check
            cooldown_until = _symbol_cooldown.get(r["symbol"], 0)
            if time.time() < cooldown_until:
                remaining = int(cooldown_until - time.time())
                add_trading_log(f"{sym_short}: SKIP (cooldown {remaining}s remaining)")
                continue

            size_usd, pct = risk_mgr.calc_position_size(r)
            if size_usd < 1:
                add_trading_log(f"{sym_short}: SKIP (倉位太小 {size_usd}U)")
                continue

            strat = r["best_strat"]
            side = "SELL" if strat in SELL_STRATEGIES else "BUY"
            price = r["price"]
            quantity = round(size_usd / price, 6) if price > 0 else 0
            if quantity <= 0:
                add_trading_log(f"{sym_short}: SKIP (quantity=0, price={price})")
                continue

            add_trading_log(f"{sym_short}: 嘗試開倉 {strat}({side}) {size_usd}U({pct}%) qty={quantity}")
            slippage = 0.001
            entry_price = price * (1 + slippage) if side == "BUY" else price * (1 - slippage)
            pos = {
                "id": f"pos_{int(time.time()*1000)}",
                "symbol": r["symbol"],
                "side": side,
                "strategy": strat,
                "entry_price": entry_price,
                "tp_price": r["tp"],
                "sl_price": r["sl"],
                "size": size_usd,
                "quantity": quantity,
                "score": r["best_score"],
                "signal_strength": r["signal_strength"],
                "atr": r.get("atr", 0),
                "opened_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            # 原子化 check + open
            ok, reason = risk_mgr.try_open_position(r, pos)
            if not ok:
                add_trading_log(f"{sym_short}: SKIP ({reason})")
                continue

            order_result = client.place_order(r["symbol"], side, "MARKET", quantity)

            if order_result.get("result") or order_result.get("paper_mode"):
                learner.record_prediction(
                    symbol=r["symbol"], strategy=strat,
                    entry_price=price, tp_price=r["tp"], sl_price=r["sl"],
                    rate=r["best_rate"], score=r["best_score"], regime=r["regime"],
                )
                opened += 1
                add_trading_log(f"OPEN {r['symbol'].replace('_USDT_PERP','')} {strat}({side}) "
                                f"{size_usd}U | Score:{r['best_score']} | TP:{r['tp']} SL:{r['sl']}")
            else:
                # 下單失敗，回滾已記錄的持倉
                with risk_mgr._lock:
                    risk_mgr.open_positions = [p for p in risk_mgr.open_positions if p["id"] != pos["id"]]
                add_trading_log(f"ORDER FAILED: {r['symbol']} - {order_result}")

        if opened > 0:
            add_trading_log(f"本輪開倉 {opened} 筆")


        _update_trading_state(risk_mgr)
        save_trade_log(risk_mgr)
        learner.save()

        status = risk_mgr.get_status()
        short_pos = sum(1 for p in risk_mgr.open_positions if p.get("strategy_type") != "trend")
        trend_pos = sum(1 for p in risk_mgr.open_positions if p.get("strategy_type") == "trend")
        add_trading_log(f"Balance: {status['balance']}U | "
                        f"持倉: 短線{short_pos}/趨勢{trend_pos} | "
                        f"Today: {status['daily_pnl']:+.2f}U | "
                        f"WR: {status['win_rate']}% ({status['total_trades']}筆)")

        time.sleep(BOT_CHECK_INTERVAL)


def _update_trading_state(risk_mgr):
    """將風控狀態同步到全域 trading_state"""
    with trading_lock:
        trading_state["status"] = risk_mgr.get_status()
        trading_state["open_positions"] = list(risk_mgr.open_positions)
        trading_state["closed_trades"] = list(risk_mgr.closed_trades[-50:])


# 監控掃描控制
scan_should_restart = threading.Event()

def run_background_scan():
    """背景掃描執行緒（帶錯誤保護）"""
    print("[SCAN] 背景掃描執行緒啟動")
    try:
        learner = get_shared_learner()
    except Exception as e:
        print(f"[SCAN] LearningEngine 初始化失敗: {e}")
        return
    round_num = 0
    consecutive_errors = 0

    while True:
        try:
            round_num += 1
            print(f"[SCAN] === 第 {round_num} 輪掃描開始 ===")
            with state_lock:
                state["round"] = round_num
                state["status"] = "scanning"

            add_log(f"=== 第 {round_num} 輪掃描開始 ===")

            # 驗證歷史預測
            validated = learner.validate_pending_predictions()
            if validated > 0:
                add_log(f"驗證了 {validated} 筆歷史預測，權重已更新")

            if round_num % 50 == 0:
                learner.cleanup_stale_weights()

            # 抓行情
            add_log("正在抓取全市場行情...")
            tickers = get_shared_tickers()
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

            print(f"[SCAN] tickers: {len(tickers)} 個, perps: {len(perps)} 個")
            if not perps:
                print("[SCAN] 無 perps，跳過本輪")
                add_log("無法取得行情資料")
                with state_lock:
                    state["status"] = "waiting"
                time.sleep(SCAN_INTERVAL)
                continue

            perps.sort(key=lambda x: x["change"], reverse=True)
            n15 = max(1, int(len(perps) * TOP15_PCT))
            gainers = perps[:n15][:TOP15_MAX]
            losers = list(reversed(perps[-n15:]))[:TOP15_MAX]
            pool = gainers + losers

            seen = set()
            unique_pool = []
            for coin in pool:
                if coin["symbol"] not in seen:
                    seen.add(coin["symbol"])
                    unique_pool.append(coin)
            pool = unique_pool

            with state_lock:
                state["total_perps"] = len(perps)
                state["pool_size"] = len(pool)
                state["gainer_top"] = gainers[0] if gainers else None
                state["loser_top"] = losers[0] if losers else None

            add_log(f"共 {len(perps)} 個合約，分析池 {len(pool)} 個")

            # BTC 大盤趨勢
            btc_trend = get_btc_trend()
            btc_str = {1: "UP", -1: "DOWN", 0: "NEUTRAL"}.get(btc_trend, "?")
            add_log(f"BTC 大盤趨勢: {btc_str}")

            # === 每輪都跑短線，每 TREND_EVERY_N 輪加跑趨勢 ===
            # 4H K 線閉合觸發：UTC 整點 (00/04/08/12/16/20) 後 3 分鐘內強制掃描（同一週期只觸發一次）
            utc_now = datetime.now(timezone.utc)
            current_4h_slot = utc_now.hour // 4  # 0-5，每 4 小時一個 slot
            is_4h_candle_close = (
                utc_now.hour % 4 == 0
                and utc_now.minute < 3
                and getattr(run_background_scan, '_last_4h_slot', -1) != current_4h_slot
            )
            if is_4h_candle_close:
                run_background_scan._last_4h_slot = current_4h_slot
                add_log(f"[趨勢] 4H K 線閉合觸發（UTC {utc_now.strftime('%H:%M')}）")
            is_trend_round = (round_num % TREND_EVERY_N == 0) or is_4h_candle_close

            # ── 短線���描（1M K線，每輪都跑）──
            add_log(f"[短線] 並行分析 {len(pool)} 個幣種（{MAX_WORKERS} 執行緒��...")
            t_start = time.time()
            results = []
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                future_map = {
                    executor.submit(fetch_and_analyze, coin, learner, None, btc_trend): coin
                    for coin in pool
                }
                for future in as_completed(future_map):
                    coin = future_map[future]
                    try:
                        res = future.result()
                        if res:
                            results.append(res)
                    except Exception:
                        pass
            elapsed = round(time.time() - t_start, 1)
            add_log(f"[短線] 分析完成，耗時 {elapsed} 秒")

            if results:
                results.sort(key=lambda x: x["best_score"], reverse=True)
                top = results[:TOP_N]

                # Discord 通知強訊號
                notify_strong_signals(top)

                # 記錄���測（嚴格版）
                for r in top:
                    learner.record_prediction(
                        symbol=r["symbol"],
                        strategy=r["best_strat"],
                        entry_price=r["price"],
                        tp_price=r["tp"],
                        sl_price=r["sl"],
                        rate=r["best_rate"],
                        score=r["best_score"],
                        regime=r["regime"],
                    )

                # 記錄寬鬆版預測（用於 A/B 比較）
                for r in results:
                    all_strats = r.get("all_strats", {})
                    for relaxed_name in ["做空(寬)", "抄底(寬)"]:
                        rs = all_strats.get(relaxed_name)
                        if rs and rs["total"] >= 3 and rs["score"] > 0:
                            base = relaxed_name.replace("(寬)", "").strip()
                            is_short = (base == "做空")
                            price = r["price"]
                            atr = r.get("atr", price * 0.02)
                            tp_mult = 2.0 if is_short else 2.5
                            sl_mult = 1.2 if is_short else 1.5
                            if is_short:
                                tp_p = round(price - atr * tp_mult, 6)
                                sl_p = round(price + atr * sl_mult, 6)
                            else:
                                tp_p = round(price + atr * tp_mult, 6)
                                sl_p = round(price - atr * sl_mult, 6)
                            learner.record_prediction(
                                symbol=r["symbol"],
                                strategy=relaxed_name,
                                entry_price=price,
                                tp_price=tp_p,
                                sl_price=sl_p,
                                rate=rs["rate"],
                                score=rs["score"],
                                regime=r["regime"],
                            )

                with state_lock:
                    state["top_results"] = top
                    state["all_results"] = results
                    state["scan_timestamp"] = time.time()
                    state["btc_trend"] = btc_trend  # 共享給 Bot，避免重複 API
            else:
                add_log("本輪無足夠樣本的幣種")
                with state_lock:
                    state["scan_timestamp"] = time.time()  # 即使無結果也更新時間戳
                    state["btc_trend"] = btc_trend

            # ── 趨勢掃描（4H 定向 + 1H 入場，每 TREND_EVERY_N 輪跑一次）──
            if is_trend_round:
                add_log(f"[趨勢] 開始趨勢掃描（4H定向+1H入場，每 {TREND_EVERY_N} 輪一次）...")
                try:
                    trend_candidates = fetch_trend_candidates(tickers)
                    trend_results = []
                    # Phase 1: 並行抓取 4H + 1H K線
                    klines_map = {}  # symbol -> {"4h": [...], "1h": [...]}
                    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                        futures_4h = {
                            executor.submit(fetch_klines, c["symbol"], "4h", 300): ("4h", c)
                            for c in trend_candidates
                        }
                        futures_1h = {
                            executor.submit(fetch_klines, c["symbol"], "1h", 100): ("1h", c)
                            for c in trend_candidates
                        }
                        for future in as_completed({**futures_4h, **futures_1h}):
                            if future in futures_4h:
                                tf, coin = futures_4h[future]
                            else:
                                tf, coin = futures_1h[future]
                            try:
                                kdata = future.result()
                                sym = coin["symbol"]
                                if sym not in klines_map:
                                    klines_map[sym] = {"coin": coin, "4h": None, "1h": None}
                                klines_map[sym][tf] = kdata
                            except Exception:
                                pass

                    # Phase 2: 分析（4H 定向 + 1H 入場確認）
                    for sym, data in klines_map.items():
                        klines_4h = data["4h"]
                        klines_1h = data["1h"]
                        coin = data["coin"]
                        if klines_4h and len(klines_4h) >= 60:
                            tres = analyze_trend(coin["symbol"], klines_4h, coin["change"],
                                                 learner, btc_trend, klines_1h=klines_1h)
                            if tres:
                                trend_results.append(tres)
                    trend_results.sort(key=lambda x: x["best_score"], reverse=True)
                    with state_lock:
                        state["trend_results"] = trend_results[:5]
                        state["trend_scan_time"] = datetime.now().strftime("%H:%M:%S")
                    add_log(f"[趨勢] 趨勢���描完成，{len(trend_results)} 個訊號，取前 {min(5, len(trend_results))} 個")

                    # Discord 通知趨勢強訊號
                    notify_strong_signals(trend_results[:5], tag="趨勢")

                    # 記錄趨勢預測
                    for r in trend_results[:3]:
                        learner.record_prediction(
                            symbol=r["symbol"], strategy=r["best_strat"],
                            entry_price=r["price"], tp_price=r["tp"], sl_price=r["sl"],
                            rate=r["best_rate"], score=r["best_score"],
                            regime=r.get("regime", "unknown"), ttl=72 * 3600,
                        )
                except Exception as e:
                    print(f"[TREND] Error: {e}")
                    add_log(f"[趨勢] 趨勢掃描出錯: {e}")

            # 學習統計
            stats = learner.data["stats"]
            decided = stats["total_wins"] + stats["total_losses"]
            overall_rate = round(stats["total_wins"] / decided * 100, 1) if decided > 0 else 0

            strat_stats = {}
            for strat in ["做空", "抄底", "追多", "做空(寬)", "抄底(寬)", "趨勢做多", "趨勢做空"]:
                s = stats["strategy_stats"].get(strat, {"wins": 0, "losses": 0, "expired": 0})
                sw, sl_count = s["wins"], s["losses"]
                st = sw + sl_count
                sr = round(sw / st * 100, 1) if st > 0 else 0
                strat_stats[strat] = {"wins": sw, "losses": sl_count, "rate": sr}

            # 更新全域狀態
            with state_lock:
                state["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                state["status"] = "waiting"
                state["learn_stats"] = {
                    "total_predictions": stats["total_predictions"],
                    "total_validations": stats["total_validations"],
                    "total_wins": stats["total_wins"],
                    "total_losses": stats["total_losses"],
                    "total_expired": stats["total_expired"],
                    "overall_rate": overall_rate,
                    "pending": len(learner.data["pending"]),
                    "weights_count": len(learner.data["weights"]),
                    "strategy_stats": strat_stats,
                }
                state["top_performers"] = learner.get_top_performers(5)

                short_count = len(state.get('all_results', []))
                if is_trend_round:
                    trend_count = len(state.get("trend_results", []))
                    add_log(f"第 {round_num} 輪完成（短線 {short_count} + 趨勢 {trend_count}）")
                else:
                    add_log(f"第 {round_num} 輪完成（短線 {short_count} 個有效幣種）")
                consecutive_errors = 0  # 重置錯誤計數

        except Exception as e:
            consecutive_errors += 1
            err_msg = f"掃描出錯: {type(e).__name__}: {e}"
            print(f"[SCAN ERROR] {err_msg}")
            traceback.print_exc()
            add_log(f"[ERROR] {err_msg}")

            if consecutive_errors >= 3:
                add_log(f"[ERROR] 連續 {consecutive_errors} 次錯誤，監控暫停。請點擊「重新啟動監控」按鈕。")
                with state_lock:
                    state["status"] = "error"
                # 等待手動重啟信號
                scan_should_restart.clear()
                scan_should_restart.wait()  # 阻塞直到按下重啟按鈕
                add_log("收到重啟信號，監控恢復！")
                consecutive_errors = 0
                continue

        time.sleep(SCAN_INTERVAL)


# ===== Flask Routes =====

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
  try:
    with state_lock:
        # 序列化 top_results
        top = []
        for r in state["top_results"]:
            try:
                top.append({
                    "symbol": r["symbol"],
                    "change24h": r["change24h"],
                    "price": r["price"],
                    "rsi": r.get("rsi", 0),
                    "mfi": r.get("mfi", 0),
                    "bb_pos": r.get("bb_pos", "N/A"),
                    "atr": r.get("atr", 0),
                    "regime": r.get("regime", "unknown"),
                    "obv_dir": r.get("obv_dir", 0),
                    "best_strat": r["best_strat"],
                    "best_rate": r["best_rate"],
                    "best_total": r["best_total"],
                    "best_score": r["best_score"],
                    "confidence": r.get("confidence", "N/A"),
                    "weight": r.get("weight", 1.0),
                    "regime_bonus": r.get("regime_bonus", 1.0),
                    "obv_bonus": r.get("obv_bonus", 1.0),
                    "recent_bonus": r.get("recent_bonus", 1.0),
                    "signal_strength": r.get("signal_strength", "WEAK"),
                    "detail": r.get("detail", ""),
                    "relaxed_detail": r.get("relaxed_detail", ""),
                    "env_multiplier": r.get("env_multiplier", 1.0),
                    "global_penalty": r.get("global_penalty", 1.0),
                    "tp": r.get("tp", 0),
                    "sl": r.get("sl", 0),
                    "rr": r.get("rr", 0),
                    "tp_pct": r.get("tp_pct", 0),
                    "sl_pct": r.get("sl_pct", 0),
                })
            except Exception as e:
                print(f"[API] 序列化結果出錯: {e}, keys={list(r.keys())}")

        # 序列化 trend_results
        trend = []
        for r in state.get("trend_results", []):
            try:
                trend.append({
                    "symbol": r["symbol"],
                    "change24h": r["change24h"],
                    "price": r["price"],
                    "rsi": r.get("rsi", 0),
                    "atr": r.get("atr", 0),
                    "regime": r.get("regime", "unknown"),
                    "obv_dir": r.get("obv_dir", 0),
                    "ema20": r.get("ema20", 0),
                    "ema50": r.get("ema50", 0),
                    "ema_status": r.get("ema_status", ""),
                    "vol_above_avg": r.get("vol_above_avg", False),
                    "best_strat": r["best_strat"],
                    "best_rate": r["best_rate"],
                    "best_total": r["best_total"],
                    "best_score": r["best_score"],
                    "signal_strength": r.get("signal_strength", "WEAK"),
                    "tp": r.get("tp", 0),
                    "sl": r.get("sl", 0),
                    "rr": r.get("rr", 0),
                    "tp_pct": r.get("tp_pct", 0),
                    "sl_pct": r.get("sl_pct", 0),
                    "strategy_type": r.get("strategy_type", "trend"),
                    "hold_days": r.get("hold_days", 3),
                })
            except Exception as e:
                print(f"[API] 序列化趨勢結果出錯: {e}")

        return jsonify({
            "round": state["round"],
            "status": state["status"],
            "last_update": state["last_update"],
            "top_results": top,
            "trend_results": trend,
            "trend_scan_time": state.get("trend_scan_time", ""),
            "all_count": len(state["all_results"]),
            "pool_size": state["pool_size"],
            "total_perps": state["total_perps"],
            "gainer_top": state["gainer_top"],
            "loser_top": state["loser_top"],
            "learn_stats": state["learn_stats"],
            "top_performers": state["top_performers"],
            "logs": state["logs"][-50:],
            "interval": SCAN_INTERVAL,
        })
  except Exception as e:
    print(f"[API STATE ERROR] {e}")
    traceback.print_exc()
    return jsonify({"error": str(e), "round": 0, "status": "error", "top_results": [], "all_count": 0,
                     "pool_size": 0, "total_perps": 0, "gainer_top": None, "loser_top": None,
                     "learn_stats": {}, "top_performers": [], "logs": [], "interval": SCAN_INTERVAL})


@app.route("/api/backtest/<symbol>")
def api_backtest(symbol):
    """即時回測單一幣種"""
    from backtest import fetch_klines_backtest, run_backtest, analyze_trades
    klines = fetch_klines_backtest(symbol, limit=300)
    if not klines:
        return jsonify({"error": "No data"}), 404
    trades = run_backtest(symbol, klines)
    if not trades:
        return jsonify({"error": "Insufficient trades"}), 404
    results = analyze_trades(trades)
    # Convert int keys to str for JSON
    serializable = {}
    for hp, strats in results.items():
        serializable[str(hp)] = {}
        for strat, r in strats.items():
            serializable[str(hp)][strat] = r
    return jsonify({"symbol": symbol, "results": serializable})


@app.route("/api/learning")
def api_learning():
    """查看完整學習資料"""
    if os.path.exists(LEARNING_FILE):
        with open(LEARNING_FILE, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    return jsonify({"error": "No learning data yet"}), 404


@app.route("/api/trading")
def api_trading():
    """交易機器人狀態 API"""
    with trading_lock:
        if not trading_state["enabled"]:
            # 嘗試從 trade_log.json 讀取
            if os.path.exists(TRADE_LOG_FILE):
                try:
                    with open(TRADE_LOG_FILE, 'r', encoding='utf-8') as f:
                        log_data = json.load(f)
                    return jsonify({
                        "enabled": False,
                        "source": "file",
                        "last_updated": log_data.get("last_updated"),
                        "status": log_data.get("status", {}),
                        "open_positions": log_data.get("open_positions", []),
                        "closed_trades": log_data.get("closed_trades", [])[-30:],
                        "logs": [],
                    })
                except (json.JSONDecodeError, IOError):
                    pass
            return jsonify({"enabled": False, "status": {}, "open_positions": [], "closed_trades": [], "logs": []})

        return jsonify({
            "enabled": True,
            "source": "live",
            "round": trading_state["round"],
            "status": trading_state["status"],
            "open_positions": trading_state["open_positions"],
            "closed_trades": trading_state["closed_trades"],
            "logs": trading_state["logs"][-50:],
        })


@app.route("/api/trading/start", methods=["POST"])
def api_trading_start():
    """啟動模擬交易機器人"""
    with trading_lock:
        if trading_state["enabled"]:
            return jsonify({"error": "Bot already running"}), 400

    balance = 100
    try:
        data = request.get_json(silent=True)
        if data and "balance" in data:
            balance = float(data["balance"])
    except (ValueError, TypeError):
        pass

    bot_thread = threading.Thread(target=run_trading_bot, args=(balance,), daemon=True)
    bot_thread.start()
    return jsonify({"result": True, "balance": balance, "msg": f"Paper trading bot started with {balance}U"})


@app.route("/api/trading/reset", methods=["POST"])
def api_trading_reset():
    """重置停機狀態，讓機器人繼續運行"""
    global trading_risk_mgr
    with trading_lock:
        if not trading_state["enabled"]:
            return jsonify({"error": "Bot not running"}), 400
        if trading_risk_mgr:
            trading_risk_mgr.halted = False
            trading_risk_mgr.halt_reason = ""
            trading_risk_mgr.consecutive_losses = 0
            add_trading_log("手動重置停機狀態，機器人繼續運行")
            _update_trading_state(trading_risk_mgr)
            return jsonify({"result": True, "msg": "Bot reset, resuming trading"})
    return jsonify({"error": "No risk manager found"}), 500


@app.route("/api/discord/setup", methods=["POST"])
def api_discord_setup():
    """設定 Discord Webhook 並發送測試訊息"""
    global DISCORD_WEBHOOK_URL
    data = request.get_json(silent=True) or {}
    webhook_url = data.get("webhook_url", "").strip()
    if not webhook_url:
        return jsonify({"error": "請提供 Discord Webhook URL"}), 400
    # 發送測試訊息
    try:
        resp = req_lib.post(
            webhook_url,
            json={"content": "✅ 幣圈監控 Discord 通知已連接成功！"},
            timeout=10,
        )
        if resp.status_code == 204:
            DISCORD_WEBHOOK_URL = webhook_url
            return jsonify({"result": True, "msg": "Discord 通知設定成功，已發送測試訊息"})
        else:
            return jsonify({"error": f"Webhook 無效 (HTTP {resp.status_code})"}), 400
    except Exception as e:
        return jsonify({"error": f"連線失敗: {e}"}), 500


@app.route("/api/discord/status")
def api_discord_status():
    """查詢 Discord Webhook 是否已設定"""
    return jsonify({"enabled": bool(DISCORD_WEBHOOK_URL)})


@app.route("/api/scan/restart", methods=["POST"])
def api_scan_restart():
    """手動重啟監控掃描"""
    with state_lock:
        current = state["status"]
    scan_should_restart.set()
    add_log("收到手動重啟指令")
    return jsonify({"result": True, "msg": "Scan restart signal sent", "prev_status": current})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="幣圈監控 Web 儀表板")
    parser.add_argument("--bot", action="store_true", help="同時啟動模擬交易機器人")
    parser.add_argument("--balance", type=float, default=100, help="機器人初始資金（預設 100U）")
    args = parser.parse_args()

    # 啟動背景掃描
    scan_thread = threading.Thread(target=run_background_scan, daemon=True)
    scan_thread.start()

    # 選擇性啟動交易機器人
    if args.bot:
        bot_thread = threading.Thread(target=run_trading_bot, args=(args.balance,), daemon=True)
        bot_thread.start()
        print(f" 模擬交易機器人已啟動（初始資金: {args.balance}U）")

    print("=" * 50)
    print(" 幣圈監控 Web 儀表板啟動")
    print(" 開啟瀏覽器前往: http://localhost:5000")
    if args.bot:
        print(" 模擬交易機器人: 已啟動")
    else:
        print(" 模擬交易機器人: 未啟動（可在面板中啟動，或加 --bot 參數）")
    print("=" * 50)

    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
