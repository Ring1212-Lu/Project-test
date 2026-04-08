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
# 幣安合約 API（比派網限流寬鬆，價格幾乎一致）
BASE_URL   = "https://fapi.binance.com/fapi/v1/klines"
TICK_URL   = "https://fapi.binance.com/fapi/v1/ticker/24hr"
INTERVAL   = 60        # 秒，standalone 模式用。web_app 改用 SCAN_INTERVAL=45
TOP_N      = 3         # 最終回報前幾名
MIN_SIG    = 5         # 最低訊號次數門檻（提高以確保統計顯著性）
TOP15_PCT  = 0.15      # 每側（漲/跌）取百分比
TOP15_MAX  = 12        # 每側最多取幾個
MAX_WORKERS = 6        # 並行執行緒數
MAX_RETRIES = 3        # API 請求重試次數

# ATR 倍數（自適應止盈止損）
# ATR 倍數（所有策略 RR >= 1.5）
# 做空: 2.25/1.5=1.50, 抄底: 3.0/1.0=3.00, 追多: 2.7/1.5=1.80
# 趨勢做多: 5.0/3.0=1.67, 趨勢做空: 4.5/2.5=1.80
ATR_TP_MULT = {"做空": 2.25, "抄底": 3.0, "追多": 2.7, "做空(寬)": 2.25, "抄底(寬)": 3.0, "趨勢做多": 5.0, "趨勢做空": 4.5}
ATR_SL_MULT = {"做空": 1.5, "抄底": 1.0, "追多": 1.5, "做空(寬)": 1.5, "抄底(寬)": 1.0, "趨勢做多": 3.0, "趨勢做空": 2.5}

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


def obv_trend(obv_vals, lookback=30):
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


def detect_rsi_divergence(closes, rsi_vals, lookback=20):
    """
    偵測 RSI 背離（比閾值交叉更強的反轉信號）
    - 看漲背離：價格創新低，RSI 沒有 → 買入信號
    - 看跌背離：價格創新高，RSI 沒有 → 賣出信號
    回傳: 1=看漲背離, -1=看跌背離, 0=無背離
    """
    if len(closes) < lookback or len(rsi_vals) < lookback:
        return 0

    recent_closes = closes[-lookback:]
    recent_rsi = rsi_vals[-lookback:]

    # 找前半和後半的極值
    mid = lookback // 2
    first_half_c = recent_closes[:mid]
    second_half_c = recent_closes[mid:]
    first_half_r = recent_rsi[:mid]
    second_half_r = recent_rsi[mid:]

    # 看跌背離：價格新高但 RSI 沒新高
    if max(second_half_c) > max(first_half_c) and max(second_half_r) < max(first_half_r):
        return -1

    # 看漲背離：價格新低但 RSI 沒新低
    if min(second_half_c) < min(first_half_c) and min(second_half_r) > min(first_half_r):
        return 1

    return 0


def calc_support_resistance(closes, lookback=50):
    """
    簡易支撐阻力位計算（基於近期高低點聚集）
    回傳 (support, resistance) 價格
    """
    if len(closes) < lookback:
        return None, None

    recent = closes[-lookback:]
    price = closes[-1]

    # 找局部高低點
    highs = []
    lows = []
    for i in range(2, len(recent) - 2):
        if recent[i] > recent[i-1] and recent[i] > recent[i-2] and \
           recent[i] > recent[i+1] and recent[i] > recent[i+2]:
            highs.append(recent[i])
        if recent[i] < recent[i-1] and recent[i] < recent[i-2] and \
           recent[i] < recent[i+1] and recent[i] < recent[i+2]:
            lows.append(recent[i])

    # 支撐 = 低於當前價的最高低點
    support = max([l for l in lows if l < price], default=None)
    # 阻力 = 高於當前價的最低高點
    resistance = min([h for h in highs if h > price], default=None)

    return support, resistance


# ============================================================
#  資料擷取
# ============================================================

def _binance_to_pionex_symbol(binance_sym):
    """幣安格式轉派網格式：BTCUSDT -> BTC_USDT_PERP"""
    if binance_sym.endswith("USDT"):
        base = binance_sym[:-4]
        return f"{base}_USDT_PERP"
    return binance_sym

def _pionex_to_binance_symbol(pionex_sym):
    """派網格式轉幣安格式：BTC_USDT_PERP -> BTCUSDT"""
    return pionex_sym.replace("_USDT_PERP", "USDT").replace("_", "")


def fetch_tickers():
    """從幣安合約 API 抓取行情，轉換成相容格式（帶 3 次重試）"""
    for attempt in range(3):
        try:
            r = _throttled_get(TICK_URL, timeout=15)
            if r.status_code == 200:
                data = r.json()
                tickers = []
                for t in data:
                    sym = t.get("symbol", "")
                    if not sym.endswith("USDT"):
                        continue
                    tickers.append({
                        "symbol": _binance_to_pionex_symbol(sym),
                        "open": t.get("openPrice", "0"),
                        "close": t.get("lastPrice", "0"),
                        "amount": t.get("quoteVolume", "0"),  # USDT 成交額
                    })
                return tickers
            print(f"  [WARN] fetch_tickers HTTP {r.status_code} (attempt {attempt+1})")
        except Exception as e:
            print(f"  [ERROR] 行情抓取失敗: {type(e).__name__}: {e} (attempt {attempt+1})")
        if attempt < 2:
            time.sleep(2 ** attempt)  # 1s, 2s backoff
    return []


def _create_session():
    """建立帶重試機制的 requests Session"""
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    })
    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=3,         # 3s, 6s, 12s（429 時等更久再重試）
        status_forcelist=[500, 502, 503, 504],  # 429 不自動重試，由節流控制
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# 全域 Session（連線池復用，避免反覆握手）
_session = _create_session()

# Token Bucket 限速器（允許真正的並發）
import threading

class RateLimiter:
    """Token Bucket 限速器，允許真正的並發"""
    def __init__(self, max_per_second=5):
        self._lock = threading.Lock()
        self._tokens = max_per_second
        self._max = max_per_second
        self._last_refill = time.time()

    def acquire(self):
        while True:
            with self._lock:
                now = time.time()
                elapsed = now - self._last_refill
                self._tokens = min(self._max, self._tokens + elapsed * self._max)
                self._last_refill = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
            time.sleep(0.05)

