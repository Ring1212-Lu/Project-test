#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
幣圈監控 v2 — 自我學習增強版
=============================
核心改進：
1.  Wilder 平滑 RSI（O(n) 效率）
2.  ATR 自適應止盈止損（取代固定百分比）
3.  布林帶波動率通道 + 價格位置判斷
4.  MACD 安全索引存取（修復越界 Bug）
5.  RSI 極端值過濾邏輯修正（不再矛盾過濾）
6.  成交量加權信號評分
7.  市場狀態偵測（trending/ranging/volatile）影響策略選擇
8.  自我學習模組：記錄預測 → 驗證結果 → 動態調整策略權重
9.  多維度綜合評分（勝率 × 信心 × 學習權重 × 市場適配）
10. 近期表現加權（指數衰減，近期結果更重要）
11. OBV 量能確認（防止量價背離假信號）
12. 連續虧損保護（自動降低曝險）
"""

import requests
import time
import json
import os
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from statistics import mean, stdev

# ===== 自我學習模組 =====
from learning_engine import LearningEngine, MarketRegime

try:
    import colorama
    colorama.init()
    def color(text, c):
        codes = {'red': '\033[91m', 'green': '\033[92m', 'yellow': '\033[93m',
                 'cyan': '\033[96m', 'white': '\033[97m', 'bold': '\033[1m', 'end': '\033[0m'}
        return f"{codes.get(c, '')}{text}{codes['end']}"
except ImportError:
    def color(text, c):
        return text

# ===== 全域設定 =====
BASE_URL   = "https://api.pionex.com/api/v1/market/klines"
TICK_URL   = "https://api.pionex.com/api/v1/market/tickers"
INTERVAL   = 60        # 秒，每輪間隔
TOP_N      = 3         # 最終回報前幾名
MIN_SIG    = 3         # 最低訊號次數門檻
TOP15_PCT  = 0.10      # 每側（漲/跌）取百分比
TOP15_MAX  = 10        # 每側最多取幾個
MAX_WORKERS = 3        # 並行執行緒數（太多會被 API 擋）
MAX_RETRIES = 3        # API 請求重試次數

# ATR 倍數（自適應止盈止損）
ATR_TP_MULT = {"做空": 2.0, "抄底": 2.5, "追多": 3.0}
ATR_SL_MULT = {"做空": 1.2, "抄底": 1.5, "追多": 1.5}

LEARNING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "learning_data.json")

# 連續虧損保護
MAX_CONSECUTIVE_LOSSES = 3  # 連續虧損超過此數，降低信心分數

# 自動優化間隔（每 N 輪執行一次回測優化）
AUTO_OPTIMIZE_INTERVAL = 10


# ============================================================
#  技術指標（優化版）
# ============================================================

def calc_rsi_wilder(closes, period=14):
    """Wilder 平滑 RSI — O(n) 複雜度"""
    if len(closes) < period + 1:
        return []
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    gains = [max(d, 0) for d in deltas[:period]]
    losses = [max(-d, 0) for d in deltas[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    rsi = []
    for i in range(period, len(deltas)):
        d = deltas[i]
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
        if avg_loss == 0:
            rsi.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi.append(100.0 - 100.0 / (1.0 + rs))
    return rsi


def calc_mfi_optimized(klines, period=14):
    """資金流量指標（滑動窗口優化版）"""
    if len(klines) < period + 2:
        return []

    # 預計算所有 typical price 和 money flow
    tps = []
    vols = []
    for i in range(len(klines)):
        try:
            tp = (float(klines[i]['high']) + float(klines[i]['low']) + float(klines[i]['close'])) / 3
            vol = float(klines[i]['volume'])
        except (KeyError, ValueError):
            tp = 0
            vol = 0
        tps.append(tp)
        vols.append(vol)

    # 初始窗口
    pos = neg = 0
    for j in range(1, period + 1):
        mf = tps[j] * vols[j]
        if tps[j] > tps[j - 1]:
            pos += mf
        else:
            neg += mf

    mfi = []
    mfi.append(100.0 if neg == 0 else 100.0 - 100.0 / (1.0 + pos / (neg + 1e-9)))

    # 滑動窗口
    for i in range(period + 1, len(klines)):
        # 移除最舊
        old_j = i - period
        old_mf = tps[old_j] * vols[old_j]
        if old_j > 0 and tps[old_j] > tps[old_j - 1]:
            pos -= old_mf
        else:
            neg -= old_mf

        # 加入最新
        new_mf = tps[i] * vols[i]
        if tps[i] > tps[i - 1]:
            pos += new_mf
        else:
            neg += new_mf

        # 防止浮點誤差導致負值
        pos = max(pos, 0)
        neg = max(neg, 0)

        mfi.append(100.0 if neg == 0 else 100.0 - 100.0 / (1.0 + pos / (neg + 1e-9)))

    return mfi


def calc_ema(arr, period):
    """指數移動平均"""
    if not arr:
        return []
    k = 2.0 / (period + 1)
    ema = [arr[0]]
    for v in arr[1:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


def calc_macd_hist(closes):
    """MACD 柱狀圖"""
    if len(closes) < 26:
        return []
    e12 = calc_ema(closes, 12)
    e26 = calc_ema(closes, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = calc_ema(macd, 9)
    return [a - b for a, b in zip(macd, sig)]


def calc_atr(klines, period=14):
    """Average True Range"""
    if len(klines) < period + 1:
        return []
    trs = []
    for i in range(1, len(klines)):
        try:
            h = float(klines[i]['high'])
            l = float(klines[i]['low'])
            pc = float(klines[i - 1]['close'])
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        except (KeyError, ValueError):
            trs.append(0)

    atr = [mean(trs[:period])]
    for i in range(period, len(trs)):
        atr.append((atr[-1] * (period - 1) + trs[i]) / period)
    return atr


def calc_bollinger(closes, period=20, num_std=2):
    """布林帶 (middle, upper, lower)"""
    if len(closes) < period:
        return [], [], []
    mid, upper, lower = [], [], []
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1: i + 1]
        m = mean(window)
        s = stdev(window) if len(window) > 1 else 0
        mid.append(m)
        upper.append(m + num_std * s)
        lower.append(m - num_std * s)
    return mid, upper, lower


def calc_obv(closes, volumes):
    """On-Balance Volume — 量能方向確認"""
    if len(closes) < 2 or len(volumes) < 2:
        return []
    obv = [0]
    for i in range(1, min(len(closes), len(volumes))):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    return obv


def obv_trend(obv_vals, lookback=10):
    """判斷 OBV 趨勢方向：1=上升, -1=下降, 0=持平"""
    if len(obv_vals) < lookback:
        return 0
    recent = obv_vals[-lookback:]
    slope = recent[-1] - recent[0]
    if slope > 0:
        return 1
    elif slope < 0:
        return -1
    return 0


# ============================================================
#  資料擷取
# ============================================================

def fetch_tickers():
    try:
        r = _session.get(TICK_URL, params={"type": "PERP"}, timeout=15)
        if r.status_code == 200:
            return r.json().get("data", {}).get("tickers", [])
    except Exception as e:
        print(f"  [ERROR] 行情抓取失敗: {type(e).__name__}: {e}")
    return []


def _create_session():
    """建立帶重試機制的 requests Session"""
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    session = requests.Session()
    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=0.5,       # 0.5s, 1s, 2s
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=5, pool_maxsize=5)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# 全域 Session（連線池復用，避免反覆握手）
_session = _create_session()


def fetch_klines(symbol):
    try:
        r = _session.get(BASE_URL, params={"symbol": symbol, "interval": "1M", "limit": 120}, timeout=15)
        if r.status_code == 200:
            kl = r.json().get("data", {}).get("klines", [])
            if isinstance(kl, list) and len(kl) >= 60:
                return kl
    except Exception as e:
        print(f"    [WARN] {symbol}: {type(e).__name__}")
    return []


def fetch_and_analyze(coin, learner, opt_params=None):
    """單一幣種：抓取 + 分析（供並行使用）"""
    sym = coin["symbol"]
    klines = fetch_klines(sym)
    if not klines:
        return None
    return analyze(sym, klines, round(coin["change"], 2), learner, opt_params)


# ============================================================
#  核心分析
# ============================================================

def analyze(symbol, klines, change24h, learner, opt_params=None):
    closes = [float(k['close']) for k in klines if float(k.get('close', 0)) > 0]
    if len(closes) < 60:
        return None

    volumes = []
    for k in klines:
        try:
            volumes.append(float(k.get('volume', 0)))
        except (ValueError, TypeError):
            volumes.append(0)

    # 計算所有指標
    rsi_vals   = calc_rsi_wilder(closes)
    mfi_vals   = calc_mfi_optimized(klines)
    macd_hist  = calc_macd_hist(closes)
    atr_vals   = calc_atr(klines)
    bb_mid, bb_upper, bb_lower = calc_bollinger(closes)
    obv_vals   = calc_obv(closes, volumes[:len(closes)])

    if not rsi_vals or not mfi_vals or not macd_hist:
        return None

    # 市場狀態偵測
    regime = MarketRegime.detect(closes)

    # OBV 趨勢
    obv_dir = obv_trend(obv_vals)

    # 對齊索引
    RSI_PERIOD = 14
    RSI_OFF = RSI_PERIOD + 1

    length = min(len(rsi_vals), len(mfi_vals))

    # 使用優化參數（如果有的話）
    rsi_short_th = 70
    rsi_long_th = 30
    hold_period = 5
    if opt_params:
        rsi_short_th = opt_params.get("rsi_short_thresh", 70)
        rsi_long_th = opt_params.get("rsi_long_thresh", 30)
        hold_period = opt_params.get("best_hold_period", 5)

    # 統計計數
    stats = {
        "做空": {"win": 0, "total": 0, "vol_sum": 0},
        "抄底": {"win": 0, "total": 0, "vol_sum": 0},
        "追多": {"win": 0, "total": 0, "vol_sum": 0},
    }

    for i in range(6, length - hold_period):
        ci = i + RSI_OFF
        if ci + hold_period >= len(closes) or ci < 2:
            continue
        # MACD 安全索引
        if ci >= len(macd_hist) or ci - 1 < 0:
            continue

        price      = closes[ci]
        next_price = closes[ci + hold_period]
        rsi_prev   = rsi_vals[i - 1] if i - 1 >= 0 else 50
        rsi_cur    = rsi_vals[i]
        mfi_cur    = mfi_vals[i] if i < len(mfi_vals) else 50

        macd_down = macd_hist[ci] < macd_hist[ci - 1]
        macd_up   = macd_hist[ci] > macd_hist[ci - 1]

        try:
            vol = volumes[ci] if ci < len(volumes) else 1.0
        except IndexError:
            vol = 1.0

        # ---- 做空條件（使用優化閾值）----
        if rsi_prev > rsi_short_th and rsi_cur < rsi_prev and macd_down and rsi_cur > 30:
            stats["做空"]["total"] += 1
            stats["做空"]["vol_sum"] += vol
            if next_price < price:
                stats["做空"]["win"] += 1

        # ---- 抄底條件（使用優化閾值）----
        if rsi_prev < rsi_long_th and rsi_cur > rsi_prev and mfi_cur < 25 and macd_up:
            stats["抄底"]["total"] += 1
            stats["抄底"]["vol_sum"] += vol
            if next_price > price:
                stats["抄底"]["win"] += 1

        # ---- 追多條件 ----
        # 連續上漲 + RSI 強勢區 + MACD 動能向上
        if (closes[ci] > closes[ci - 1] > closes[ci - 2]
                and 60 < rsi_cur < 85 and macd_up):
            stats["追多"]["total"] += 1
            stats["追多"]["vol_sum"] += vol
            if next_price > price:
                stats["追多"]["win"] += 1

    # 計算勝率與綜合分數
    def rate(w, t):
        return round(w / t * 100, 1) if t > 0 else 0.0

    strat_results = {}
    for strat, s in stats.items():
        r = rate(s["win"], s["total"])
        weight = learner.get_weight(symbol, strat)
        regime_bonus = learner.get_regime_bonus(regime, strat)

        # 信心係數：樣本越多越高
        confidence = min(s["total"] / 10.0, 1.5) if s["total"] >= MIN_SIG else 0

        # OBV 確認加成
        obv_bonus = 1.0
        if strat in ("抄底", "追多") and obv_dir == 1:
            obv_bonus = 1.1  # 量能支持做多
        elif strat == "做空" and obv_dir == -1:
            obv_bonus = 1.1  # 量能支持做空
        elif strat in ("抄底", "追多") and obv_dir == -1:
            obv_bonus = 0.85  # 量價背離，降低信心
        elif strat == "做空" and obv_dir == 1:
            obv_bonus = 0.85

        # 近期表現加權
        recent_acc, recent_n = learner.get_recent_accuracy(symbol, strat)
        recent_bonus = 1.0
        if recent_acc is not None and recent_n >= 3:
            if recent_acc > 60:
                recent_bonus = 1.15
            elif recent_acc < 40:
                recent_bonus = 0.80

        # 綜合分數
        score = r * confidence * weight * regime_bonus * obv_bonus * recent_bonus

        strat_results[strat] = {
            "rate": r, "total": s["total"], "win": s["win"],
            "confidence": round(confidence, 2),
            "weight": round(weight, 2),
            "regime_bonus": round(regime_bonus, 2),
            "obv_bonus": round(obv_bonus, 2),
            "recent_bonus": round(recent_bonus, 2),
            "score": round(score, 1),
        }

    # 過濾不足樣本
    valid = {k: v for k, v in strat_results.items() if v["total"] >= MIN_SIG}
    if not valid:
        return None

    best_strat = max(valid, key=lambda k: valid[k]["score"])
    best = valid[best_strat]

    current_price = closes[-1]
    current_rsi   = rsi_vals[-1] if rsi_vals else 50
    current_mfi   = mfi_vals[-1] if mfi_vals else 50

    # ATR 自適應止盈止損
    current_atr = atr_vals[-1] if atr_vals else current_price * 0.02
    tp_dist = current_atr * ATR_TP_MULT[best_strat]
    sl_dist = current_atr * ATR_SL_MULT[best_strat]

    if best_strat == "做空":
        tp_price = round(current_price - tp_dist, 6)
        sl_price = round(current_price + sl_dist, 6)
    else:
        tp_price = round(current_price + tp_dist, 6)
        sl_price = round(current_price - sl_dist, 6)

    tp_pct = round(tp_dist / current_price * 100, 2)
    sl_pct = round(sl_dist / current_price * 100, 2)
    rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0

    # 布林帶位置
    bb_pos = "N/A"
    if bb_upper and bb_lower and bb_mid:
        if current_price >= bb_upper[-1]:
            bb_pos = "upper+"
        elif current_price <= bb_lower[-1]:
            bb_pos = "lower-"
        elif current_price > bb_mid[-1]:
            bb_pos = "mid+"
        else:
            bb_pos = "mid-"

    # 策略明細
    detail_parts = []
    for st in ["做空", "抄底", "追多"]:
        sr = strat_results[st]
        detail_parts.append(f"{st}{sr['rate']}%({sr['total']})")
    detail = " | ".join(detail_parts)

    # 信號強度等級
    score_val = best["score"]
    if score_val >= 120:
        signal_strength = "STRONG"
    elif score_val >= 80:
        signal_strength = "MEDIUM"
    else:
        signal_strength = "WEAK"

    return {
        "symbol":         symbol,
        "change24h":      change24h,
        "price":          current_price,
        "rsi":            round(current_rsi, 1),
        "mfi":            round(current_mfi, 1),
        "bb_pos":         bb_pos,
        "atr":            round(current_atr, 6),
        "regime":         regime,
        "obv_dir":        obv_dir,
        # 最佳策略
        "best_strat":     best_strat,
        "best_rate":      best["rate"],
        "best_total":     best["total"],
        "best_score":     best["score"],
        "confidence":     best["confidence"],
        "weight":         best["weight"],
        "regime_bonus":   best["regime_bonus"],
        "obv_bonus":      best["obv_bonus"],
        "recent_bonus":   best["recent_bonus"],
        "signal_strength": signal_strength,
        "detail":         detail,
        # 進出場
        "tp":             tp_price,
        "sl":             sl_price,
        "rr":             rr,
        "tp_pct":         tp_pct,
        "sl_pct":         sl_pct,
        # 所有策略結果
        "all_strats":     strat_results,
    }


# ============================================================
#  主流程
# ============================================================

def run_scan(learner, round_num):
    print(color("=" * 62, 'cyan'))
    print(color(" 幣圈監控 v2 — 自我學習增強版", 'cyan'))
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  第 {round_num} 輪")
    print(f"   學習資料：{learner.total_predictions} 筆預測 / "
          f"{learner.total_validations} 筆驗證")
    print(color("=" * 62, 'cyan'))

    # Step 0：驗證過去的預測
    validated = learner.validate_pending_predictions()
    if validated > 0:
        print(color(f"\n[LEARN] 本輪驗證了 {validated} 筆歷史預測，權重已更新", 'yellow'))

    # 定期清理過期權重（每 50 輪一次）
    if round_num % 50 == 0:
        learner.cleanup_stale_weights()

    # Step 0.5：自動優化（每 AUTO_OPTIMIZE_INTERVAL 輪執行一次）
    opt_params = learner.get_optimized_params()
    if round_num % AUTO_OPTIMIZE_INTERVAL == 0 and round_num > 1:
        try:
            from backtest import fetch_klines_backtest, run_backtest, analyze_trades
            # 取成交量前幾的幣種做回測
            top_syms = []
            temp_tickers = fetch_tickers()
            for t in temp_tickers:
                sym = t.get("symbol", "")
                if sym.endswith("_USDT_PERP"):
                    try:
                        vol = float(t.get("amount", 0))
                        if vol > 50000:
                            top_syms.append((sym, vol))
                    except (ValueError, TypeError):
                        pass
            top_syms.sort(key=lambda x: x[1], reverse=True)
            opt_symbols = [s[0] for s in top_syms[:8]]

            if opt_symbols:
                opt_params = learner.auto_optimize(
                    opt_symbols, fetch_klines_backtest, run_backtest, analyze_trades
                )
        except Exception as e:
            print(f"[AUTO-OPT] 優化過程出錯: {type(e).__name__}: {e}")

    if opt_params.get("last_optimized"):
        print(f"   優化參數：RSI 做空>{opt_params['rsi_short_thresh']} "
              f"抄底<{opt_params['rsi_long_thresh']} "
              f"持倉期={opt_params['best_hold_period']}根 "
              f"(上次優化: {opt_params['last_optimized']})")

    # Step 1：抓全市場行情
    print("\n[INFO] 正在抓取全市場行情...")
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
        print("[ERROR] 無法取得行情資料，請檢查網路連線")
        return

    perps.sort(key=lambda x: x["change"], reverse=True)
    n15 = max(1, int(len(perps) * TOP15_PCT))

    gainers = perps[:n15][:TOP15_MAX]
    losers  = list(reversed(perps[-n15:]))[:TOP15_MAX]
    pool    = gainers + losers

    # 去重
    seen = set()
    unique_pool = []
    for coin in pool:
        if coin["symbol"] not in seen:
            seen.add(coin["symbol"])
            unique_pool.append(coin)
    pool = unique_pool

    print(f"[OK] 共 {len(perps)} 個合約，分析池 {len(pool)} 個")
    if gainers:
        print(f"  漲幅榜首：{gainers[0]['symbol']} +{gainers[0]['change']:.1f}%")
    if losers:
        print(f"  跌幅榜首：{losers[0]['symbol']} {losers[0]['change']:.1f}%")

    # Step 2：並行分析（大幅加速）
    print(f"\n[INFO] 開始並行分析 {len(pool)} 個幣種（{MAX_WORKERS} 執行緒）...")
    t_start = time.time()
    results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {
            executor.submit(fetch_and_analyze, coin, learner, opt_params): coin
            for coin in pool
        }
        done_count = 0
        for future in as_completed(future_map):
            done_count += 1
            coin = future_map[future]
            sym = coin["symbol"]
            direction = "+" if coin["change"] > 0 else ""
            try:
                res = future.result()
                if res:
                    results.append(res)
                    print(f"  [{done_count:02d}/{len(pool)}] {sym} ({direction}{coin['change']:.1f}%)  "
                          f"{res['best_strat']} {res['best_rate']}% "
                          f"[{res['signal_strength']}] "
                          f"(score:{res['best_score']})")
                else:
                    print(f"  [{done_count:02d}/{len(pool)}] {sym} ({direction}{coin['change']:.1f}%)  跳過")
            except Exception as e:
                print(f"  [{done_count:02d}/{len(pool)}] {sym} 錯誤: {e}")

    elapsed = round(time.time() - t_start, 1)
    print(f"\n[INFO] 分析完成，耗時 {elapsed} 秒")

    if not results:
        print("\n[WARN] 本輪無足夠樣本的幣種，請稍後再試")
        return

    # Step 3：依綜合分數排序
    results.sort(key=lambda x: x["best_score"], reverse=True)
    top = results[:TOP_N]

    # Step 4：記錄預測
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

    # Step 5：輸出結果
    print(color("\n" + "=" * 62, 'yellow'))
    print(color(f" 綜合前 {len(top)} 名（共分析 {len(results)} 個有效幣種）", 'yellow'))
    print(color("=" * 62, 'yellow'))

    medals = ["[1st]", "[2nd]", "[3rd]"]
    for rank, r in enumerate(top):
        chg = f"+{r['change24h']:.2f}%" if r['change24h'] > 0 else f"{r['change24h']:.2f}%"
        obv_str = {1: "Up", -1: "Down", 0: "Flat"}.get(r['obv_dir'], "?")
        strength_color = {'STRONG': 'green', 'MEDIUM': 'yellow', 'WEAK': 'red'}

        print(f"\n{medals[rank]} {color(r['symbol'], 'cyan')}  24h: {chg}")
        print(f"   Price: {r['price']}  |  RSI: {r['rsi']}  |  MFI: {r['mfi']}")
        print(f"   BB: {r['bb_pos']}  |  ATR: {r['atr']}  |  OBV: {obv_str}")
        print(f"   Regime: {r['regime']}")
        print(f"   策略：{r['best_strat']} {color(str(r['best_rate']) + '%', 'green')}"
              f"（{r['best_total']}次）")
        print(f"   Signal: {color(r['signal_strength'], strength_color.get(r['signal_strength'], 'white'))}"
              f"  Score: {r['best_score']}")
        print(f"   Weights: learn={r['weight']} regime={r['regime_bonus']} "
              f"obv={r['obv_bonus']} recent={r['recent_bonus']} "
              f"conf={r['confidence']}")
        print(f"   明細：{r['detail']}")
        print(f"   進場: {r['price']}  "
              f"止盈: {r['tp']} (+{r['tp_pct']}%)  "
              f"止損: {r['sl']} (-{r['sl_pct']}%)  "
              f"風報比: 1:{r['rr']}")
        print(f"   " + "-" * 56)

    # Step 6：學習統計
    learner.print_summary()

    # 顯示歷史最佳組合
    top_perf = learner.get_top_performers(3)
    if top_perf:
        print(f"\n[LEARN] 歷史最佳組合：")
        for sym_strat, acc, cnt in top_perf:
            print(f"  {sym_strat}: {acc}% ({cnt}次)")

    print(color("\n" + "=" * 62, 'cyan'))
    print(color(f" 掃描完成  |  下次更新：{INTERVAL} 秒後", 'cyan'))
    print(color("=" * 62, 'cyan'))


def main():
    print(color(" 幣圈監控 v2 — 自我學習增強版啟動", 'cyan'))
    print("   停止方式：按 Ctrl + C\n")

    learner = LearningEngine(LEARNING_FILE)
    round_num = 0
    try:
        while True:
            round_num += 1
            print(f"\n{'=' * 62}")
            print(f" 第 {round_num} 輪掃描")
            run_scan(learner, round_num)
            print(f"\n 等待 {INTERVAL} 秒後進行下一輪...")
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        learner.save()
        print(color("\n\n 監控已停止，學習資料已儲存！", 'yellow'))


if __name__ == "__main__":
    main()
