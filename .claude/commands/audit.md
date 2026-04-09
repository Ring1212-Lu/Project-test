## 4-Agent Parallel Audit (REVIEW_CHECKLIST.md)

請依照 REVIEW_CHECKLIST.md 執行完整 4 Agent 並行審查。

### 執行流程：

**Phase 1 — 4 Agent 並行審查**

同時啟動 4 個 Agent，各自負責以下類別：

- **Agent A (Thread Safety & Rollback & Consistency)**: 類別 1-3（執行緒安全、失敗回滾、信號一致性）
- **Agent B (Scan Frequency & Resources)**: 類別 4-5（掃描頻率、資源重複）
- **Agent C (Notifications & Position & State)**: 類別 6-8（通知完整性、倉位數據、狀態持久化）
- **Agent D (Triggers & Code Hygiene)**: 類別 9-10（觸發邏輯、程式碼衛生）

每個 Agent 需：
1. 逐項讀取相關程式碼（crypto_monitor_v2.py, trading_bot.py, web_app.py, learning_engine.py）
2. 對每個 checklist 項目給出 PASS / FAIL / WARN
3. FAIL 項目需附上：檔案、行號、問題描述、建議修改方案
4. 特別標記 CRITICAL 級別問題

**Phase 2 — 彙總與論證**

彙總 4 Agent 結果：
1. 列出所有 FAIL/WARN 項目
2. 對有爭議的項目進行交叉論證（一個 Agent 的 PASS 可能是另一個的 FAIL）
3. 區分 CRITICAL（必須立即修）vs NON-BLOCKING（建議修）
4. 產出最終修改清單

**Phase 3 — 執行修改**

依優先級修改程式碼：
1. 先修 CRITICAL
2. 再修 NON-BLOCKING
3. 每個修改後執行 `python3 -c "import py_compile; py_compile.compile('filename', doraise=True)"` 驗證語法
4. Commit 並 push

**Phase 4 — 4 Agent 驗證（自動觸發）**

修改完成後，自動啟動 4 個 Agent 驗證修改：
- 確認每個 FAIL 項目確實已修復
- 確認修改未引入新問題
- 確認跨檔案一致性（web_app / trading_bot / crypto_monitor_v2 同步）
- 最終輸出驗證報告