_rate_limiter = RateLimiter(max_per_second=5)
_last_api_call = 0

def _throttled_get(url, **kwargs):
    """帶限速的 GET 請求，遇到 429 自動等待重試（3 次）"""
    for attempt in range(3):
        _rate_limiter.acquire()
        try:
            r = _session.get(url, **kwargs)
            if r.status_code != 429:
                return r
            retry_after = int(r.headers.get("Retry-After", 30))
            print(f"  [WARN] 429 Rate limited, wait {retry_after}s (attempt {attempt+1})")
            time.sleep(retry_after)
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    return r


def fetch_klines(symbol, interval="1M", limit=500):
    """從幣安合約 API 抓取 K 線，轉換成相容格式"""
    # 轉換符號格式
    binance_sym = _pionex_to_binance_symbol(symbol)
    # 轉換時間框架格式：1M -> 1m, 5M -> 5m
    binance_interval = interval.lower()

    try:
        r = _throttled_get(BASE_URL, params={
            "symbol": binance_sym, "interval": binance_interval, "limit": limit
        }, timeout=15)
        if r.status_code == 200:
            raw = r.json()
            if isinstance(raw, list) and len(raw) >= 30:
                # 幣安格式: [time, open, high, low, close, volume, ...]
                # 轉成派網相容格式: {"open":, "close":, "high":, "low":, "volume":}
                klines = []
                for k in raw:
                    klines.append({
                        "open": k[1],
                        "high": k[2],
                        "low": k[3],
                        "close": k[4],
                        "volume": k[5],
                    })
                return klines
    except Exception as e:
        print(f"    [WARN] {symbol}: {type(e).__name__}")
    return []


def get_btc_trend():
    """
    取得 BTC 趨勢方向（大盤過濾器）
    回傳: 1=上漲, -1=下跌, 0=震盪
    """
    klines = fetch_klines("BTC_USDT_PERP", interval="5M", limit=30)
    if not klines:
        return 0
    closes = [float(k['close']) for k in klines]
    if len(closes) < 20:
        return 0

    # 用 EMA 20 判斷趨勢
    ema20 = calc_ema(closes, 20)
    if not ema20:
        return 0

    price = closes[-1]
    ema = ema20[-1]
    # 價格在 EMA 上方且 EMA 上升 = 上漲
    if price > ema and ema20[-1] > ema20[-3]:
        return 1
    elif price < ema and ema20[-1] < ema20[-3]:
        return -1
    return 0


def get_higher_tf_trend(symbol):
    """
    取得 5 分鐘線趨勢（多時間框架確認）
    回傳: 1=上漲, -1=下跌, 0=不明
    """
    klines = fetch_klines(symbol, interval="5M", limit=30)
    if not klines:
        return 0
    closes = [float(k['close']) for k in klines]
    if len(closes) < 15:
        return 0

    rsi = calc_rsi_wilder(closes, 14)
    ema_fast = calc_ema(closes, 8)
    ema_slow = calc_ema(closes, 21)

    if not rsi or not ema_fast or not ema_slow:
        return 0

    # EMA 金叉 + RSI > 50 = 多頭
    if ema_fast[-1] > ema_slow[-1] and rsi[-1] > 50:
        return 1
    elif ema_fast[-1] < ema_slow[-1] and rsi[-1] < 50:
        return -1
    return 0


def fetch_and_analyze(coin, learner, opt_params=None, btc_trend=0):
    """單一幣種：抓取 + 分析（供並行使用）"""
    sym = coin["symbol"]
    klines = fetch_klines(sym)
    if not klines:
        return None
    # 多時間框架確認
    htf_trend = get_higher_tf_trend(sym)
    # 使用 ticker 即時價格（比 K 線收盤價更即時）
    realtime_price = coin.get("price", None)
    return analyze(sym, klines, round(coin["change"], 2), learner, opt_params,
                   btc_trend=btc_trend, htf_trend=htf_trend,
                   realtime_price=realtime_price)


# ============================================================
#  核心分析
# ============================================================

