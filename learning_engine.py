#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自我學習引擎 v2 (Learning Engine)
==================================
改進：
1. 貝葉斯權重更新 + 時間衰減（長期未交易的幣種權重自動回歸均值）
2. 市場狀態偵測（trending / ranging / volatile）影響策略選擇
3. 多周期驗證（不再僅依 5 分鐘判定，改用觸及止盈止損 + 週期加權）
4. 策略表現衰減：近期結果影響力 > 遠期（指數衰減）
5. 最佳組合追蹤：記錄歷史最佳 (symbol, strategy) 組合
6. 防止檔案無限膨脹：自動裁剪 + 壓縮舊資料
"""

import json
import os
import time
import math
import threading
from datetime import datetime
from collections import defaultdict


class MarketRegime:
    """市場狀態偵測器"""
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    VOLATILE = "volatile"

    @staticmethod
    def detect(closes, period=50):
        """根據價格序列判斷市場狀態"""
        if len(closes) < period:
            return MarketRegime.RANGING

        recent = closes[-period:]
        # 計算趨勢斜率（線性回歸簡化版）
        n = len(recent)
        x_mean = (n - 1) / 2
        y_mean = sum(recent) / n
        numerator = sum((i - x_mean) * (recent[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        slope = numerator / denominator if denominator != 0 else 0
        # 正規化斜率
        norm_slope = slope / y_mean if y_mean != 0 else 0

        # 計算波動率（標準差/均值）
        std = (sum((p - y_mean) ** 2 for p in recent) / n) ** 0.5
        volatility = std / y_mean if y_mean != 0 else 0

        # 判斷
        if volatility > 0.05:  # 高波動
            return MarketRegime.VOLATILE
        elif norm_slope > 0.001:
            return MarketRegime.TRENDING_UP
        elif norm_slope < -0.001:
            return MarketRegime.TRENDING_DOWN
        else:
            return MarketRegime.RANGING


class LearningEngine:
    """自我學習引擎 v2：追蹤預測 → 驗證結果 → 動態調整權重"""

    # 權重更新參數
    WIN_MULTIPLIER  = 1.08   # 預測正確時的權重提升（加大獎勵）
    LOSE_MULTIPLIER = 0.88   # 預測錯誤時的權重衰減（加大懲罰）
    MAX_WEIGHT      = 2.5    # 權重上限
    MIN_WEIGHT      = 0.2    # 權重下限
    DEFAULT_WEIGHT  = 1.0    # 初始權重

    # 時間衰減參數
    WEIGHT_DECAY_RATE  = 0.001   # 每小時權重向均值回歸的速率
    WEIGHT_DECAY_HOURS = 24      # 超過此時間未更新開始衰減

    # 預測過期時間（秒）
    PREDICTION_TTL = 3600 * 4   # 4 小時（縮短，提高反饋速度）

    # 驗證等待時間（秒）
    MIN_VALIDATION_AGE = 180    # 至少等 3 分鐘

    # 歷史記錄上限
    MAX_HISTORY = 5000
    MAX_PENDING = 2000

    # 市場狀態對策略的適配分數
    # 基於實際數據更新：做空各regime 74-100%，追多各regime <40%
    REGIME_BONUS = {
        MarketRegime.TRENDING_UP:   {"做空": 1.1, "抄底": 0.9, "追多": 0.7},   # 做空78%↑, 追多28%↓
        MarketRegime.TRENDING_DOWN: {"做空": 1.4, "抄底": 1.4, "追多": 0.5},   # 做空100%, 抄底100%
        MarketRegime.RANGING:       {"做空": 1.1, "抄底": 1.2, "追多": 0.7},   # 做空80%, 追多38%
        MarketRegime.VOLATILE:      {"做空": 1.0, "抄底": 0.8, "追多": 0.6},   # 做空74%, 追多29%
    }

    def __init__(self, filepath):
        self._lock = threading.RLock()
        self.filepath = filepath
        self.data = {
            "weights": {},          # {"SYMBOL:STRATEGY": {"value": float, "updated": timestamp}}
            "pending": [],          # 待驗證的預測
            "history": [],          # 已驗證的歷史記錄
            "stats": {
                "total_predictions": 0,
                "total_validations": 0,
                "total_wins": 0,
                "total_losses": 0,
                "total_expired": 0,
                "strategy_stats": {
                    "做空": {"wins": 0, "losses": 0, "expired": 0},
                    "抄底": {"wins": 0, "losses": 0, "expired": 0},
                    "追多": {"wins": 0, "losses": 0, "expired": 0},
                    "做空(寬)": {"wins": 0, "losses": 0, "expired": 0},
                    "抄底(寬)": {"wins": 0, "losses": 0, "expired": 0},
                    "趨勢做多": {"wins": 0, "losses": 0, "expired": 0},
                    "趨勢做空": {"wins": 0, "losses": 0, "expired": 0},
                },
                "regime_stats": {},  # {regime: {strategy: {wins, losses}}}
            },
        }
        self._load()

    # ---- 持久化 ----

    def _load(self):
        with self._lock:
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, 'r', encoding='utf-8') as f:
                        loaded = json.load(f)
                    # 合併載入的資料（向後相容）
                    for key in self.data:
                        if key in loaded:
                            self.data[key] = loaded[key]
                    # 遷移舊格式權重（純數字 → 物件）
                    for k, v in list(self.data["weights"].items()):
                        if isinstance(v, (int, float)):
                            self.data["weights"][k] = {
                                "value": v, "updated": time.time()
                            }
                    # 確保結構完整
                    for strat in ["做空", "抄底", "追多", "趨勢做多", "趨勢做空"]:
                        if strat not in self.data["stats"]["strategy_stats"]:
                            self.data["stats"]["strategy_stats"][strat] = {
                                "wins": 0, "losses": 0, "expired": 0
                            }
                    if "regime_stats" not in self.data["stats"]:
                        self.data["stats"]["regime_stats"] = {}
                    print(f"[LEARN] 載入學習資料：{len(self.data['pending'])} 筆待驗證，"
                          f"{len(self.data['history'])} 筆歷史，"
                          f"{len(self.data['weights'])} 組權重")
                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    print(f"[LEARN] 學習檔案損壞，重新初始化: {e}")

    def save(self):
        with self._lock:
            # 裁剪
            if len(self.data["history"]) > self.MAX_HISTORY:
                self.data["history"] = self.data["history"][-self.MAX_HISTORY:]
            if len(self.data["pending"]) > self.MAX_PENDING:
                self.data["pending"] = self.data["pending"][-self.MAX_PENDING:]
            try:
                with open(self.filepath, 'w', encoding='utf-8') as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
            except IOError as e:
                print(f"[LEARN] 儲存失敗: {e}")

    # ---- 權重管理 ----

    def _weight_key(self, symbol, strategy):
        return f"{symbol}:{strategy}"

    def get_weight(self, symbol, strategy):
        """取得某 (symbol, strategy) 的學習權重（含時間衰減）"""
        key = self._weight_key(symbol, strategy)
        entry = self.data["weights"].get(key)
        if entry is None:
            return self.DEFAULT_WEIGHT

        value = entry["value"]
        last_updated = entry.get("updated", time.time())
        hours_since = (time.time() - last_updated) / 3600

        # 時間衰減：權重向 DEFAULT_WEIGHT 回歸
        if hours_since > self.WEIGHT_DECAY_HOURS:
            decay_hours = hours_since - self.WEIGHT_DECAY_HOURS
            decay_factor = math.exp(-self.WEIGHT_DECAY_RATE * decay_hours)
            value = self.DEFAULT_WEIGHT + (value - self.DEFAULT_WEIGHT) * decay_factor

        return round(value, 4)

    def get_regime_bonus(self, regime, strategy):
        """取得市場狀態對策略的加成"""
        return self.REGIME_BONUS.get(regime, {}).get(strategy, 1.0)

    def get_global_strat_winrate(self, strategy):
        """取得策略的全局歷史勝率，回傳 (win_rate, total_trades)"""
        base = strategy.replace("(寬)", "").strip() if "(寬)" in strategy else strategy
        ss = self.data["stats"]["strategy_stats"].get(base, {"wins": 0, "losses": 0})
        total = ss["wins"] + ss["losses"]
        if total < 10:
            return None, total  # 數據不足，不做調整
        return round(ss["wins"] / total * 100, 1), total

    def _update_weight(self, symbol, strategy, win):
        """更新權重"""
        key = self._weight_key(symbol, strategy)
        entry = self.data["weights"].get(key, {"value": self.DEFAULT_WEIGHT})
        current = entry["value"] if isinstance(entry, dict) else entry

        if win:
            new_value = min(current * self.WIN_MULTIPLIER, self.MAX_WEIGHT)
        else:
            new_value = max(current * self.LOSE_MULTIPLIER, self.MIN_WEIGHT)

        self.data["weights"][key] = {
            "value": round(new_value, 4),
            "updated": time.time(),
        }
        return self.data["weights"][key]["value"]

    # ---- 預測記錄 ----

    def record_prediction(self, symbol, strategy, entry_price, tp_price, sl_price,
                          rate, score, regime="unknown", ttl=None):
        """記錄一筆預測（同幣+同策略去重，避免 pending 溢出）"""
        with self._lock:
            # 去重：同幣種+同策略尚在 pending 中 → 跳過
            for p in self.data["pending"]:
                if p["symbol"] == symbol and p["strategy"] == strategy:
                    return  # 已存在，不重複記錄

            prediction = {
                "symbol":      symbol,
                "strategy":    strategy,
                "entry_price": entry_price,
                "tp_price":    tp_price,
                "sl_price":    sl_price,
                "rate":        rate,
                "score":       score,
                "regime":      regime,
                "timestamp":   time.time(),
                "time_str":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "ttl":         ttl if ttl is not None else self.PREDICTION_TTL,
            }
            self.data["pending"].append(prediction)
            self.data["stats"]["total_predictions"] += 1
        self.save()

    # ---- 預測驗證 ----

    def validate_pending_predictions(self):
        """驗證所有待驗證的預測，回傳本次驗證筆數"""
        with self._lock:
            if not self.data["pending"]:
                return 0

            now = time.time()
            validated_count = 0
            remaining = []

            current_prices = self._fetch_current_prices()

            for pred in self.data["pending"]:
                age = now - pred["timestamp"]
                symbol = pred["symbol"]
                strategy = pred["strategy"]

                # 超過 TTL → 過期
                pred_ttl = pred.get("ttl", self.PREDICTION_TTL)
                if age > pred_ttl:
                    self._record_result(pred, "expired")
                    validated_count += 1
                    continue

                # 未到驗證時間
                if age < self.MIN_VALIDATION_AGE:
                    remaining.append(pred)
                    continue

                current_price = current_prices.get(symbol)
                if current_price is None:
                    remaining.append(pred)
                    continue

                result = self._check_prediction(pred, current_price, age)
                if result is not None:
                    is_win = result
                    self._record_result(pred, "win" if is_win else "lose")
                    new_w = self._update_weight(symbol, strategy, is_win)
                    # 記錄 regime 統計
                    self._update_regime_stats(pred.get("regime", "unknown"), strategy, is_win)
                    validated_count += 1
                else:
                    remaining.append(pred)

            self.data["pending"] = remaining
            if validated_count > 0:
                self.data["stats"]["total_validations"] += validated_count
                self.save()

            return validated_count

    def _check_prediction(self, pred, current_price, age):
        """
        多階段驗證：
        1. 觸及止盈 → 勝
        2. 觸及止損 → 敗
        3. 超過 15 分鐘 → 用價格方向 + 幅度加權判定
        """
        entry = pred["entry_price"]
        tp    = pred["tp_price"]
        sl    = pred["sl_price"]
        strat = pred["strategy"]

        is_short = "做空" in strat
        if is_short:
            if current_price <= tp:
                return True
            if current_price >= sl:
                return False
        else:
            if current_price >= tp:
                return True
            if current_price <= sl:
                return False

        # 15 分鐘後用方向 + 幅度判定
        if age > 900:
            pnl_pct = (current_price - entry) / entry if entry > 0 else 0
            if is_short:
                pnl_pct = -pnl_pct  # 做空盈虧反轉

            # 需要至少 0.1% 的明確方向才判定
            if pnl_pct > 0.001:
                return True
            elif pnl_pct < -0.001:
                return False

        return None

    def _update_regime_stats(self, regime, strategy, win):
        """更新市場狀態統計"""
        rs = self.data["stats"]["regime_stats"]
        if regime not in rs:
            rs[regime] = {}
        if strategy not in rs[regime]:
            rs[regime][strategy] = {"wins": 0, "losses": 0}
        if win:
            rs[regime][strategy]["wins"] += 1
        else:
            rs[regime][strategy]["losses"] += 1

    def _record_result(self, pred, result):
        """記錄驗證結果"""
        record = {
            **pred,
            "result":       result,
            "validated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.data["history"].append(record)

        strat = pred["strategy"]
        ss = self.data["stats"]["strategy_stats"]
        if strat not in ss:
            ss[strat] = {"wins": 0, "losses": 0, "expired": 0}

        if result == "win":
            self.data["stats"]["total_wins"] += 1
            ss[strat]["wins"] += 1
        elif result == "lose":
            self.data["stats"]["total_losses"] += 1
            ss[strat]["losses"] += 1
        elif result == "expired":
            self.data["stats"]["total_expired"] += 1
            ss[strat]["expired"] += 1

    def _fetch_current_prices(self):
        """批量取得目前行情價格（帶重試）"""
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        prices = {}
        try:
            session = requests.Session()
            retry = Retry(total=3, backoff_factor=0.5, allowed_methods=["GET"])
            session.mount("https://", HTTPAdapter(max_retries=retry))
            r = session.get("https://api.pionex.com/api/v1/market/tickers",
                            params={"type": "PERP"}, timeout=15)
            if r.status_code == 200:
                tickers = r.json().get("data", {}).get("tickers", [])
                for t in tickers:
                    sym = t.get("symbol", "")
                    try:
                        prices[sym] = float(t.get("close", 0))
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass
        return prices

    # ---- 近期表現加權 ----

    def get_recent_accuracy(self, symbol, strategy, lookback=20):
        """
        取得近 N 筆驗證的勝率（指數衰減加權）。
        近期結果影響力更大。
        """
        key = f"{symbol}:{strategy}"
        relevant = [h for h in self.data["history"]
                    if h["symbol"] == symbol
                    and h["strategy"] == strategy
                    and h["result"] in ("win", "lose")]

        if not relevant:
            return None, 0

        recent = relevant[-lookback:]
        total_weight = 0
        weighted_wins = 0
        for i, h in enumerate(recent):
            # 越新的記錄權重越高
            w = math.exp(0.1 * (i - len(recent)))
            total_weight += w
            if h["result"] == "win":
                weighted_wins += w

        if total_weight == 0:
            return None, 0

        return round(weighted_wins / total_weight * 100, 1), len(recent)

    # ---- 統計輸出 ----

    @property
    def total_predictions(self):
        return self.data["stats"]["total_predictions"]

    @property
    def total_validations(self):
        return self.data["stats"]["total_validations"]

    def print_summary(self):
        """印出學習統計摘要"""
        stats = self.data["stats"]
        total_v = stats["total_validations"]
        if total_v == 0:
            print("\n[LEARN] 尚無驗證資料，持續累積中...")
            return

        wins    = stats["total_wins"]
        losses  = stats["total_losses"]
        expired = stats["total_expired"]
        decided = wins + losses
        overall_rate = round(wins / decided * 100, 1) if decided > 0 else 0

        print(f"\n[LEARN] === 學習統計 ===")
        print(f"  總預測: {stats['total_predictions']}  "
              f"已驗證: {total_v}  "
              f"待驗證: {len(self.data['pending'])}")
        print(f"  勝: {wins}  負: {losses}  過期: {expired}  "
              f"整體勝率: {overall_rate}%")

        ss = stats["strategy_stats"]
        for strat in ["做空", "抄底", "追多"]:
            s = ss.get(strat, {"wins": 0, "losses": 0, "expired": 0})
            sw, sl_count = s["wins"], s["losses"]
            st = sw + sl_count
            sr = round(sw / st * 100, 1) if st > 0 else 0
            # 平均權重
            weights = []
            for k, v in self.data["weights"].items():
                if k.endswith(f":{strat}"):
                    val = v["value"] if isinstance(v, dict) else v
                    weights.append(val)
            avg_w = round(sum(weights) / len(weights), 3) if weights else self.DEFAULT_WEIGHT
            print(f"  {strat}: {sw}勝/{sl_count}負 ({sr}%)  平均權重: {avg_w}")

        # 市場狀態統計
        rs = stats.get("regime_stats", {})
        if rs:
            print(f"\n  --- 市場狀態分析 ---")
            for regime, strats in rs.items():
                parts = []
                for s, v in strats.items():
                    total = v["wins"] + v["losses"]
                    rate = round(v["wins"] / total * 100, 1) if total > 0 else 0
                    parts.append(f"{s}:{rate}%({total})")
                print(f"  {regime}: {' | '.join(parts)}")

    def get_top_performers(self, n=5):
        """回傳歷史表現最好的 (symbol, strategy) 組合"""
        perf = defaultdict(lambda: {"wins": 0, "total": 0})
        for h in self.data["history"]:
            if h["result"] in ("win", "lose"):
                key = f"{h['symbol']}:{h['strategy']}"
                perf[key]["total"] += 1
                if h["result"] == "win":
                    perf[key]["wins"] += 1

        ranked = []
        for key, v in perf.items():
            if v["total"] >= 3:
                ranked.append((key, round(v["wins"] / v["total"] * 100, 1), v["total"]))
        ranked.sort(key=lambda x: (x[1], x[2]), reverse=True)
        return ranked[:n]

    def cleanup_stale_weights(self, max_age_hours=168):
        """清理超過指定時間未更新的權重（預設 7 天）"""
        now = time.time()
        removed = 0
        for key in list(self.data["weights"].keys()):
            entry = self.data["weights"][key]
            if isinstance(entry, dict):
                age_hours = (now - entry.get("updated", now)) / 3600
                if age_hours > max_age_hours:
                    del self.data["weights"][key]
                    removed += 1
        if removed > 0:
            print(f"[LEARN] 清理了 {removed} 組過期權重")
            self.save()
        return removed

    # ---- 自動優化引擎 ----

    def auto_optimize(self, symbols, fetch_klines_fn, backtest_fn, analyze_trades_fn):
        """
        自動回測 → 分析 → 更新最佳參數
        回傳優化後的參數字典
        """
        # 載入現有最佳參數
        if "optimized_params" not in self.data:
            self.data["optimized_params"] = {
                "rsi_short_thresh": 70,
                "rsi_long_thresh": 30,
                "best_hold_period": 5,
                "last_optimized": None,
                "optimization_count": 0,
                "history": [],  # 歷次優化記錄
            }

        params = self.data["optimized_params"]
        print(f"[AUTO-OPT] 開始自動優化（第 {params['optimization_count'] + 1} 次）...")

        # 收集所有交易數據
        all_trades_by_hp = defaultdict(lambda: {"做空": [], "抄底": [], "追多": []})
        rsi_test_results = []
        tested = 0

        for sym in symbols[:8]:  # 最多測 8 個幣種（速度考量）
            klines = fetch_klines_fn(sym)
            if not klines:
                continue

            # 標準回測
            trades = backtest_fn(sym, klines, hold_periods=[3, 5, 8, 10, 15])
            if not trades:
                continue
            tested += 1

            for hp, strats in trades.items():
                for strat, t_list in strats.items():
                    all_trades_by_hp[hp][strat].extend(t_list)

            # RSI 閾值測試
            for short_th in [65, 70, 75, 80]:
                for long_th in [20, 25, 30, 35]:
                    t = backtest_fn(sym, klines, hold_periods=[5],
                                    rsi_short_thresh=short_th, rsi_long_thresh=long_th)
                    if not t:
                        continue
                    for strat in ["做空", "抄底", "追多"]:
                        t_list = t[5][strat]
                        if len(t_list) >= 3:
                            wins = sum(1 for x in t_list if x["pnl_pct"] > 0)
                            rsi_test_results.append({
                                "strat": strat,
                                "rsi_short": short_th,
                                "rsi_long": long_th,
                                "trades": len(t_list),
                                "win_rate": round(wins / len(t_list) * 100, 1),
                            })

        if tested == 0:
            print(f"[AUTO-OPT] 無法取得足夠數據，保持現有參數")
            return params

        # 找最佳持倉期
        best_hp = params["best_hold_period"]
        best_hp_score = -999
        for hp, strats in all_trades_by_hp.items():
            total_trades = sum(len(v) for v in strats.values())
            total_wins = sum(sum(1 for t in v if t["pnl_pct"] > 0) for v in strats.values())
            if total_trades >= 10:
                wr = total_wins / total_trades * 100
                # 分數 = 勝率，但偏好較少 K 線（速度更快）
                score = wr - (hp * 0.3)  # 每多 1 根扣 0.3 分
                if score > best_hp_score:
                    best_hp_score = score
                    best_hp = hp

        # 找最佳 RSI 閾值
        best_short = params["rsi_short_thresh"]
        best_long = params["rsi_long_thresh"]

        if rsi_test_results:
            # 按策略分組找最佳
            for strat in ["做空", "抄底", "追多"]:
                strat_r = [r for r in rsi_test_results
                           if r["strat"] == strat and r["trades"] >= 5]
                if strat_r:
                    best = max(strat_r, key=lambda x: x["win_rate"])
                    if strat == "做空" and best["win_rate"] > 55:
                        best_short = best["rsi_short"]
                    elif strat == "抄底" and best["win_rate"] > 55:
                        best_long = best["rsi_long"]

        # 更新 regime bonus（根據實際數據）
        regime_stats = self.data["stats"].get("regime_stats", {})
        updated_bonus = dict(self.REGIME_BONUS)  # 複製預設值
        for regime, strats in regime_stats.items():
            if regime not in updated_bonus:
                continue
            for strat, v in strats.items():
                total = v["wins"] + v["losses"]
                if total >= 5:
                    actual_wr = v["wins"] / total
                    # 根據勝率調整 bonus：>60% 加成，<40% 懲罰
                    if actual_wr > 0.6:
                        updated_bonus[regime][strat] = min(
                            updated_bonus[regime][strat] * 1.1, 1.5
                        )
                    elif actual_wr < 0.4:
                        updated_bonus[regime][strat] = max(
                            updated_bonus[regime][strat] * 0.85, 0.4
                        )

        # 計算優化前後對比
        old_params = {
            "rsi_short": params["rsi_short_thresh"],
            "rsi_long": params["rsi_long_thresh"],
            "hold": params["best_hold_period"],
        }
        new_params = {
            "rsi_short": best_short,
            "rsi_long": best_long,
            "hold": best_hp,
        }

        # 保存
        params["rsi_short_thresh"] = best_short
        params["rsi_long_thresh"] = best_long
        params["best_hold_period"] = best_hp
        params["last_optimized"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        params["optimization_count"] += 1

        # 記錄優化歷史
        opt_record = {
            "time": params["last_optimized"],
            "tested_symbols": tested,
            "old": old_params,
            "new": new_params,
            "changed": old_params != new_params,
        }
        params["history"].append(opt_record)
        if len(params["history"]) > 50:
            params["history"] = params["history"][-50:]

        self.REGIME_BONUS = updated_bonus
        self.data["optimized_params"] = params
        self.save()

        # 輸出結果
        changed = old_params != new_params
        if changed:
            print(f"[AUTO-OPT] 參數已更新！")
            if old_params["rsi_short"] != new_params["rsi_short"]:
                print(f"  RSI 做空閾值: {old_params['rsi_short']} -> {new_params['rsi_short']}")
            if old_params["rsi_long"] != new_params["rsi_long"]:
                print(f"  RSI 抄底閾值: {old_params['rsi_long']} -> {new_params['rsi_long']}")
            if old_params["hold"] != new_params["hold"]:
                print(f"  最佳持倉期: {old_params['hold']} -> {new_params['hold']} 根")
        else:
            print(f"[AUTO-OPT] 現有參數已是最佳，無需調整")

        print(f"[AUTO-OPT] 測試了 {tested} 個幣種，完成第 {params['optimization_count']} 次優化")

        return params

    def get_optimized_params(self):
        """取得目前的最佳參數"""
        return self.data.get("optimized_params", {
            "rsi_short_thresh": 70,
            "rsi_long_thresh": 30,
            "best_hold_period": 5,
        })
