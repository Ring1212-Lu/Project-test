## 4-Agent Parallel Verification (Domain Experts)

請啟動 4 個 **Opus 領域專家 Agent** 並行驗證最近的程式碼修改，確認改動是否符合結論。

### 驗證範圍

用 `git diff HEAD~1` 找出所有變更，然後 4 位專家 Agent 並行審查：

- **Agent 1 — 高級量化性能 Agent (Opus)**
  從實盤 P&L、執行品質、生產系統角度評估所有變更的實際影響。
  檢查範圍：滑點建模、費用結構、訂單路由、倉位生命週期、latency/throughput、故障回滾、halt/circuit breaker、持久化還原、資源洩漏。

- **Agent 2 — 頂級虛擬貨幣分析 Agent (Opus)**
  從信號研究方法論、Alpha 保存、評分系統深度分析角度評估所有變更。
  檢查範圍：regime 判斷、score formula、Bayesian shrinkage、global_penalty、信號衰減、overfitting 風險、多時間框架一致性、strategy mix 平衡。

- **Agent 3 — 統計建模 Agent (Opus)**
  審查所有策略的進場條件、評分公式、安全檢查、ATR 倍數是否正確實作。
  檢查範圍：TP/SL 邏輯、win_rate/adj_rate 計算、min_score gate、crash filter、EMA/RSI/MACD 閾值、陪審團投票、寬嚴變體一致性、型別與邊界。

- **Agent 4 — 回測方法論審計 Agent (Opus)**
  審查回測-實盤一致性、費用模型、TP/SL 計算、學習引擎整合。
  檢查範圍：BT/Live fee alignment (FEE_RATE=0.0015)、slippage parity、ATR index offset 動態對齊、record_prediction post-slippage、_check_prediction 判定對齊、同 hold_period/同閾值、hp/hold 超時、保本止損、BT close 取樣對齊實盤 lastPrice。

### 執行規範

每位 Agent 必須：
1. 先讀 `git diff HEAD~1` 確認本輪變更範圍
2. 針對自己領域的每個變更點給出 **PASS / CONCERN / REJECT**
3. 每項結論附上 `file:line` 與具體理由
4. **CONCERN 或 REJECT** 必須提出具體修改建議

### 輸出格式

每個 Agent 輸出：
```
[Agent N - 領域名稱]
  項目 | 結果 | file:line | 說明
  ...
  小結：X PASS / Y CONCERN / Z REJECT
```

### 最終彙總

主程式彙總 4 位 Agent 的結論：
- **VERIFIED**: 四位 Agent 全部 PASS 的修改
- **CONCERN**: 需要注意但不阻塞的問題（記錄於 commit message，下一輪處理）
- **REJECT**: 必須修正的問題（自動進入修復流程，修完後重新 /verify）

### 修復循環

如果任一 Agent 標記 REJECT：
1. 根據建議修改程式碼
2. 執行 `python3 -c "import py_compile; py_compile.compile('filename', doraise=True)"` 驗證語法
3. 重新啟動 4 位 Agent 驗證修復是否正確
4. 全部 VERIFIED 後 commit + push