def analyze(symbol, klines, change24h, learner, opt_params=None,
            btc_trend=0, htf_trend=0, realtime_price=None):
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

    # 連續下跌根數（用於抄底保護）
    consec_down = 0
    for j in range(len(closes) - 1, 0, -1):
        if closes[j] < closes[j - 1]:
            consec_down += 1
        else:
            break

    # RSI 背離偵測
    divergence = detect_rsi_divergence(closes, rsi_vals)

    # 支撐阻力位
    support, resistance = calc_support_resistance(closes)

    # 對齊索引
    RSI_PERIOD = 14
    RSI_OFF = RSI_PERIOD + 1

    length = min(len(rsi_vals), len(mfi_vals))

    # 使用優化參數（如果有的話）
    rsi_short_th = 70
    rsi_long_th = 35
    hold_period = 15
    if opt_params:
        rsi_short_th = opt_params.get("rsi_short_thresh", 70)
        rsi_long_th = opt_params.get("rsi_long_thresh", 35)
        hold_period = opt_params.get("best_hold_period", 15)

    # 統計計數
    stats = {
        "做空": {"win": 0, "total": 0, "vol_sum": 0},
        "抄底": {"win": 0, "total": 0, "vol_sum": 0},
        "追多": {"win": 0, "total": 0, "vol_sum": 0},
        "做空(寬)": {"win": 0, "total": 0, "vol_sum": 0},
        "抄底(寬)": {"win": 0, "total": 0, "vol_sum": 0},
    }

    # ATR for backtest TP/SL simulation
    atr_for_bt = calc_atr(klines)
    # 預提取 high/low 備用（close 為主要判定價格，對齊實盤 lastPrice 離散取樣）
    bt_highs = [float(k.get('high', k.get('close', 0))) for k in klines]
    bt_lows = [float(k.get('low', k.get('close', 0))) for k in klines]
    BT_FEE_RATE = 0.0015  # 統一：0.05% taker × 2 sides + 0.05% slippage
    BT_BAR_SECONDS = 300  # 5min per bar
    BT_MAX_AGE = 7200     # 2h timeout（對齊實盤 MAX_POSITION_AGE）
    BT_BREAKEVEN_MARGIN = 0.003  # 保本止損 margin（對齊實盤 0.3%）

    # 抄底專用 hold_period（超賣反彈需要更長醞釀時間）
    chaodi_hold = 30

    def _bt_check_win(strat_name, entry_price, bar_start, is_short_side):
        """回測 TP/SL 判定（對齊實盤邏輯：close 取樣、超時、保本止損、SL floor）"""
        tp_mult = ATR_TP_MULT.get(strat_name, 2.0)
        sl_mult = ATR_SL_MULT.get(strat_name, 1.5)
        atr_idx = bar_start - 14
        if atr_idx < 0 or atr_idx >= len(atr_for_bt):
            atr_val = entry_price * 0.02
        else:
            atr_val = atr_for_bt[atr_idx]

        # Fee-adjusted TP/SL distances
        fee_cost = entry_price * BT_FEE_RATE
        tp_dist = atr_val * tp_mult - fee_cost
        sl_dist = atr_val * sl_mult + fee_cost

        # SL floor/cap（對齊實盤 SL% 1.5%-15%）
        sl_pct = sl_dist / entry_price * 100 if entry_price > 0 else 0
        if sl_pct < 1.5:
            sl_dist = entry_price * 0.015
        elif sl_pct > 15.0:
            sl_dist = entry_price * 0.15
        # TP 保持原始 RR 比例
        raw_rr = tp_mult / sl_mult if sl_mult > 0 else 2.0
        tp_dist = sl_dist * raw_rr

        # 抄底使用更長的 hold_period（但仍受超時限制）
        hp = chaodi_hold if "抄底" in strat_name else hold_period

        # 保本止損追蹤
        trailing_sl_active = False
        elapsed = 0

        for j in range(1, hp + 1):
            idx = bar_start + j
            if idx >= len(closes):
                break

            # 用 close 做判定（對齊實盤 lastPrice 離散取樣）
            bar_price = closes[idx]
            elapsed += BT_BAR_SECONDS

            # 2h 超時強平（對齊實盤 MAX_POSITION_AGE=7200s）
            if elapsed >= BT_MAX_AGE:
                if is_short_side:
                    return bar_price < entry_price - fee_cost
                else:
                    return bar_price > entry_price + fee_cost

            if is_short_side:
                # 保本止損：盈利 >= 1 ATR 時 SL 移至 entry - 0.3%
                profit_dist = entry_price - bar_price
                if profit_dist >= atr_val and not trailing_sl_active:
                    trailing_sl_active = True

                # SL 判定（用保本 SL 或原始 SL，取較緊者）
                if trailing_sl_active:
                    breakeven_sl = entry_price * (1 - BT_BREAKEVEN_MARGIN)
                    if bar_price >= breakeven_sl:
                        return True  # 保本出場（微利）
                if bar_price >= entry_price + sl_dist:
                    return False
                if bar_price <= entry_price - tp_dist:
                    return True
            else:
                # 保本止損：盈利 >= 1 ATR 時 SL 移至 entry + 0.3%
                profit_dist = bar_price - entry_price
                if profit_dist >= atr_val and not trailing_sl_active:
                    trailing_sl_active = True

                if trailing_sl_active:
                    breakeven_sl = entry_price * (1 + BT_BREAKEVEN_MARGIN)
                    if bar_price <= breakeven_sl:
                        return True  # 保本出場（微利）
                if bar_price <= entry_price - sl_dist:
                    return False
                if bar_price >= entry_price + tp_dist:
                    return True

        # Neither hit within timeout: check final price (minus fees)
        final_idx = min(bar_start + hp, len(closes) - 1)
        if is_short_side:
            return closes[final_idx] < entry_price - fee_cost
        else:
            return closes[final_idx] > entry_price + fee_cost

    max_hold = max(hold_period, chaodi_hold)
    for i in range(6, length - max_hold):
        ci = i + RSI_OFF
        if ci + max_hold >= len(closes) or ci < 2:
            continue
        # MACD 安全索引
        if ci >= len(macd_hist) or ci - 1 < 0:
            continue

        price      = closes[ci]
        rsi_prev   = rsi_vals[i - 1] if i - 1 >= 0 else 50
        rsi_cur    = rsi_vals[i]
        mfi_cur    = mfi_vals[i] if i < len(mfi_vals) else 50

        macd_down = macd_hist[ci] < macd_hist[ci - 1]
        macd_up   = macd_hist[ci] > macd_hist[ci - 1]

        try:
            vol = volumes[ci] if ci < len(volumes) else 1.0
        except IndexError:
            vol = 1.0

        # ---- 做空條件（嚴格版，使用優化閾值 + MFI>60 過濾）----
        if rsi_prev > rsi_short_th and rsi_cur < rsi_prev and macd_down and mfi_cur > 60:
            stats["做空"]["total"] += 1
            stats["做空"]["vol_sum"] += vol
            if _bt_check_win("做空", price, ci, is_short_side=True):
                stats["做空"]["win"] += 1

        # ---- 做空條件（寬鬆版：RSI 門檻 -10）----
        if rsi_prev > (rsi_short_th - 10) and rsi_cur < rsi_prev and macd_down and mfi_cur > 60:
            stats["做空(寬)"]["total"] += 1
            stats["做空(寬)"]["vol_sum"] += vol
            if _bt_check_win("做空(寬)", price, ci, is_short_side=True):
                stats["做空(寬)"]["win"] += 1

        # ---- 抄底 v2：多信號評分制 ----
        chaodi_score = 0

        # (1) RSI 極度超賣：<30 = +2, <35 = +1
        if rsi_cur < 30:
            chaodi_score += 2
        elif rsi_cur < 35:
            chaodi_score += 1

        # (2) MFI 資金流枯竭：<30 = +2, <40 = +1
        if mfi_cur < 30:
            chaodi_score += 2
        elif mfi_cur < 40:
            chaodi_score += 1

        # (3) OBV 量能枯竭後回升（先跌後回升 = 賣壓耗盡）
        _bt_obv_exhaustion = (ci >= 4 and ci < len(obv_vals) and
                              obv_vals[ci - 3] > obv_vals[ci - 1] and  # 之前在跌
                              obv_vals[ci] > obv_vals[ci - 1])          # 現在回升
        if _bt_obv_exhaustion:
            chaodi_score += 2

        # (4) 布林下軌觸及或突破
        _bb_idx = ci - (len(closes) - len(bb_lower)) if bb_lower else -1
        _bb_touch = (_bb_idx >= 0 and _bb_idx < len(bb_lower) and
                     bb_lower[_bb_idx] > 0 and
                     closes[ci] <= bb_lower[_bb_idx] * 1.005)
        if _bb_touch:
            chaodi_score += 2

        # (5) RSI 看漲背離（價格新低但 RSI 沒有）
        if ci >= 20:
            _local_closes = closes[ci - 20:ci + 1]
            _local_rsi_offset = ci - (len(closes) - len(rsi_vals))
            if _local_rsi_offset >= 20:
                _local_rsi = rsi_vals[_local_rsi_offset - 20:_local_rsi_offset + 1]
                if len(_local_closes) >= 20 and len(_local_rsi) >= 20:
                    _mid = 10
                    if (min(_local_closes[_mid:]) < min(_local_closes[:_mid]) and
                            min(_local_rsi[_mid:]) > min(_local_rsi[:_mid])):
                        chaodi_score += 3  # 看漲背離 = 強烈底部信號

        # (6) 放量（當前量 > 20根平均量 × 1.5）
        _avg_vol = mean(volumes[max(0, ci - 20):ci]) if ci >= 20 else vol
        if _avg_vol > 0 and vol > _avg_vol * 1.5:
            chaodi_score += 1

        # (7) Pump-dump 過濾：20根內最高價/當前價 < 1.5
        _peak_20 = max(closes[max(0, ci - 20):ci + 1])
        _not_post_pump = (_peak_20 / price) < 1.5 if price > 0 else True

        # (8) 連跌保護（≥4 根不抄底）
        _bt_consec = 0
        for _j in range(ci, max(ci - 5, 0), -1):
            if _j > 0 and closes[_j] < closes[_j - 1]:
                _bt_consec += 1
            else:
                break

        # 嚴格版：需 ≥ 6 分 + RSI 反彈 + MACD 向上 + 非 pump-dump + 連跌<4
        if (chaodi_score >= 6 and rsi_cur > rsi_prev and macd_up
                and _not_post_pump and _bt_consec < 4):
            stats["抄底"]["total"] += 1
            stats["抄底"]["vol_sum"] += vol
            if _bt_check_win("抄底", price, ci, is_short_side=False):
                stats["抄底"]["win"] += 1

        # 寬鬆版：需 ≥ 4 分
        if (chaodi_score >= 4 and rsi_cur > rsi_prev and macd_up
                and _not_post_pump and _bt_consec < 4):
            stats["抄底(寬)"]["total"] += 1
            stats["抄底(寬)"]["vol_sum"] += vol
            if _bt_check_win("抄底(寬)", price, ci, is_short_side=False):
                stats["抄底(寬)"]["win"] += 1

        # ---- 追多條件（放寬：連漲2根、去MFI>30、OBV lookback 3）----
        obv_rising = (ci >= 3 and ci < len(obv_vals) and
                      obv_vals[ci] > obv_vals[ci - 3])
        if (closes[ci] > closes[ci - 1]
                and ci >= 2 and closes[ci - 1] > closes[ci - 2]
                and 60 < rsi_cur < 80 and macd_up
                and obv_rising):
            stats["追多"]["total"] += 1
            stats["追多"]["vol_sum"] += vol
            if _bt_check_win("追多", price, ci, is_short_side=False):
                stats["追多"]["win"] += 1

    # 計算勝率與綜合分數
    def rate(w, t):
        return round(w / t * 100, 1) if t > 0 else 0.0

    strat_results = {}
    for strat, s in stats.items():
        r = rate(s["win"], s["total"])
        # 寬鬆版使用對應嚴格版的學習權重
        base_strat = strat.replace("(寬)", "").strip() if "(寬)" in strat else strat
        is_short = base_strat == "做空"
        is_long = base_strat in ("抄底", "追多")

        weight = learner.get_weight(symbol, base_strat)

        # === 貝葉斯收縮（取代舊 confidence 膨脹）===
        # 少樣本時拉向 50%（無信息先驗），多樣本時保持觀測值
        # adj_rate = 50 + (rate - 50) * n / (n + k)，k=20 為等效樣本數
        BAYES_K = 20
        n = s["total"]
        adj_rate = 50.0 + (r - 50.0) * n / (n + BAYES_K) if n >= MIN_SIG else 50.0

        # === 期望值 EV（取代單純勝率）===
        # EV = WR * avg_win_pct - (1-WR) * avg_loss_pct - fee
        # 使用 ATR 乘數估算 avg_win/avg_loss 百分比
        tp_mult = ATR_TP_MULT.get(strat, 2.0)
        sl_mult = ATR_SL_MULT.get(strat, 1.5)
        fee_pct = 0.2  # 0.1% 手續費 + 0.1% 滑點
        wr_dec = adj_rate / 100.0
        _atr_est = atr_vals[-1] if atr_vals else closes[-1] * 0.02
        _atr_pct = _atr_est / closes[-1] * 100 if closes[-1] > 0 else 1.0
        ev = wr_dec * tp_mult - (1 - wr_dec) * sl_mult - fee_pct / _atr_pct

        # === 綜合分數：EV 驅動 + 輕量修正 ===
        # 不再使用 regime_bonus / obv_bonus / env_multiplier 的連乘
        # 只保留 weight（學習引擎回饋）和 global_penalty（全局勝率修正）

        # 全局歷史勝率修正
        global_wr, global_n = learner.get_global_strat_winrate(strat)
        global_penalty = 1.0
        if global_wr is not None and global_n >= 30:
            if global_wr < 40:
                global_penalty = 0.6
            elif global_wr < 45:
                global_penalty = 0.8

        # Regime 調整：數據驅動（>= 30 筆才生效），冷啟動期預設 ranging=0.85
        regime_adj = learner.get_regime_adj(regime, base_strat)
        if regime_adj == 1.0 and regime == "ranging":
            regime_adj = 0.85  # 冷啟動 fallback

        # 最終分數 = adj_rate（貝葉斯校正勝率）× weight × global × regime_adj
        score = adj_rate * weight * global_penalty * regime_adj

        # 舊因子留作記錄但不參與計分
        regime_bonus = learner.get_regime_bonus(regime, base_strat)
        obv_bonus = 1.0
        recent_bonus = 1.0
        env_multiplier = 1.0

        strat_results[strat] = {
            "rate": r, "total": s["total"], "win": s["win"],
            "adj_rate": round(adj_rate, 1),
            "ev": round(ev, 3),
            "confidence": round(n / (n + BAYES_K), 2),  # 貝葉斯收縮係數（0~1）
            "weight": round(weight, 2),
            "regime_bonus": round(regime_bonus, 2),
            "obv_bonus": round(obv_bonus, 2),
            "recent_bonus": round(recent_bonus, 2),
            "env_multiplier": round(env_multiplier, 2),
            "global_penalty": round(global_penalty, 2),
            "env_detail": f"regime_adj={regime_adj}",
            "score": round(score, 1),
        }

    # 過濾不足樣本（交易用：只看嚴格版三個策略）
    core_strats = {"做空", "抄底", "追多"}
    valid = {k: v for k, v in strat_results.items() if v["total"] >= MIN_SIG and k in core_strats}
    if not valid:
        return None

    best_strat = max(valid, key=lambda k: valid[k]["score"])
    best = valid[best_strat]

    # === 做多策略即時安全檢查 ===
    # 下跌趨勢中所有做多策略（抄底、追多）都應被攔截
    if best_strat == "追多" and regime == "trending_down":
        print(f"  [BLOCK] {symbol}: 追多被攔截 — 市場處於下跌趨勢，不適合做多")
        valid.pop("追多", None)
        if not valid:
            return None
        best_strat = max(valid, key=lambda k: valid[k]["score"])
        best = valid[best_strat]

    # 抄底 v2 即時安全檢查（多層防護）
    if best_strat == "抄底":
        blocked = False
        block_reason = ""

        # Pump-dump 過濾：24h 漲幅 > 30% 的幣不抄底
        if change24h > 30:
            blocked = True
            block_reason = f"24h漲幅{change24h:+.1f}%，暴漲回落非超賣"
        # 連跌 ≥4 根：賣壓未竭
        elif consec_down >= 4:
            blocked = True
            block_reason = f"連跌 {consec_down} 根K線，賣壓未竭"
        # trending_down 時需 RSI < 25 才允許（極度超賣才可）
        elif regime == "trending_down":
            current_rsi_check = rsi_vals[-1] if rsi_vals else 50
            if current_rsi_check >= 25:
                blocked = True
                block_reason = f"trending_down + RSI={current_rsi_check:.1f}≥25，超賣不夠深"
        # 布林帶位置檢查：不在 lower- 區域不抄底
        if not blocked and bb_lower and bb_upper and bb_mid and closes:
            if closes[-1] > bb_mid[-1]:
                blocked = True
                block_reason = "價格在布林中軌上方，不是超賣區域"

        if blocked:
            print(f"  [BLOCK] {symbol}: 抄底被攔截 — {block_reason}")
            # 移除抄底，嘗試用次佳策略
            valid.pop("抄底", None)
            if not valid:
                return None
            best_strat = max(valid, key=lambda k: valid[k]["score"])
            best = valid[best_strat]

    # 計算當前 bar 的 chaodi_score（用於前端顯示/通知透明度）
    rt_chaodi_score = 0
    if best_strat == "抄底" and len(closes) >= 2 and rsi_vals and mfi_vals:
        _rt_rsi = rsi_vals[-1] if rsi_vals else 50
        _rt_mfi = mfi_vals[-1] if mfi_vals else 50
        if _rt_rsi < 30: rt_chaodi_score += 2
        elif _rt_rsi < 35: rt_chaodi_score += 1
        if _rt_mfi < 30: rt_chaodi_score += 2
        elif _rt_mfi < 40: rt_chaodi_score += 1
        # OBV exhaustion (先跌後回升：-4 > -2 且 -1 > -2，與回測 ci-3/ci-1/ci 對齊)
        if (len(obv_vals) >= 4 and
                obv_vals[-4] > obv_vals[-2] and obv_vals[-1] > obv_vals[-2]):
            rt_chaodi_score += 2
        # BB lower touch
        if bb_lower and bb_lower[-1] > 0 and closes[-1] <= bb_lower[-1] * 1.005:
            rt_chaodi_score += 2
        # RSI divergence (use same 20-bar window)
        if len(closes) >= 21 and len(rsi_vals) >= 21:
            _lc = closes[-21:]
            _lr = rsi_vals[-21:]
            _m = 10
            if min(_lc[_m:]) < min(_lc[:_m]) and min(_lr[_m:]) > min(_lr[:_m]):
                rt_chaodi_score += 3
        # Volume spike
        if len(volumes) >= 21 and volumes[-1] > mean(volumes[-21:-1]) * 1.5:
            rt_chaodi_score += 1

    # 優先使用 ticker 即時價格，K 線收盤價作為備用
    current_price = realtime_price if realtime_price else closes[-1]
    current_rsi   = rsi_vals[-1] if rsi_vals else 50
    current_mfi   = mfi_vals[-1] if mfi_vals else 50

    # ATR 自適應止盈止損
    current_atr = atr_vals[-1] if atr_vals else current_price * 0.02
    tp_dist = current_atr * ATR_TP_MULT[best_strat]
    sl_dist = current_atr * ATR_SL_MULT[best_strat]

    # SL% 下限 1.5%、上限 15%（防止微型幣 ATR 過小被噪音掃損）
    sl_pct_raw = sl_dist / current_price * 100 if current_price > 0 else 0
    SL_FLOOR_PCT, SL_CAP_PCT = 1.5, 15.0
    if sl_pct_raw < SL_FLOOR_PCT:
        sl_dist = current_price * SL_FLOOR_PCT / 100
    elif sl_pct_raw > SL_CAP_PCT:
        sl_dist = current_price * SL_CAP_PCT / 100

    # TP 也相應調整：保持原始 RR 比例
    raw_rr = ATR_TP_MULT[best_strat] / ATR_SL_MULT[best_strat]
    tp_dist = sl_dist * raw_rr

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

    # 策略明細（嚴格版）
    detail_parts = []
    for st in ["做空", "抄底", "追多"]:
        sr = strat_results[st]
        detail_parts.append(f"{st}{sr['rate']}%({sr['total']})")
    detail = " | ".join(detail_parts)

    # 寬鬆版明細（相同順序：做空、抄底，追多無寬鬆版留空）
    sr_short_r = strat_results["做空(寬)"]
    sr_bottom_r = strat_results["抄底(寬)"]
    relaxed_detail = (f"做空{sr_short_r['rate']}%({sr_short_r['total']}) | "
                      f"抄底{sr_bottom_r['rate']}%({sr_bottom_r['total']})")

    # 信號強度等級
    score_val = best["score"]
    if score_val >= 120:
        signal_strength = "STRONG"
    elif score_val >= 80:
        signal_strength = "MEDIUM"
    else:
        signal_strength = "WEAK"

    # Kelly 公式倉位建議
    win_rate_dec = best["rate"] / 100
    avg_win_loss = abs(tp_dist / sl_dist) if sl_dist > 0 else 1
    kelly_pct = 0
    if avg_win_loss > 0:
        kelly_pct = max(0, win_rate_dec - (1 - win_rate_dec) / avg_win_loss)
        kelly_pct = min(kelly_pct * 100, 25)  # 上限 25%（半 Kelly）
    kelly_pct = round(kelly_pct, 1)

    # 背離描述
    div_str = {1: "bullish", -1: "bearish", 0: "none"}.get(divergence, "none")

    # 支撐阻力
    sr_str = ""
    if support:
        sr_str += f"S:{round(support, 6)} "
    if resistance:
        sr_str += f"R:{round(resistance, 6)}"
    if not sr_str:
        sr_str = "N/A"

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
        "divergence":     div_str,
        "btc_trend":      btc_trend,
        "htf_trend":      htf_trend,
        "support_resistance": sr_str,
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
        "env_multiplier": best.get("env_multiplier", 1.0),
        "env_detail":     best.get("env_detail", ""),
        "signal_strength": signal_strength,
        "ev":             best.get("ev", 0),
        "chaodi_score":   rt_chaodi_score if best_strat == "抄底" else None,
        "detail":         detail,
        "relaxed_detail":  relaxed_detail,
        "kelly_pct":      kelly_pct,
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
#  趨勢策略（4H K線，3-7天持倉）
# ============================================================

