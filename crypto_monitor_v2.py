#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
幣圈監控 v2 — 自我學習增強版
=============================
核心改進：
1. Wilder 平滑 RSI（O(n) 效率）
2. ATR 自適應止盈止損（取代固定百分比）
3. 布林帶波動率通道
4. MACD 安全索引存取（修復越界 Bug）
5. RSI 極端值過濾邏輯修正
6. 成交量加權信號評分
7. 自我學習模組：記錄預測 → 驗證結果 → 動態調整策略權重
8. 多維度綜合評分排序（勝率 × 信心 × 學習權重）
"""

import requests
import time
import json
import os
from datetime import datetime
from statistics import mean, stdev

# ===== 自我學習模組 =====
from learning_engine import LearningEngine

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
TOP15_PCT  = 0.15      # 每側（漲/跌）取百分比
TOP15_MAX  = 15        # 每側最多取幾個

# ATR 倍數（取代固定百分比）
ATR_TP_MULT = {"做空": 2.0, "抄底": 2.5, "追多": 3.0}
ATR_SL_MULT = {"做空": 1.2, "抄底": 1.5, "追多": 1.5}

LEARNING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "learning_data.json")


# ============================================================
#  技術指標（優化版）
# ============================================================

def calc_rsi_wilder(closes, period=14):
    """Wilder 平滑 RSI — O(n) 複雜度"""
    if len(closes) < period + 1:
        return []
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    # 初始平均
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


def calc_mfi(klines, period=14):
    """資金流量指標"""
    mfi = []
    for i in range(period + 1, len(klines)):
        pos = neg = 0
        for j in range(i - period, i):
            try:
                c, p = klines[j], klines[j - 1]
                tp0 = (float(c['high']) + float(c['low']) + float(c['close'])) / 3
                tp1 = (float(p['high']) + float(p['low']) + float(p['close'])) / 3
                mf = tp0 * float(c['volume'])
                if tp0 > tp1:
                    pos += mf
                else:
                    neg += mf
            except (KeyError, ValueError, ZeroDivisionError):
                pass
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
    """Average True Range — 用於自適應止盈止損"""
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

    # Wilder 平滑
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


# ============================================================
#  資料擷取
# ============================================================

def fetch_tickers():
    try:
        r = requests.get(TICK_URL, params={"type": "PERP"}, timeout=15)
        if r.status_code == 200:
            return r.json().get("data", {}).get("tickers", [])
    except Exception as e:
        print(f"  [ERROR] 行情抓取失敗: {e}")
    return []


def fetch_klines(symbol):
    try:
        r = requests.get(BASE_URL, params={"symbol": symbol, "interval": "1M", "limit": 300}, timeout=20)
        if r.status_code == 200:
            kl = r.json().get("data", {}).get("klines", [])
            if isinstance(kl, list) and len(kl) >= 60:
                return kl
    except Exception as e:
        print(f"    [WARN] {symbol} 抓取失敗: {e}")
    return []


# ============================================================
#  核心分析（修復版 + 增強版）
# ============================================================

def analyze(symbol, klines, change24h, learner):
    closes = [float(k['close']) for k in klines if float(k.get('close', 0)) > 0]
    if len(closes) < 60:
        return None

    # 計算所有指標
    rsi_vals   = calc_rsi_wilder(closes)
    mfi_vals   = calc_mfi(klines)
    macd_hist  = calc_macd_hist(closes)
    atr_vals   = calc_atr(klines)
    bb_mid, bb_upper, bb_lower = calc_bollinger(closes)

    if not rsi_vals or not mfi_vals or not macd_hist:
        return None

    # 對齊長度：RSI 從 index=period 開始，對應 closes[period+1]
    # RSI offset: calc_rsi_wilder 回傳長度 = len(closes) - period - 1
    # rsi_vals[0] 對應 closes[period+1] => closes_idx = rsi_idx + period + 1
    RSI_PERIOD = 14
    RSI_OFF = RSI_PERIOD + 1  # rsi_vals[i] 對應 closes[i + RSI_OFF]

    length = min(len(rsi_vals), len(mfi_vals))

    # 統計計數
    stats = {
        "做空": {"win": 0, "total": 0, "vol_sum": 0},
        "抄底": {"win": 0, "total": 0, "vol_sum": 0},
        "追多": {"win": 0, "total": 0, "vol_sum": 0},
    }

    for i in range(6, length - 5):
        ci = i + RSI_OFF
        if ci + 5 >= len(closes) or ci < 2:
            continue
        # 安全存取 MACD
        if ci >= len(macd_hist) or ci - 1 < 0:
            continue

        price      = closes[ci]
        next_price = closes[ci + 5]
        rsi_prev   = rsi_vals[i - 1] if i - 1 >= 0 else 50
        rsi_cur    = rsi_vals[i]
        mfi_cur    = mfi_vals[i]

        # MACD 方向
        macd_down = macd_hist[ci] < macd_hist[ci - 1]
        macd_up   = macd_hist[ci] > macd_hist[ci - 1]

        # 成交量（用於加權）
        try:
            vol = float(klines[ci]['volume'])
        except (KeyError, ValueError, IndexError):
            vol = 1.0

        # ---- 做空條件（修正版）----
        # 修正：移除 not_extreme 過濾，因為 RSI > 70 本身就是超買信號
        # 改為：RSI 從超買回落 + MACD 動能向下 + 非超賣區（RSI > 30）
        if rsi_prev > 70 and rsi_cur < rsi_prev and macd_down and rsi_cur > 30:
            stats["做空"]["total"] += 1
            stats["做空"]["vol_sum"] += vol
            if next_price < price:
                stats["做空"]["win"] += 1

        # ---- 抄底條件 ----
        # RSI 超賣回升 + MFI 低位 + MACD 動能向上
        if rsi_prev < 30 and rsi_cur > rsi_prev and mfi_cur < 25 and macd_up:
            stats["抄底"]["total"] += 1
            stats["抄底"]["vol_sum"] += vol
            if next_price > price:
                stats["抄底"]["win"] += 1

        # ---- 追多條件 ----
        # 連續上漲 + RSI 強勢 + MACD 動能向上
        if (closes[ci] > closes[ci - 1] > closes[ci - 2]
                and 60 < rsi_cur < 85 and macd_up):
            stats["追多"]["total"] += 1
            stats["追多"]["vol_sum"] += vol
            if next_price > price:
                stats["追多"]["win"] += 1

    # 計算勝率
    def rate(w, t):
        return round(w / t * 100, 1) if t > 0 else 0.0

    strat_results = {}
    for strat, s in stats.items():
        r = rate(s["win"], s["total"])
        # 取得學習權重
        weight = learner.get_weight(symbol, strat)
        # 綜合分數 = 勝率 × 信心係數 × 學習權重
        # 信心係數：樣本越多越高，用 min(total/10, 1.5) 限制上界
        confidence = min(s["total"] / 10.0, 1.5) if s["total"] >= MIN_SIG else 0
        score = r * confidence * weight
        strat_results[strat] = {
            "rate": r, "total": s["total"], "win": s["win"],
            "confidence": round(confidence, 2),
            "weight": round(weight, 2),
            "score": round(score, 1),
        }

    # 過濾不足樣本
    valid = {k: v for k, v in strat_results.items() if v["total"] >= MIN_SIG}
    if not valid:
        return None

    # 選擇最佳策略（依綜合分數）
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
    bb_pos = "中軌"
    if bb_upper and bb_lower:
        if current_price >= bb_upper[-1]:
            bb_pos = "上軌上方"
        elif current_price <= bb_lower[-1]:
            bb_pos = "下軌下方"
        elif bb_mid and current_price > bb_mid[-1]:
            bb_pos = "中軌上方"
        else:
            bb_pos = "中軌下方"

    # 策略明細字串
    detail_parts = []
    for st in ["做空", "抄底", "追多"]:
        sr = strat_results[st]
        detail_parts.append(f"{st}{sr['rate']}%({sr['total']})")
    detail = " | ".join(detail_parts)

    return {
        "symbol":       symbol,
        "change24h":    change24h,
        "price":        current_price,
        "rsi":          round(current_rsi, 1),
        "mfi":          round(current_mfi, 1),
        "bb_pos":       bb_pos,
        "atr":          round(current_atr, 6),
        # 最佳策略
        "best_strat":   best_strat,
        "best_rate":    best["rate"],
        "best_total":   best["total"],
        "best_score":   best["score"],
        "confidence":   best["confidence"],
        "weight":       best["weight"],
        "detail":       detail,
        # 進出場
        "tp":           tp_price,
        "sl":           sl_price,
        "rr":           rr,
        "tp_pct":       tp_pct,
        "sl_pct":       sl_pct,
        # 所有策略結果（供學習引擎使用）
        "all_strats":   strat_results,
    }


# ============================================================
#  主流程
# ============================================================

def run_scan(learner):
    print(color("=" * 62, 'cyan'))
    print(color(" 幣圈監控 v2 — 自我學習增強版", 'cyan'))
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   學習資料：{learner.total_predictions} 筆預測 / "
          f"{learner.total_validations} 筆驗證")
    print(color("=" * 62, 'cyan'))

    # Step 0：驗證過去的預測
    validated = learner.validate_pending_predictions()
    if validated > 0:
        print(color(f"\n[LEARN] 本輪驗證了 {validated} 筆歷史預測，權重已更新", 'yellow'))

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

    # 去重（避免漲跌幅都在前15%的極端情況）
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

    # Step 2：逐一分析
    print(f"\n[INFO] 開始分析 {len(pool)} 個幣種...")
    results = []
    for idx, coin in enumerate(pool, 1):
        sym = coin["symbol"]
        direction = "+" if coin["change"] > 0 else ""
        print(f"  [{idx:02d}/{len(pool)}] {sym} ({direction}{coin['change']:.1f}%)", end="  ", flush=True)
        klines = fetch_klines(sym)
        if not klines:
            print("跳過（資料不足）")
            continue
        res = analyze(sym, klines, round(coin["change"], 2), learner)
        if res:
            results.append(res)
            print(f"{res['best_strat']} {res['best_rate']}% "
                  f"(score:{res['best_score']}, w:{res['weight']})")
        else:
            print("跳過（訊號不足）")
        time.sleep(0.15)

    if not results:
        print("\n[WARN] 本輪無足夠樣本的幣種，請稍後再試")
        return

    # Step 3：依綜合分數排序
    results.sort(key=lambda x: x["best_score"], reverse=True)
    top = results[:TOP_N]

    # Step 4：記錄預測（供下輪驗證）
    for r in top:
        learner.record_prediction(
            symbol=r["symbol"],
            strategy=r["best_strat"],
            entry_price=r["price"],
            tp_price=r["tp"],
            sl_price=r["sl"],
            rate=r["best_rate"],
            score=r["best_score"],
        )

    # Step 5：輸出結果
    print(color("\n" + "=" * 62, 'yellow'))
    print(color(f" 綜合前 {len(top)} 名（共分析 {len(results)} 個有效幣種）", 'yellow'))
    print(color("=" * 62, 'yellow'))

    medals = ["[1st]", "[2nd]", "[3rd]"]
    for rank, r in enumerate(top):
        chg = f"+{r['change24h']:.2f}%" if r['change24h'] > 0 else f"{r['change24h']:.2f}%"

        print(f"\n{medals[rank]} {color(r['symbol'], 'cyan')}  24h: {chg}")
        print(f"   Price: {r['price']}  |  RSI: {r['rsi']}  |  MFI: {r['mfi']}  |  BB: {r['bb_pos']}")
        print(f"   ATR: {r['atr']}")
        print(f"   策略：{r['best_strat']} {color(str(r['best_rate']) + '%', 'green')}"
              f"（{r['best_total']}次）  Score: {r['best_score']}")
        print(f"   信心: {r['confidence']}  學習權重: {r['weight']}")
        print(f"   明細：{r['detail']}")
        print(f"   進場: {r['price']}  "
              f"止盈: {r['tp']} (+{r['tp_pct']}%)  "
              f"止損: {r['sl']} (-{r['sl_pct']}%)  "
              f"風報比: 1:{r['rr']}")
        print(f"   " + "-" * 56)

    # Step 6：印出學習統計
    learner.print_summary()

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
            run_scan(learner)
            print(f"\n 等待 {INTERVAL} 秒後進行下一輪...")
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        learner.save()
        print(color("\n\n 監控已停止，學習資料已儲存！", 'yellow'))


if __name__ == "__main__":
    main()
