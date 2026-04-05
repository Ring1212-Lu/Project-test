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
import requests as req_lib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from flask import Flask, render_template, jsonify, request

from crypto_monitor_v2 import (
    fetch_tickers, fetch_and_analyze, analyze, get_btc_trend,
    TOP15_PCT, TOP15_MAX, TOP_N, INTERVAL, MAX_WORKERS
)
from learning_engine import LearningEngine
from trading_bot import RiskManager, check_positions, save_trade_log
from pionex_client import PionexClient

app = Flask(__name__, template_folder="templates", static_folder="static")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LEARNING_FILE = os.path.join(BASE_DIR, "learning_data.json")
TRADE_LOG_FILE = os.path.join(BASE_DIR, "trade_log.json")

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


def notify_strong_signals(results):
    """掃描完成後，將 STRONG 訊號透過 Discord 通知"""
    strong = [r for r in results if r.get("signal_strength") == "STRONG"]
    if not strong:
        return
    lines = ["🔔 **強訊號通知 ({} 個)**".format(len(strong))]
    for r in strong[:5]:  # 最多通知 5 個
        sym = r["symbol"].replace("_USDT_PERP", "")
        lines.append(
            f"```\n{sym} | {r['best_strat']} | "
            f"分數:{r['best_score']} 勝率:{r['best_rate']}%\n"
            f"價格:{r['price']}  TP:{r['tp']}  SL:{r['sl']}\n```"
        )
    send_discord_message("\n".join(lines))


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

# 交易機器人全域狀態
trading_state = {
    "enabled": False,
    "round": 0,
    "status": {},
    "open_positions": [],
    "closed_trades": [],
    "logs": [],
}
trading_lock = threading.Lock()
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