def fetch_trend_candidates(tickers, min_volume=10_000_000):
    """篩選日成交量 > 1000萬U 的主流幣做趨勢分析"""
    candidates = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("_USDT_PERP"):
            continue
        try:
            vol = float(t.get("amount", 0))
            c = float(t.get("close", 0))
            o = float(t.get("open", 0))
            if vol >= min_volume and c > 0 and o > 0:
                change = (c - o) / o * 100
                candidates.append({"symbol": sym, "change": change, "price": c, "volume": vol})
        except (ValueError, ZeroDivisionError):
            continue
    candidates.sort(key=lambda x: x["volume"], reverse=True)
    return candidates[:20]  # Top 20 by volume


def analyze_trend(symbol, klines_4h, change24h, learner, btc_trend=0, klines_1h=None):
    """
    趨勢策略分析（4H 定向 + 1H 入場確認）
    策略：趨勢做多 / 趨勢做空
    持倉期：3-7天（18根4H K線 ≈ 3天）
    """
    closes = [float(k['close']) for k in klines_4h if float(k.get('close', 0)) > 0]
    if len(closes) < 60:
        return None

    volumes = []
    for k in klines_4h:
        try:
            volumes.append(float(k.get('volume', 0)))
        except (ValueError, TypeError):
            volumes.append(0)

    # 計算指標
    ema20 = calc_ema(closes, 20)
    ema50 = calc_ema(closes, 50)
    rsi_vals = calc_rsi_wilder(closes, 14)
    obv_vals = calc_obv(closes, volumes[:len(closes)])
    atr_vals = calc_atr(klines_4h)

    if not ema20 or not ema50 or not rsi_vals or len(ema20) < 2 or len(ema50) < 2:
        return None

    current_price = closes[-1]
    current_ema20 = ema20[-1]
    current_ema50 = ema50[-1]
    current_rsi = rsi_vals[-1] if rsi_vals else 50
    current_atr = atr_vals[-1] if atr_vals else current_price * 0.02

    # OBV 趨勢
    obv_dir = obv_trend(obv_vals, lookback=10)

    # Volume vs 20-period average
    vol_avg_20 = mean(volumes[-20:]) if len(volumes) >= 20 else mean(volumes) if volumes else 1
    current_vol = volumes[-1] if volumes else 0
    vol_above_avg = current_vol > vol_avg_20

    # 市場狀態偵測
    regime = MarketRegime.detect(closes)

    hold_period = 18  # ≈3 days of 4h candles
    # 趨勢回測對齊實盤常數
    TREND_BT_BAR_SEC = 14400          # 4h per bar
    TREND_BT_MAX_AGE = 7 * 24 * 3600  # 7 days（對齊 MAX_TREND_POSITION_AGE）
    TREND_BT_FEE_RATE = 0.0015        # 統一手續費
    TREND_BT_TRAIL_TRIGGER = 0.05     # 5% 利潤觸發（對齊 TREND_TRAILING_TRIGGER）
    TREND_BT_TRAIL_STEP = 0.03        # 3% 回撤止損（對齊 TREND_TRAILING_STEP）

    # 回測驗證
    stats = {
        "趨勢做多": {"win": 0, "total": 0},
        "趨勢做空": {"win": 0, "total": 0},
    }

    def _trend_bt_check(price_i, atr_i, is_short, bar_start):
        """趨勢回測 TP/SL 判定（對齊實盤：trailing stop、手續費、SL floor、超時）"""
        strat_name = "趨勢做空" if is_short else "趨勢做多"
        tp_mult = ATR_TP_MULT[strat_name]
        sl_mult = ATR_SL_MULT[strat_name]
        fee_cost = price_i * TREND_BT_FEE_RATE
        tp_dist = atr_i * tp_mult - fee_cost
        sl_dist = atr_i * sl_mult + fee_cost

        # SL floor/cap（對齊實盤 1.5%-15%）
        sl_pct = sl_dist / price_i * 100 if price_i > 0 else 0
        if sl_pct < 1.5:
            sl_dist = price_i * 0.015
        elif sl_pct > 15.0:
            sl_dist = price_i * 0.15
        raw_rr = tp_mult / sl_mult if sl_mult > 0 else 1.5
        tp_dist = sl_dist * raw_rr

        trailing_sl = None
        peak_profit_pct = 0
        elapsed = 0

        for j in range(1, hold_period + 1):
            idx = bar_start + j
            if idx >= len(closes):
                break
            fp = closes[idx]
            elapsed += TREND_BT_BAR_SEC

            # 超時強平
            if elapsed >= TREND_BT_MAX_AGE:
                if is_short:
                    return fp < price_i - fee_cost
                else:
                    return fp > price_i + fee_cost

            if is_short:
                profit_pct = (price_i - fp) / price_i
                # Trailing stop：利潤 >= 5% 時啟動
                if profit_pct > TREND_BT_TRAIL_TRIGGER:
                    new_sl = fp * (1 + TREND_BT_TRAIL_STEP)
                    if trailing_sl is None or new_sl < trailing_sl:
                        trailing_sl = new_sl
                # 檢查 trailing SL
                if trailing_sl is not None and fp >= trailing_sl:
                    return True  # trailing stop 觸發（有利潤）
                if fp >= price_i + sl_dist:
                    return False
                if fp <= price_i - tp_dist:
                    return True
            else:
                profit_pct = (fp - price_i) / price_i
                if profit_pct > TREND_BT_TRAIL_TRIGGER:
                    new_sl = fp * (1 - TREND_BT_TRAIL_STEP)
                    if trailing_sl is None or new_sl > trailing_sl:
                        trailing_sl = new_sl
                if trailing_sl is not None and fp <= trailing_sl:
                    return True
                if fp <= price_i - sl_dist:
                    return False
                if fp >= price_i + tp_dist:
                    return True

        # 超時：用最後收盤價判定
        final_idx = min(bar_start + hold_period, len(closes) - 1)
        if is_short:
            return closes[final_idx] < price_i - fee_cost
        else:
            return closes[final_idx] > price_i + fee_cost

    for i in range(60, len(closes) - hold_period):
        if i >= len(ema20) or i >= len(ema50):
            continue
        e20 = ema20[i]
        e50 = ema50[i]
        price_i = closes[i]

        # RSI index alignment
        rsi_offset = len(closes) - len(rsi_vals)
        rsi_idx = i - rsi_offset
        if rsi_idx < 0 or rsi_idx >= len(rsi_vals):
            continue
        rsi_i = rsi_vals[rsi_idx]

        # ATR at this bar for adaptive TP/SL
        atr_offset = len(closes) - len(atr_vals)
        atr_idx = i - atr_offset
        atr_i = atr_vals[atr_idx] if 0 <= atr_idx < len(atr_vals) else price_i * 0.02

        # 趨勢做多
        if e20 > e50 and price_i > e20 and 45 <= rsi_i <= 75:
            stats["趨勢做多"]["total"] += 1
            if _trend_bt_check(price_i, atr_i, is_short=False, bar_start=i):
                stats["趨勢做多"]["win"] += 1

        # 趨勢做空
        if e20 < e50 and price_i < e20 and 30 <= rsi_i <= 50:
            stats["趨勢做空"]["total"] += 1
            if _trend_bt_check(price_i, atr_i, is_short=True, bar_start=i):
                stats["趨勢做空"]["win"] += 1

    # 計算分數
    def rate(w, t):
        return round(w / t * 100, 1) if t > 0 else 0.0

    best_strat = None
    best_score = 0
    best_rate_val = 0
    best_total = 0
    best_ev = 0

    for strat, s in stats.items():
        if s["total"] < 3:
            continue

        r = rate(s["win"], s["total"])
        is_long = (strat == "趨勢做多")

        # Check current conditions
        if is_long:
            if not (current_ema20 > current_ema50 and current_price > current_ema20
                    and 45 <= current_rsi <= 75):
                continue
        else:
            if not (current_ema20 < current_ema50 and current_price < current_ema20
                    and 30 <= current_rsi <= 50):
                continue
            # 趨勢做空需要 BTC 大盤非上漲（下跌或中性皆可）
            if btc_trend == 1:
                continue

        weight = learner.get_weight(symbol, strat)

        # 貝葉斯收縮（與短線策略一致）
        BAYES_K = 20
        n = s["total"]
        adj_rate = 50.0 + (r - 50.0) * n / (n + BAYES_K) if n >= 3 else 50.0

        # EV 計算
        tp_mult = ATR_TP_MULT[strat]
        sl_mult = ATR_SL_MULT[strat]
        _atr_pct = current_atr / current_price * 100 if current_price > 0 else 1.0
        wr_dec = adj_rate / 100.0
        ev = wr_dec * tp_mult - (1 - wr_dec) * sl_mult - 0.15 / _atr_pct

        # Regime 調整
        regime_adj = learner.get_regime_adj(regime, strat)
        if regime_adj == 1.0 and regime == "ranging":
            regime_adj = 0.85

        score = adj_rate * weight * regime_adj

        if score > best_score:
            best_score = score
            best_strat = strat
            best_rate_val = r
            best_total = s["total"]
            best_ev = round(ev, 3)

    if not best_strat:
        return None

    # ── 1H 入場時機確認（4H 定向通過後，用 1H 精確入場）──
    entry_timing = "4H_ONLY"
    if klines_1h and len(klines_1h) >= 30:
        closes_1h = [float(k['close']) for k in klines_1h if float(k.get('close', 0)) > 0]
        if len(closes_1h) >= 30:
            ema20_1h = calc_ema(closes_1h, 20)
            rsi_1h = calc_rsi_wilder(closes_1h, 14)

            if ema20_1h and rsi_1h:
                price_1h = closes_1h[-1]
                ema20_1h_val = ema20_1h[-1]
                rsi_1h_val = rsi_1h[-1]
                # 價格與 1H EMA20 的距離（百分比）
                dist_to_ema20 = (price_1h - ema20_1h_val) / ema20_1h_val

                is_long = (best_strat == "趨勢做多")

                if is_long:
                    # 做多入場：價格在 1H EMA20 附近（回調入場）或剛突破
                    # 距離 EMA20 在 -1% ~ +2% 範圍 + RSI 40-65（不過熱）
                    if -0.01 <= dist_to_ema20 <= 0.02 and 40 <= rsi_1h_val <= 65:
                        entry_timing = "1H_PULLBACK"  # 最佳：回調至均線
                    elif dist_to_ema20 > 0.02 and rsi_1h_val < 70:
                        entry_timing = "1H_ABOVE"     # 可接受：在均線上方但未過熱
                    else:
                        entry_timing = "1H_REJECT"    # 不適合入場
                else:
                    # 做空入場：價格在 1H EMA20 附近（反彈入場）或剛跌破
                    # 距離 EMA20 在 -2% ~ +1% 範圍 + RSI 35-60（不過冷）
                    if -0.02 <= dist_to_ema20 <= 0.01 and 35 <= rsi_1h_val <= 60:
                        entry_timing = "1H_PULLBACK"  # 最佳：反彈至均線
                    elif dist_to_ema20 < -0.02 and 30 < rsi_1h_val < 55:
                        entry_timing = "1H_BELOW"     # 可接受：在均線下方但未過冷也未過熱
                    else:
                        entry_timing = "1H_REJECT"    # 不適合入場

                # 1H 拒絕入場 → 降級為 WAIT（仍顯示訊號，但標記不適合立即入場）
                if entry_timing == "1H_REJECT":
                    entry_timing = "1H_WAIT"
                    print(f"  [趨勢] {symbol}: 4H方向通過但1H入場時機不佳 "
                          f"(dist_ema20={dist_to_ema20*100:+.1f}%, RSI_1h={rsi_1h_val:.1f}) → 顯示但標記等待")

    # TP/SL: ATR adaptive
    tp_dist = current_atr * ATR_TP_MULT[best_strat]
    sl_dist = current_atr * ATR_SL_MULT[best_strat]

    # SL% 下限 1.5%、上限 15%
    sl_pct_raw = sl_dist / current_price * 100 if current_price > 0 else 0
    SL_FLOOR_PCT, SL_CAP_PCT = 1.5, 15.0
    if sl_pct_raw < SL_FLOOR_PCT:
        sl_dist = current_price * SL_FLOOR_PCT / 100
    elif sl_pct_raw > SL_CAP_PCT:
        sl_dist = current_price * SL_CAP_PCT / 100
    raw_rr = ATR_TP_MULT[best_strat] / ATR_SL_MULT[best_strat]
    tp_dist = sl_dist * raw_rr

    if best_strat == "趨勢做多":
        tp_price = round(current_price + tp_dist, 6)
        sl_price = round(current_price - sl_dist, 6)
    else:  # 趨勢做空
        tp_price = round(current_price - tp_dist, 6)
        sl_price = round(current_price + sl_dist, 6)

    tp_pct = round(tp_dist / current_price * 100, 2)
    sl_pct = round(sl_dist / current_price * 100, 2)
    rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0

    # Signal strength
    if best_score >= 120:
        signal_strength = "STRONG"
    elif best_score >= 80:
        signal_strength = "MEDIUM"
    else:
        signal_strength = "WEAK"

    ema_status = "EMA20 > EMA50" if current_ema20 > current_ema50 else "EMA20 < EMA50"

    return {
        "symbol":         symbol,
        "change24h":      change24h,
        "price":          current_price,
        "rsi":            round(current_rsi, 1),
        "atr":            round(current_atr, 6),
        "regime":         regime,
        "obv_dir":        obv_dir,
        "ema20":          round(current_ema20, 6),
        "ema50":          round(current_ema50, 6),
        "ema_status":     ema_status,
        "vol_above_avg":  vol_above_avg,
        "btc_trend":      btc_trend,
        # 最佳策略
        "best_strat":     best_strat,
        "best_rate":      best_rate_val,
        "best_total":     best_total,
        "best_score":     round(best_score, 1),
        "signal_strength": signal_strength,
        "ev":             best_ev,
        # 進出場
        "tp":             tp_price,
        "sl":             sl_price,
        "rr":             rr,
        "tp_pct":         tp_pct,
        "sl_pct":         sl_pct,
        # 趨勢特有
        "strategy_type":  "trend",
        "hold_days":      3,
        "entry_timing":   entry_timing,
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

    # Step 0.8：取得 BTC 大盤趨勢
    btc_trend = get_btc_trend()
    btc_str = {1: "UP", -1: "DOWN", 0: "NEUTRAL"}.get(btc_trend, "?")
    print(f"\n[BTC] 大盤趨勢: {color(btc_str, 'green' if btc_trend == 1 else 'red' if btc_trend == -1 else 'yellow')}")

    # Step 1：抓全市場行情
    print("[INFO] 正在抓取全市場行情...")
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
            executor.submit(fetch_and_analyze, coin, learner, opt_params, btc_trend): coin
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
        htf_str = {1: "Up", -1: "Down", 0: "?"}.get(r.get('htf_trend', 0), "?")
        strength_color = {'STRONG': 'green', 'MEDIUM': 'yellow', 'WEAK': 'red'}
        div_str = r.get('divergence', 'none')

        print(f"\n{medals[rank]} {color(r['symbol'], 'cyan')}  24h: {chg}")
        print(f"   Price: {r['price']}  |  RSI: {r['rsi']}  |  MFI: {r['mfi']}")
        print(f"   BB: {r['bb_pos']}  |  ATR: {r['atr']}  |  OBV: {obv_str}")
        print(f"   Regime: {r['regime']}  |  5m Trend: {htf_str}  |  Divergence: {div_str}")
        print(f"   S/R: {r.get('support_resistance', 'N/A')}")
        print(f"   策略：{r['best_strat']} {color(str(r['best_rate']) + '%', 'green')}"
              f"（{r['best_total']}次）")
        print(f"   Signal: {color(r['signal_strength'], strength_color.get(r['signal_strength'], 'white'))}"
              f"  Score: {r['best_score']}")
        print(f"   Core: learn={r['weight']} regime={r['regime_bonus']} "
              f"obv={r['obv_bonus']} recent={r['recent_bonus']} "
              f"conf={r['confidence']}")
        print(f"   Env: {r.get('env_multiplier', 1.0)} ({r.get('env_detail', '')})")
        print(f"   明細：{r['detail']}")
        print(f"   進場: {r['price']}  "
              f"止盈: {r['tp']} (+{r['tp_pct']}%)  "
              f"止損: {r['sl']} (-{r['sl_pct']}%)  "
              f"風報比: 1:{r['rr']}")
        print(f"   建議倉位: {r.get('kelly_pct', 0)}% (Kelly)")
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
