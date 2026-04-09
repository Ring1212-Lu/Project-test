## Quick 4-Agent Review (針對單一修改)

請針對 $ARGUMENTS 執行快速 4-Agent 交叉審查。

### 流程

同時啟動 4 個 Agent，從不同角度審查這個修改：

- **Agent 1 (Advocate)**: 分析修改的優點和正確性，確認它解決了目標問題
- **Agent 2 (Critic)**: 挑戰修改，找出潛在問題、邊界情況、遺漏
- **Agent 3 (Integrator)**: 檢查修改與系統其他部分的相容性和一致性
- **Agent 4 (Tester)**: 設計測試情境，檢查邊界條件和異常路徑

### 論證

4 Agent 結果彙總後：
1. 如果 Agent 2 或 4 發現問題，說明問題並提出修改建議
2. 如果所有 Agent 同意，確認修改可以 commit
3. 有分歧時，列出正反論點讓我決定
