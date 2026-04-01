#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自我學習引擎 (Learning Engine)
==============================
功能：
1. 記錄每次預測（幣種、策略、進場價、止盈止損、時間戳）
2. 下一輪掃描時驗證歷史預測的結果（是否觸及止盈/止損）
3. 根據驗證結果動態調整策略權重（貝葉斯更新）
4. 持久化學習資料到 JSON 檔案
5. 提供策略表現統計

學習邏輯：
- 每個 (symbol, strategy) 組合維護一個權重值（初始 1.0）
- 預測正確 → 權重 × 1.05（最高 2.0）
- 預測錯誤 → 權重 × 0.90（最低 0.3）
- 權重影響綜合評分，使系統逐漸偏向歷史表現好的策略
"""

import json
import os
import time
from datetime import datetime


class LearningEngine:
    """自我學習引擎：追蹤預測 → 驗證結果 → 調整權重"""

    # 權重更新參數
    WIN_MULTIPLIER  = 1.05   # 預測正確時的權重提升
    LOSE_MULTIPLIER = 0.90   # 預測錯誤時的權重衰減
    MAX_WEIGHT      = 2.0    # 權重上限
    MIN_WEIGHT      = 0.3    # 權重下限
    DEFAULT_WEIGHT  = 1.0    # 初始權重

    # 預測過期時間（秒）— 超過此時間的預測直接丟棄
    PREDICTION_TTL  = 3600 * 6  # 6 小時

    # 最多保留多少筆歷史記錄（防止檔案無限增長）
    MAX_HISTORY     = 5000

    def __init__(self, filepath):
        self.filepath = filepath
        self.data = {
            "weights": {},          # {"SYMBOL:STRATEGY": weight}
            "pending": [],          # 待驗證的預測
            "history": [],          # 已驗證的歷史記錄
            "stats": {              # 全域統計
                "total_predictions": 0,
                "total_validations": 0,
                "total_wins": 0,
                "total_losses": 0,
                "total_expired": 0,
                "strategy_stats": {
                    "做空": {"wins": 0, "losses": 0, "expired": 0},
                    "抄底": {"wins": 0, "losses": 0, "expired": 0},
                    "追多": {"wins": 0, "losses": 0, "expired": 0},
                },
            },
        }
        self._load()

    # ---- 持久化 ----

    def _load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                # 合併載入的資料（向後相容）
                for key in self.data:
                    if key in loaded:
                        self.data[key] = loaded[key]
                # 確保 strategy_stats 結構完整
                for strat in ["做空", "抄底", "追多"]:
                    if strat not in self.data["stats"]["strategy_stats"]:
                        self.data["stats"]["strategy_stats"][strat] = {
                            "wins": 0, "losses": 0, "expired": 0
                        }
                print(f"[LEARN] 載入學習資料：{len(self.data['pending'])} 筆待驗證，"
                      f"{len(self.data['history'])} 筆歷史")
            except (json.JSONDecodeError, KeyError) as e:
                print(f"[LEARN] 學習檔案損壞，重新初始化: {e}")

    def save(self):
        # 裁剪歷史記錄
        if len(self.data["history"]) > self.MAX_HISTORY:
            self.data["history"] = self.data["history"][-self.MAX_HISTORY:]
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"[LEARN] 儲存失敗: {e}")

    # ---- 權重管理 ----

    def _weight_key(self, symbol, strategy):
        return f"{symbol}:{strategy}"

    def get_weight(self, symbol, strategy):
        """取得某 (symbol, strategy) 的學習權重"""
        key = self._weight_key(symbol, strategy)
        return self.data["weights"].get(key, self.DEFAULT_WEIGHT)

    def _update_weight(self, symbol, strategy, win):
        """更新權重（貝葉斯式漸進調整）"""
        key = self._weight_key(symbol, strategy)
        current = self.data["weights"].get(key, self.DEFAULT_WEIGHT)
        if win:
            new_weight = min(current * self.WIN_MULTIPLIER, self.MAX_WEIGHT)
        else:
            new_weight = max(current * self.LOSE_MULTIPLIER, self.MIN_WEIGHT)
        self.data["weights"][key] = round(new_weight, 4)
        return self.data["weights"][key]

    # ---- 預測記錄 ----

    def record_prediction(self, symbol, strategy, entry_price, tp_price, sl_price,
                          rate, score):
        """記錄一筆預測，等待下輪驗證"""
        prediction = {
            "symbol":      symbol,
            "strategy":    strategy,
            "entry_price": entry_price,
            "tp_price":    tp_price,
            "sl_price":    sl_price,
            "rate":        rate,
            "score":       score,
            "timestamp":   time.time(),
            "time_str":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.data["pending"].append(prediction)
        self.data["stats"]["total_predictions"] += 1
        self.save()

    # ---- 預測驗證 ----

    def validate_pending_predictions(self):
        """
        驗證所有待驗證的預測。
        使用當前市場價格判斷是否觸及止盈/止損。
        回傳：本次驗證的筆數
        """
        if not self.data["pending"]:
            return 0

        now = time.time()
        validated_count = 0
        remaining = []

        # 批量取得行情
        current_prices = self._fetch_current_prices()

        for pred in self.data["pending"]:
            age = now - pred["timestamp"]
            symbol = pred["symbol"]
            strategy = pred["strategy"]

            # 超過 TTL → 過期丟棄
            if age > self.PREDICTION_TTL:
                self._record_result(pred, "expired")
                validated_count += 1
                continue

            # 至少等一個週期（60秒）才驗證
            if age < 60:
                remaining.append(pred)
                continue

            current_price = current_prices.get(symbol)
            if current_price is None:
                remaining.append(pred)
                continue

            # 判斷結果
            result = self._check_prediction(pred, current_price)
            if result is not None:
                self._record_result(pred, "win" if result else "lose")
                self._update_weight(symbol, strategy, result)
                validated_count += 1
            else:
                # 尚未觸及止盈/止損，繼續等待
                remaining.append(pred)

        self.data["pending"] = remaining
        if validated_count > 0:
            self.data["stats"]["total_validations"] += validated_count
            self.save()

        return validated_count

    def _check_prediction(self, pred, current_price):
        """
        檢查預測是否觸及止盈或止損。
        回傳 True=勝, False=敗, None=未定
        """
        entry  = pred["entry_price"]
        tp     = pred["tp_price"]
        sl     = pred["sl_price"]
        strat  = pred["strategy"]

        if strat == "做空":
            # 做空：價格跌到止盈=勝，漲到止損=敗
            if current_price <= tp:
                return True
            if current_price >= sl:
                return False
        else:
            # 做多（抄底/追多）：價格漲到止盈=勝，跌到止損=敗
            if current_price >= tp:
                return True
            if current_price <= sl:
                return False

        # 未觸及任一邊 → 用盈虧方向作為參考
        # 如果已經過了2個週期且有明顯方向，提前判定
        age = time.time() - pred["timestamp"]
        if age > 300:  # 5分鐘後
            if strat == "做空":
                return current_price < entry
            else:
                return current_price > entry

        return None

    def _record_result(self, pred, result):
        """記錄驗證結果到歷史"""
        record = {
            **pred,
            "result":      result,
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
        """批量取得目前行情價格"""
        import requests
        prices = {}
        try:
            r = requests.get("https://api.pionex.com/api/v1/market/tickers",
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

        wins   = stats["total_wins"]
        losses = stats["total_losses"]
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
            sw, sl = s["wins"], s["losses"]
            st = sw + sl
            sr = round(sw / st * 100, 1) if st > 0 else 0
            # 找出此策略的平均權重
            weights = [v for k, v in self.data["weights"].items() if k.endswith(f":{strat}")]
            avg_w = round(sum(weights) / len(weights), 3) if weights else self.DEFAULT_WEIGHT
            print(f"  {strat}: {sw}勝/{sl}負 ({sr}%)  平均權重: {avg_w}")

    def get_top_performers(self, n=5):
        """回傳歷史表現最好的 (symbol, strategy) 組合"""
        from collections import defaultdict
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
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked[:n]
