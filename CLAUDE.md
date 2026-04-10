# Project Instructions

## Architecture
- **crypto_monitor_v2.py**: Core analysis engine (signals, scoring, indicators)
- **trading_bot.py**: Standalone execution bot (RiskManager, position management)
- **web_app.py**: Web dashboard bot (Flask + embedded trading logic)
- **learning_engine.py**: Backtest & parameter optimization

## Key Design Principles
- Two-layer architecture: Backtest = signal quality measurement, Live = conservative execution filtering
- Score formula: `score = adj_rate × weight × global_penalty × regime_adj` with Bayesian shrinkage
- Claim-then-close pattern for position closing (atomic removal under lock, rollback on API failure)
- RT indicator calculations must align with BT (backtest) implementations

## Cross-file Consistency Requirements
Parameters that must stay in sync across files:
- `min_score`: trading_bot.py ↔ web_app.py
- `best_hold_period`: learning_engine.py default ↔ crypto_monitor_v2.py default
- Position dict fields: trading_bot.py ↔ web_app.py (strategy_type, atr, chaodi_score, ev, etc.)
- `log_fn` callback chain: crypto_monitor_v2.py → trading_bot.py / web_app.py

## Auto-Review Protocol

When making code changes (optimization, bug fix, new feature), follow this protocol:

### After completing code modifications:
1. Run `python3 -c "import py_compile; py_compile.compile('filename', doraise=True)"` on all modified files
2. Launch 4 parallel **domain-expert** verification Agents (all Opus) to confirm the modifications match the intended conclusions:
   - **Agent 1 — 高級量化性能 Agent (Opus)**: 從實盤 P&L、執行品質、生產系統角度評估所有變更的實際影響（滑點建模、費用結構、訂單路由、倉位生命週期、latency/throughput、故障回滾）
   - **Agent 2 — 頂級虛擬貨幣分析 Agent (Opus)**: 從信號研究方法論、Alpha 保存、評分系統深度分析角度評估（regime 判斷、score formula、Bayesian shrinkage、信號衰減、overfitting 風險）
   - **Agent 3 — 統計建模 Agent (Opus)**: 審查所有策略的進場條件、評分公式、安全檢查、ATR 倍數（TP/SL 邏輯、win_rate 計算、min_score gate、circuit breaker、crash filter）
   - **Agent 4 — 回測方法論審計 Agent (Opus)**: 審查回測-實盤一致性、費用模型、TP/SL 計算、學習引擎整合（BT/Live fee alignment、slippage parity、ATR index offset、record_prediction 對齊）
3. Each agent outputs PASS / CONCERN / REJECT per item with file:line references
4. If any Agent finds REJECT issues, fix them before committing
5. Include verification summary in commit message

### Available Slash Commands:
- `/audit` — Full REVIEW_CHECKLIST.md 4-Agent parallel audit (35 items)
- `/verify` — 4-Agent verification of recent changes
- `/quick-review [topic]` — Quick 4-Agent cross-review of a specific change