def run_trading_bot(initial_balance=100):
    """背景交易機器人執行緒"""
    global trading_risk_mgr, trading_client

    from crypto_monitor_v2 import get_btc_trend

    client = PionexClient(paper_mode=True)
    client.paper_balance = initial_balance

    risk_config = {
        "max_loss_pct": 10,
        "max_position_pct": 20,
        "max_positions": 2,
        "max_consecutive_loss": 3,
        "min_signal_strength": "STRONG",
        "min_score": 60,
        "min_win_rate": 55,
        "min_rr": 1.3,
    }
    risk_mgr = RiskManager(initial_balance, risk_config)
    learner = LearningEngine(LEARNING_FILE)

    with trading_lock:
        trading_risk_mgr = risk_mgr
        trading_client = client
        trading_state["enabled"] = True

    add_trading_log(f"機器人啟動 | 初始資金: {initial_balance}U (模擬模式)")
    # 等待 45 秒再開始，錯開與監控掃描的 API 呼叫
    add_trading_log("等待 45 秒後開始掃描（避免 API 限流）...")
    time.sleep(45)
    round_num = 0

    while True:
        round_num += 1
        with trading_lock:
            trading_state["round"] = round_num

        add_trading_log(f"=== 交易掃描第 {round_num} 輪 ===")

        # 檢查現有持倉
        check_positions(risk_mgr, client)

        # 驗證學習預測
        validated = learner.validate_pending_predictions()
        if validated > 0:
            add_trading_log(f"驗證了 {validated} 筆預測")

        opt_params = learner.get_optimized_params()
        btc_trend = get_btc_trend()
        btc_str = {1: "UP", -1: "DOWN", 0: "NEUTRAL"}.get(btc_trend, "?")
        add_trading_log(f"BTC 趨勢: {btc_str}")

        # 抓行情（使用共享快取，減少 API 呼叫）
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

        if not perps:
            add_trading_log("無法取得行情")
            time.sleep(INTERVAL)
            continue

        perps.sort(key=lambda x: x["change"], reverse=True)
        n15 = max(1, int(len(perps) * TOP15_PCT))
        gainers = perps[:n15][:TOP15_MAX]
        losers = list(reversed(perps[-n15:]))[:TOP15_MAX]
        pool = gainers + losers

        seen = set()
        pool = [c for c in pool if c["symbol"] not in seen and not seen.add(c["symbol"])]

        # 排除已持倉
        held_symbols = {p["symbol"] for p in risk_mgr.open_positions}
        pool = [c for c in pool if c["symbol"] not in held_symbols]

        add_trading_log(f"分析池 {len(pool)} 個（排除已持倉 {len(held_symbols)}）")

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
            add_trading_log("無有效信號")
            _update_trading_state(risk_mgr)
            save_trade_log(risk_mgr)
            time.sleep(INTERVAL)
            continue

        results.sort(key=lambda x: x["best_score"], reverse=True)

        # 嘗試開倉
        opened = 0
        for r in results[:TOP_N]:
            sym_short = r['symbol'].replace('_USDT_PERP', '')

            # 安全過濾：24h 跌幅超過 15% 不做追多
            if r["best_strat"] in ("追多", "抄底") and r.get("change24h", 0) < -15:
                add_trading_log(f"{sym_short}: SKIP (24h跌{r['change24h']:.1f}%，暴跌中不做多)")
                continue

            can_open, reason = risk_mgr.can_open_position(r)
            if not can_open:
                add_trading_log(f"{sym_short}: SKIP ({reason})")
                continue

            size_usd, pct = risk_mgr.calc_position_size(r)
            if size_usd < 1:
                continue

            strat = r["best_strat"]
            side = "SELL" if strat == "做空" else "BUY"
            price = r["price"]
            quantity = round(size_usd / price, 6) if price > 0 else 0
            if quantity <= 0:
                continue

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
                learner.record_prediction(
                    symbol=r["symbol"], strategy=strat,
                    entry_price=price, tp_price=r["tp"], sl_price=r["sl"],
                    rate=r["best_rate"], score=r["best_score"], regime=r["regime"],
                )
                opened += 1
                add_trading_log(f"OPEN {r['symbol'].replace('_USDT_PERP','')} {strat}({side}) "
                                f"{size_usd}U | Score:{r['best_score']} | TP:{r['tp']} SL:{r['sl']}")
            else:
                add_trading_log(f"ORDER FAILED: {r['symbol']} - {order_result}")

        if opened > 0:
            add_trading_log(f"本輪開倉 {opened} 筆")


        _update_trading_state(risk_mgr)
        save_trade_log(risk_mgr)
        learner.save()

        status = risk_mgr.get_status()
        add_trading_log(f"Balance: {status['balance']}U | Open: {status['open_positions']} | "
                        f"Today: {status['daily_pnl']:+.2f}U")

        time.sleep(INTERVAL)


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
        learner = LearningEngine(LEARNING_FILE)
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

            # BTC 大盤趨勢
            btc_trend = get_btc_trend()
            btc_str = {1: "UP", -1: "DOWN", 0: "NEUTRAL"}.get(btc_trend, "?")
            add_log(f"BTC 大盤趨勢: {btc_str}")

            # 並行分析
            add_log(f"並行分析 {len(pool)} 個幣種（{MAX_WORKERS} 執行緒）...")
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
            add_log(f"分析完成，耗時 {elapsed} 秒")

            if not results:
                add_log("本輪無足夠樣本的幣種")
                with state_lock:
                    state["status"] = "waiting"
                time.sleep(INTERVAL)
                continue

            results.sort(key=lambda x: x["best_score"], reverse=True)
            top = results[:TOP_N]

            # Discord 通知強訊號
            notify_strong_signals(top)

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
            print(f"[SCAN] 準備更新儀表板：{len(top)} 個推薦，{len(results)} 個有效結果")
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
                consecutive_errors = 0  # 重置錯誤計數

        except Exception as e:
            consecutive_errors += 1
            err_msg = f"掃描出錯: {type(e).__name__}: {e}"
            import traceback
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

        time.sleep(INTERVAL)


# ===== Flask Routes =====

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
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
                    "tp": r.get("tp", 0),
                    "sl": r.get("sl", 0),
                    "rr": r.get("rr", 0),
                    "tp_pct": r.get("tp_pct", 0),
                    "sl_pct": r.get("sl_pct", 0),
                })
            except Exception as e:
                print(f"[API] 序列化結果出錯: {e}, keys={list(r.keys())}")

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

    app.run(host="0.0.0.0", port=5000, debug=False)
