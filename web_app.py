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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from flask import Flask, render_template, jsonify

from crypto_monitor_v2 import (
    fetch_tickers, fetch_and_analyze, analyze,
    TOP15_PCT, TOP15_MAX, TOP_N, INTERVAL, MAX_WORKERS
)
from learning_engine import LearningEngine

app = Flask(__name__, template_folder="templates", static_folder="static")

LEARNING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "learning_data.json")

# 全域狀態
state = {
    "round": 0,
    "status": "idle",
    "last_update": None,
    "top_results": [],
    "all_results": [],
    "pool_size": 0,
    "total_perps": 0,
    "gainer_top": None,
    "loser_top": None,
    "learn_stats": {},
    "top_performers": [],
    "logs": [],
}
state_lock = threading.Lock()

MAX_LOGS = 200


def add_log(msg):
    with state_lock:
        state["logs"].append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "msg": msg,
        })
        if len(state["logs"]) > MAX_LOGS:
            state["logs"] = state["logs"][-MAX_LOGS:]


def run_background_scan():
    """背景掃描執行緒"""
    learner = LearningEngine(LEARNING_FILE)
    round_num = 0

    while True:
        round_num += 1
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
            add_log("無法取得行情資料")
            with state_lock:
                state["status"] = "waiting"
            time.sleep(INTERVAL)
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

        # 並行分析
        add_log(f"並行分析 {len(pool)} 個幣種（{MAX_WORKERS} 執行緒）...")
        t_start = time.time()
        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_map = {
                executor.submit(fetch_and_analyze, coin, learner): coin
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
        add_log(f"分析完成，耗時 {elapsed} 秒")

        if not results:
            add_log("本輪無足夠樣本的幣種")
            with state_lock:
                state["status"] = "waiting"
            time.sleep(INTERVAL)
            continue

        results.sort(key=lambda x: x["best_score"], reverse=True)
        top = results[:TOP_N]

        # 記錄預測
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

        # 學習統計
        stats = learner.data["stats"]
        decided = stats["total_wins"] + stats["total_losses"]
        overall_rate = round(stats["total_wins"] / decided * 100, 1) if decided > 0 else 0

        strat_stats = {}
        for strat in ["做空", "抄底", "追多"]:
            s = stats["strategy_stats"].get(strat, {"wins": 0, "losses": 0, "expired": 0})
            sw, sl_count = s["wins"], s["losses"]
            st = sw + sl_count
            sr = round(sw / st * 100, 1) if st > 0 else 0
            strat_stats[strat] = {"wins": sw, "losses": sl_count, "rate": sr}

        # 更新全域狀態
        with state_lock:
            state["top_results"] = top
            state["all_results"] = results
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

        add_log(f"第 {round_num} 輪完成，共 {len(results)} 個有效幣種，前 {len(top)} 名已更新")
        time.sleep(INTERVAL)


# ===== Flask Routes =====

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    with state_lock:
        # 序列化 top_results（只取需要的欄位）
        top = []
        for r in state["top_results"]:
            top.append({
                "symbol": r["symbol"],
                "change24h": r["change24h"],
                "price": r["price"],
                "rsi": r["rsi"],
                "mfi": r["mfi"],
                "bb_pos": r["bb_pos"],
                "atr": r["atr"],
                "regime": r["regime"],
                "obv_dir": r["obv_dir"],
                "best_strat": r["best_strat"],
                "best_rate": r["best_rate"],
                "best_total": r["best_total"],
                "best_score": r["best_score"],
                "confidence": r["confidence"],
                "weight": r["weight"],
                "regime_bonus": r["regime_bonus"],
                "obv_bonus": r["obv_bonus"],
                "recent_bonus": r["recent_bonus"],
                "signal_strength": r["signal_strength"],
                "detail": r["detail"],
                "tp": r["tp"],
                "sl": r["sl"],
                "rr": r["rr"],
                "tp_pct": r["tp_pct"],
                "sl_pct": r["sl_pct"],
            })

        return jsonify({
            "round": state["round"],
            "status": state["status"],
            "last_update": state["last_update"],
            "top_results": top,
            "all_count": len(state["all_results"]),
            "pool_size": state["pool_size"],
            "total_perps": state["total_perps"],
            "gainer_top": state["gainer_top"],
            "loser_top": state["loser_top"],
            "learn_stats": state["learn_stats"],
            "top_performers": state["top_performers"],
            "logs": state["logs"][-50:],
            "interval": INTERVAL,
        })


if __name__ == "__main__":
    # 啟動背景掃描
    scan_thread = threading.Thread(target=run_background_scan, daemon=True)
    scan_thread.start()

    print("=" * 50)
    print(" 幣圈監控 Web 儀表板啟動")
    print(" 開啟瀏覽器前往: http://localhost:5000")
    print("=" * 50)

    app.run(host="0.0.0.0", port=5000, debug=False)
