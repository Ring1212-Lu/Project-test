## Direct Audit (REVIEW_CHECKLIST.md)

由主 Claude 直接依照 REVIEW_CHECKLIST.md 逐項檢查並修改程式碼，不派遣子 Agent。

### 執行流程

**Phase 1 — 讀取 checklist**

1. 先讀取 `/home/user/Project-test/REVIEW_CHECKLIST.md` 完整 10 類別（所有項目）
2. 確認當前 branch（`git status` + `git branch --show-current`）
3. 讀取相關核心檔案的最新版本：
   - `crypto_monitor_v2.py`
   - `trading_bot.py`
   - `web_app.py`
   - `learning_engine.py`
   - `backtest.py`（如該輪涉及回測）

**Phase 2 — 逐項審查（主 Claude 直接執行）**

對 REVIEW_CHECKLIST.md 的每個項目：
1. 主 Claude **直接**用 Read / Grep 工具檢查對應程式碼
2. 對每個項目判定 **PASS / FAIL / WARN**
3. FAIL 項目必須附上：
   - 檔案路徑
   - 具體行號
   - 問題描述
   - 建議修改方案
4. 特別標記 **CRITICAL** 級別問題（會破壞正確性、安全性、資金安全）

輸出格式：
```
[類別 N - 名稱]
  §N.M 項目描述 | PASS/FAIL/WARN | file:line | 說明
  ...
```

**Phase 3 — 彙總**

逐類整理結果：
1. 列出所有 FAIL 與 WARN 項目
2. 區分 **CRITICAL**（必須立即修）vs **NON-BLOCKING**（建議修）
3. 依優先級排序，產出最終修改清單

**Phase 4 — 執行修改（主 Claude 直接動手）**

依優先級修改：
1. 先修 CRITICAL
2. 再修高價值 NON-BLOCKING
3. 每個修改後執行 `python3 -c "import py_compile; py_compile.compile('filename', doraise=True)"` 驗證語法
4. Commit 並 push 到當前 branch

**Phase 5 — 自動觸發 /verify**

修改完成後，自動執行 `/verify` 流程（4 位 Opus 領域專家驗證）：
- Agent 1: 高級量化性能 Agent
- Agent 2: 頂級虛擬貨幣分析 Agent
- Agent 3: 統計建模 Agent
- Agent 4: 回測方法論審計 Agent

如 /verify 發現 REJECT 項目 → 回到 Phase 4 修復 → 重新驗證，直到全部 VERIFIED。

### 與舊版差異

- **舊版**：Phase 1 派遣 4 個子 Agent 並行 audit
- **新版**：Phase 1-4 由主 Claude 直接執行（節省 context 傳遞成本、避免子 Agent 誤判）
- **Phase 5** 才派遣子 Agent（4 位領域專家），確保最終驗證的獨立性
