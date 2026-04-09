## 4-Agent Parallel Verification

請啟動 4 個 Agent 並行驗證最近的程式碼修改。

### 驗證範圍

用 `git diff HEAD~1` 找出所有變更，然後 4 Agent 並行驗證：

- **Agent A (Correctness)**: 修改邏輯是否正確？是否有 off-by-one、型別錯誤、邊界遺漏？
- **Agent B (Consistency)**: 修改是否在所有相關檔案同步？（web_app.py / trading_bot.py / crypto_monitor_v2.py / learning_engine.py 的參數、邏輯是否對齊）
- **Agent C (Safety)**: 是否引入新的執行緒安全問題、鎖死風險、資源洩漏？回滾路徑是否完整？
- **Agent D (Integration)**: 修改是否與既有功能相容？通知管道是否完整？前端是否同步？

### 輸出格式

每個 Agent 輸出：
```
[Agent X] 驗證項目 | 結果 | 說明
```

最終彙總：
- VERIFIED: 所有驗證通過的修改
- CONCERN: 需要注意但不阻塞的問題
- REJECT: 必須修正的問題（自動進入修復流程）
