#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回測驗證工具 — 驗證策略真實表現
================================
功能：
1. 抓取多個幣種的歷史 K 線，執行完整回測
2. 測試不同持倉時間（5/10/15/20 根 K 線）找最佳出場時機
3. 分析各策略在不同市場狀態下的勝率
4. 測試不同 RSI 閾值找最佳參數
5. 計算夏普比率、最大回撤、期望報酬等關鍵指標
6. 輸出詳細報告 + 優化建議

用法：
    python backtest.py
    python backtest.py --symbols BTC_USDT_PERP ETH_USDT_PERP
    python backtest.py --top 30
"""

import requests
import time
import argparse
import json
import os
from datetime import datetime
from statistics import mean, stdev
from collections import defaultdict
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from crypto_monitor_v2 import (
    calc_rsi_wilder, calc_mfi_optimized, calc_macd_hist,
    calc_atr, calc_bollinger, calc_obv, obv_trend,
    BASE_URL, TICK_URL,
    _pionex_to_binance_symbol, _binance_to_pionex_symbol,
)
from learning_engine import MarketRegime


# ===== Session =====
def _create_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    retry = Retry(total=3, backoff_factor=1, allowed_methods=["GET"])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=5, pool_maxsize=5)
    session.mount("https://", adapter)
    return session

_session = _create_session()


def fetch_klines_backtest(symbol, limit=300):
    """從幣安合約 API 抓取較長歷史 K 線用於回測"""
    binance_sym = _pionex_to_binance_symbol(symbol)
    try:
        r = _session.get(BASE_URL, params={
            "symbol": binance_sym, "interval": "1m", "limit": limit
        }, timeout=15)
        if r.status_code == 200:
            raw = r.json()
            if isinstance(raw, list) and len(raw) >= 100:
                klines = []
                for k in raw:
                    klines.append({
                        "open": k[1], "high": k[2], "low": k[3],
                        "close": k[4], "volume": k[5],
                    })
                return klines
    except Exception as e:
        print(f"  [WARN] {symbol}: {type(e).__name__}")
    return []


def get_top_symbols(n=20):
    """取得成交量前 N 的永續合約"""
    try:
        r = _session.get(TICK_URL, timeout=15)
        if r.status_code == 200:
            data = r.json()
            perps = []
            for t in data:
                sym = t.get("symbol", "")
                if not sym.endswith("USDT"):
                    continue
                try:
                    vol = float(t.get("quoteVolume", 0))
                    if vol > 10000:
                        perps.append({"symbol": _binance_to_pionex_symbol(sym), "volume": vol})
                except (ValueError, TypeError):
                    continue
            perps.sort(key=lambda x: x["volume"], reverse=True)
            return [p["symbol"] for p in perps[:n]]
    except Exception:
        pass
    return []


# ===== 回測核心 =====

def run_backtest(symbol, klines, hold_periods=None, rsi_short_thresh=70, rsi_long_thresh=30):
    """
    對單一幣種執行回測。
    hold_periods: 測試不同持倉期（K 線根數）
    """
    if hold_periods is None:
        hold_periods = [3, 5, 8, 10, 15, 20]

    closes = [float(k['close']) for k in klines if float(k.get('close', 0)) > 0]
    volumes = []
    for k in klines:
        try:
            volumes.append(float(k.get('volume', 0)))
        except (ValueError, TypeError):
            volumes.append(0)

    if len(closes) < 100:
        return None

    rsi_vals   = calc_rsi_wilder(closes)
    mfi_vals   = calc_mfi_optimized(klines)
    macd_hist  = calc_macd_hist(closes)
    atr_vals   = calc_atr(klines)
    bb_mid, bb_upper, bb_lower = calc_bollinger(closes)
    obv_vals   = calc_obv(closes, volumes[:len(closes)])

    if not rsi_vals or not mfi_vals or not macd_hist:
        return None

    RSI_OFF = 15
    length = min(len(rsi_vals), len(mfi_vals))
    max_hold = max(hold_periods)

    # 記錄每筆交易
    trades = {hp: {"做空": [], "抄底": [], "追多": [], "做空(寬)": [], "抄底(寬)": []} for hp in hold_periods}

    for i in range(6, length - max_hold):
        ci = i + RSI_OFF
        if ci + max_hold >= len(closes) or ci < 2:
            continue
        if ci >= len(macd_hist) or ci - 1 < 0:
            continue

        price     = closes[ci]
        rsi_prev  = rsi_vals[i - 1] if i - 1 >= 0 else 50
        rsi_cur   = rsi_vals[i]
        mfi_cur   = mfi_vals[i] if i < len(mfi_vals) else 50
        macd_down = macd_hist[ci] < macd_hist[ci - 1]
        macd_up   = macd_hist[ci] > macd_hist[ci - 1]

        # 市場狀態（用最近 50 根）
        start_idx = max(0, ci - 50)
        regime = MarketRegime.detect(closes[start_idx:ci + 1])

        # BB 位置
        bb_idx = ci - 19  # bollinger offset
        bb_position = "mid"
        if 0 <= bb_idx < len(bb_upper):
            if price >= bb_upper[bb_idx]:
                bb_position = "above_upper"
            elif price <= bb_lower[bb_idx]:
                bb_position = "below_lower"

        # OBV
        obv_d = 0
        if ci >= 10 and ci < len(obv_vals):
            obv_d = 1 if obv_vals[ci] > obv_vals[ci - 10] else -1

        # 做空（嚴格版）
        if rsi_prev > rsi_short_thresh and rsi_cur < rsi_prev and macd_down and rsi_cur > 30:
            for hp in hold_periods:
                exit_price = closes[ci + hp]
                pnl_pct = (price - exit_price) / price * 100  # 做空盈虧
                trades[hp]["做空"].append({
                    "entry": price, "exit": exit_price, "pnl_pct": pnl_pct,
                    "rsi": rsi_cur, "mfi": mfi_cur, "regime": regime,
                    "bb": bb_position, "obv": obv_d,
                })

        # 做空（寬鬆版：RSI 門檻 -5，去掉 rsi>30 限制）
        if rsi_prev > (rsi_short_thresh - 5) and rsi_cur < rsi_prev and macd_down:
            for hp in hold_periods:
                exit_price = closes[ci + hp]
                pnl_pct = (price - exit_price) / price * 100
                trades[hp]["做空(寬)"].append({
                    "entry": price, "exit": exit_price, "pnl_pct": pnl_pct,
                    "rsi": rsi_cur, "mfi": mfi_cur, "regime": regime,
                    "bb": bb_position, "obv": obv_d,
                })

        # 抄底（嚴格版）
        if rsi_prev < rsi_long_thresh and rsi_cur > rsi_prev and mfi_cur < 25 and macd_up:
            for hp in hold_periods:
                exit_price = closes[ci + hp]
                pnl_pct = (exit_price - price) / price * 100
                trades[hp]["抄底"].append({
                    "entry": price, "exit": exit_price, "pnl_pct": pnl_pct,
                    "rsi": rsi_cur, "mfi": mfi_cur, "regime": regime,
                    "bb": bb_position, "obv": obv_d,
                })

        # 抄底（寬鬆版：MFI < 35，RSI 門檻 +5）
        if rsi_prev < (rsi_long_thresh + 5) and rsi_cur > rsi_prev and mfi_cur < 35 and macd_up:
            for hp in hold_periods:
                exit_price = closes[ci + hp]
                pnl_pct = (exit_price - price) / price * 100
                trades[hp]["抄底(寬)"].append({
                    "entry": price, "exit": exit_price, "pnl_pct": pnl_pct,
                    "rsi": rsi_cur, "mfi": mfi_cur, "regime": regime,
                    "bb": bb_position, "obv": obv_d,
                })

        # 追多（加嚴：需 OBV 上升 + MFI > 40 確認買壓）
        obv_rising = (ci >= 5 and ci < len(obv_vals) and
                      obv_vals[ci] > obv_vals[ci - 5])
        if (closes[ci] > closes[ci - 1] > closes[ci - 2]
                and 60 < rsi_cur < 80 and macd_up
                and mfi_cur > 40 and obv_rising):
            for hp in hold_periods:
                exit_price = closes[ci + hp]
                pnl_pct = (exit_price - price) / price * 100
                trades[hp]["追多"].append({
                    "entry": price, "exit": exit_price, "pnl_pct": pnl_pct,
                    "rsi": rsi_cur, "mfi": mfi_cur, "regime": regime,
                    "bb": bb_position, "obv": obv_d,
                })

    return trades


def analyze_trades(all_trades):
    """分析交易結果"""
    results = {}

    for hp, strats in all_trades.items():
        results[hp] = {}
        for strat, trades_list in strats.items():
            if not trades_list:
                results[hp][strat] = None
                continue

            wins = [t for t in trades_list if t["pnl_pct"] > 0]
            losses = [t for t in trades_list if t["pnl_pct"] <= 0]
            pnls = [t["pnl_pct"] for t in trades_list]

            win_rate = len(wins) / len(trades_list) * 100
            avg_pnl = mean(pnls)
            avg_win = mean([t["pnl_pct"] for t in wins]) if wins else 0
            avg_loss = mean([t["pnl_pct"] for t in losses]) if losses else 0

            # 夏普比率（簡化版）
            sharpe = (avg_pnl / stdev(pnls)) if len(pnls) > 1 and stdev(pnls) > 0 else 0

            # 期望報酬
            expectancy = (win_rate / 100 * avg_win) + ((100 - win_rate) / 100 * avg_loss)

            # 按 regime 分組
            regime_stats = defaultdict(lambda: {"total": 0, "wins": 0, "pnls": []})
            for t in trades_list:
                r = t["regime"]
                regime_stats[r]["total"] += 1
                regime_stats[r]["pnls"].append(t["pnl_pct"])
                if t["pnl_pct"] > 0:
                    regime_stats[r]["wins"] += 1

            # 按 OBV 分組
            obv_stats = defaultdict(lambda: {"total": 0, "wins": 0})
            base_strat = strat.replace("(寬)", "").strip() if "(寬)" in strat else strat
            for t in trades_list:
                key = "confirmed" if (
                    (base_strat == "做空" and t["obv"] == -1) or
                    (base_strat in ("抄底", "追多") and t["obv"] == 1)
                ) else "divergent"
                obv_stats[key]["total"] += 1
                if t["pnl_pct"] > 0:
                    obv_stats[key]["wins"] += 1

            # 按 BB 分組
            bb_stats = defaultdict(lambda: {"total": 0, "wins": 0})
            for t in trades_list:
                bb_stats[t["bb"]]["total"] += 1
                if t["pnl_pct"] > 0:
                    bb_stats[t["bb"]]["wins"] += 1

            # 最大連續虧損
            max_consecutive_loss = 0
            current_loss_streak = 0
            for t in trades_list:
                if t["pnl_pct"] <= 0:
                    current_loss_streak += 1
                    max_consecutive_loss = max(max_consecutive_loss, current_loss_streak)
                else:
                    current_loss_streak = 0

            # 最大回撤
            cumulative = 0
            peak = 0
            max_drawdown = 0
            for t in trades_list:
                cumulative += t["pnl_pct"]
                peak = max(peak, cumulative)
                drawdown = peak - cumulative
                max_drawdown = max(max_drawdown, drawdown)

            results[hp][strat] = {
                "total": len(trades_list),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(win_rate, 1),
                "avg_pnl": round(avg_pnl, 4),
                "avg_win": round(avg_win, 4),
                "avg_loss": round(avg_loss, 4),
                "sharpe": round(sharpe, 3),
                "expectancy": round(expectancy, 4),
                "max_consecutive_loss": max_consecutive_loss,
                "max_drawdown": round(max_drawdown, 2),
                "regime_stats": {
                    r: {
                        "total": v["total"],
                        "win_rate": round(v["wins"] / v["total"] * 100, 1) if v["total"] > 0 else 0,
                        "avg_pnl": round(mean(v["pnls"]), 4) if v["pnls"] else 0,
                    } for r, v in regime_stats.items()
                },
                "obv_stats": {
                    k: {
                        "total": v["total"],
                        "win_rate": round(v["wins"] / v["total"] * 100, 1) if v["total"] > 0 else 0,
                    } for k, v in obv_stats.items()
                },
                "bb_stats": {
                    k: {
                        "total": v["total"],
                        "win_rate": round(v["wins"] / v["total"] * 100, 1) if v["total"] > 0 else 0,
                    } for k, v in bb_stats.items()
                },
            }

    return results


def test_rsi_thresholds(symbol, klines):
    """測試不同 RSI 閾值的勝率"""
    results = []
    for short_th in [65, 70, 75, 80]:
        for long_th in [20, 25, 30, 35]:
            trades = run_backtest(symbol, klines, hold_periods=[5],
                                 rsi_short_thresh=short_th, rsi_long_thresh=long_th)
            if not trades:
                continue
            for strat in ["做空", "抄底", "追多"]:
                t_list = trades[5][strat]
                if len(t_list) >= 5:
                    wins = sum(1 for t in t_list if t["pnl_pct"] > 0)
                    wr = round(wins / len(t_list) * 100, 1)
                    avg = round(mean([t["pnl_pct"] for t in t_list]), 4)
                    results.append({
                        "strat": strat,
                        "rsi_short": short_th,
                        "rsi_long": long_th,
                        "trades": len(t_list),
                        "win_rate": wr,
                        "avg_pnl": avg,
                    })
    return results


# ===== 輸出報告 =====

def print_report(symbol, results, rsi_results=None):
    """印出單一幣種的回測報告"""
    print(f"\n{'='*70}")
    print(f"  {symbol} 回測報告")
    print(f"{'='*70}")

    # 找出最佳持倉期
    print(f"\n  --- 各持倉期勝率比較 ---")
    print(f"  {'持倉期':>6} | {'做空':>18} | {'抄底':>18} | {'追多':>18}")
    print(f"  {'-'*6}-+-{'-'*18}-+-{'-'*18}-+-{'-'*18}")

    for hp in sorted(results.keys()):
        parts = []
        for strat in ["做空", "抄底", "追多"]:
            r = results[hp][strat]
            if r and r["total"] >= 3:
                parts.append(f"{r['win_rate']:5.1f}% ({r['total']:3d}x)")
            else:
                parts.append(f"{'N/A':>14}")
        print(f"  {hp:>4} 根 | {parts[0]} | {parts[1]} | {parts[2]}")

    # 最佳組合
    best_score = -999
    best_combo = None
    for hp in results:
        for strat in ["做空", "抄底", "追多"]:
            r = results[hp][strat]
            if r and r["total"] >= 5:
                score = r["expectancy"] * min(r["total"] / 10, 1.5)
                if score > best_score:
                    best_score = score
                    best_combo = (hp, strat, r)

    if best_combo:
        hp, strat, r = best_combo
        print(f"\n  >>> 最佳組合：{strat} 持倉 {hp} 根")
        print(f"      勝率: {r['win_rate']}%  |  交易數: {r['total']}")
        print(f"      平均盈虧: {r['avg_pnl']:.4f}%  |  平均獲利: {r['avg_win']:.4f}%  |  平均虧損: {r['avg_loss']:.4f}%")
        print(f"      夏普比率: {r['sharpe']:.3f}  |  期望報酬: {r['expectancy']:.4f}%")
        print(f"      最大連虧: {r['max_consecutive_loss']} 次  |  最大回撤: {r['max_drawdown']:.2f}%")

        # Regime 分析
        if r["regime_stats"]:
            print(f"\n      市場狀態分析:")
            for regime, rs in r["regime_stats"].items():
                if rs["total"] >= 2:
                    print(f"        {regime:20s}: 勝率 {rs['win_rate']:5.1f}% ({rs['total']:3d}次) 平均 {rs['avg_pnl']:+.4f}%")

        # OBV 確認分析
        if r["obv_stats"]:
            print(f"\n      OBV 量能確認:")
            for key, os_val in r["obv_stats"].items():
                if os_val["total"] >= 2:
                    label = "量能確認" if key == "confirmed" else "量價背離"
                    print(f"        {label}: 勝率 {os_val['win_rate']:5.1f}% ({os_val['total']}次)")

        # BB 分析
        if r["bb_stats"]:
            print(f"\n      布林帶位置:")
            for key, bs in r["bb_stats"].items():
                if bs["total"] >= 2:
                    print(f"        {key:15s}: 勝率 {bs['win_rate']:5.1f}% ({bs['total']}次)")

    # RSI 閾值測試
    if rsi_results:
        print(f"\n  --- RSI 閾值優化（持倉 5 根）---")
        # 按策略分組，取勝率最高的
        for strat in ["做空", "抄底", "追多"]:
            strat_results = [r for r in rsi_results if r["strat"] == strat and r["trades"] >= 5]
            if not strat_results:
                continue
            strat_results.sort(key=lambda x: x["win_rate"], reverse=True)
            print(f"\n      {strat} 最佳 RSI 參數（前 3）:")
            for r in strat_results[:3]:
                print(f"        RSI 做空>{r['rsi_short']} 抄底<{r['rsi_long']}: "
                      f"勝率 {r['win_rate']}% ({r['trades']}次) 平均 {r['avg_pnl']:+.4f}%")


def print_summary(all_symbol_results):
    """印出全幣種總結"""
    print(f"\n{'='*70}")
    print(f"  全幣種綜合分析")
    print(f"{'='*70}")

    # 匯總所有交易
    totals = defaultdict(lambda: defaultdict(lambda: {"wins": 0, "total": 0, "pnls": []}))

    for sym, results in all_symbol_results.items():
        for hp, strats in results.items():
            for strat, r in strats.items():
                if r and r["total"] >= 3:
                    totals[hp][strat]["wins"] += r["wins"]
                    totals[hp][strat]["total"] += r["total"]

    print(f"\n  --- 全幣種匯總勝率 ---")
    print(f"  {'持倉期':>6} | {'做空':>18} | {'抄底':>18} | {'追多':>18}")
    print(f"  {'-'*6}-+-{'-'*18}-+-{'-'*18}-+-{'-'*18}")

    for hp in sorted(totals.keys()):
        parts = []
        for strat in ["做空", "抄底", "追多"]:
            d = totals[hp][strat]
            if d["total"] >= 5:
                wr = round(d["wins"] / d["total"] * 100, 1)
                parts.append(f"{wr:5.1f}% ({d['total']:3d}x)")
            else:
                parts.append(f"{'N/A':>14}")
        print(f"  {hp:>4} 根 | {parts[0]} | {parts[1]} | {parts[2]}")

    # 找全域最佳
    best = None
    best_wr = 0
    for hp in totals:
        for strat in ["做空", "抄底", "追多"]:
            d = totals[hp][strat]
            if d["total"] >= 10:
                wr = d["wins"] / d["total"] * 100
                if wr > best_wr:
                    best_wr = wr
                    best = (hp, strat, d["total"])

    if best:
        print(f"\n  >>> 全域最佳：{best[1]} 持倉 {best[0]} 根 — 勝率 {best_wr:.1f}% ({best[2]} 次)")

    # 優化建議
    print(f"\n{'='*70}")
    print(f"  優化建議")
    print(f"{'='*70}")

    suggestions = []

    # 檢查各策略勝率
    for strat in ["做空", "抄底", "追多"]:
        d5 = totals.get(5, {}).get(strat, {"wins": 0, "total": 0})
        d10 = totals.get(10, {}).get(strat, {"wins": 0, "total": 0})
        if d5["total"] >= 5:
            wr5 = d5["wins"] / d5["total"] * 100
            if wr5 < 50:
                suggestions.append(f"  - {strat} 持倉 5 根勝率僅 {wr5:.1f}%，建議加嚴進場條件或延長持倉期")
            if d10["total"] >= 5:
                wr10 = d10["wins"] / d10["total"] * 100
                if wr10 > wr5 + 5:
                    suggestions.append(f"  - {strat} 持倉 10 根 ({wr10:.1f}%) 明顯優於 5 根 ({wr5:.1f}%)，建議延長持倉期")

    if not suggestions:
        suggestions.append("  - 目前策略表現良好，建議持續觀察累積更多數據")

    for s in suggestions:
        print(s)

    print()


# ===== 主程式 =====

def main():
    parser = argparse.ArgumentParser(description="幣圈監控回測驗證工具")
    parser.add_argument("--symbols", nargs="+", help="指定幣種（如 BTC_USDT_PERP ETH_USDT_PERP）")
    parser.add_argument("--top", type=int, default=10, help="自動選取成交量前 N 的幣種（預設 10）")
    parser.add_argument("--limit", type=int, default=300, help="K 線數量（預設 300）")
    parser.add_argument("--rsi-test", action="store_true", help="執行 RSI 閾值優化測試")
    parser.add_argument("--save", type=str, help="儲存結果到 JSON 檔案")
    args = parser.parse_args()

    print("=" * 70)
    print("  幣圈監控 — 回測驗證工具")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 取得幣種列表
    if args.symbols:
        symbols = args.symbols
    else:
        print(f"\n  正在取得成交量前 {args.top} 的幣種...")
        symbols = get_top_symbols(args.top)
        if not symbols:
            print("  [ERROR] 無法取得幣種列表")
            return

    print(f"  待測試幣種: {len(symbols)} 個")
    print(f"  K 線數量: {args.limit}")
    print(f"  RSI 閾值測試: {'是' if args.rsi_test else '否'}")

    all_results = {}
    all_rsi_results = {}

    for idx, sym in enumerate(symbols, 1):
        print(f"\n  [{idx}/{len(symbols)}] 正在回測 {sym}...", end=" ", flush=True)
        klines = fetch_klines_backtest(sym, limit=args.limit)
        if not klines:
            print("資料不足，跳過")
            continue

        trades = run_backtest(sym, klines)
        if not trades:
            print("交易不足，跳過")
            continue

        results = analyze_trades(trades)
        all_results[sym] = results

        rsi_results = None
        if args.rsi_test:
            rsi_results = test_rsi_thresholds(sym, klines)
            all_rsi_results[sym] = rsi_results

        # 計算總交易數
        total_trades = sum(
            r["total"] for hp_results in results.values()
            for r in hp_results.values() if r
        )
        print(f"完成（{total_trades} 筆交易）")

        print_report(sym, results, rsi_results)
        time.sleep(0.3)  # 避免 API 限速

    if len(all_results) > 1:
        print_summary(all_results)

    # 儲存結果
    if args.save:
        save_data = {}
        for sym, results in all_results.items():
            save_data[sym] = {}
            for hp, strats in results.items():
                save_data[sym][str(hp)] = {}
                for strat, r in strats.items():
                    save_data[sym][str(hp)][strat] = r
        with open(args.save, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        print(f"\n  結果已儲存至: {args.save}")

    print("\n  回測完成！")


if __name__ == "__main__":
    main()
