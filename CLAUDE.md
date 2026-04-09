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
2. Launch 4 parallel verification Agents to confirm:
   - Agent A: Correctness of the change logic
   - Agent B: Cross-file consistency (parameters, types, field names)
   - Agent C: Thread safety and rollback completeness
   - Agent D: Notification/logging coverage and integration
3. If any Agent finds issues, fix them before committing
4. Include verification summary in commit message

### Available Slash Commands:
- `/audit` — Full REVIEW_CHECKLIST.md 4-Agent parallel audit (35 items)
- `/verify` — 4-Agent verification of recent changes
- `/quick-review [topic]` — Quick 4-Agent cross-review of a specific change
