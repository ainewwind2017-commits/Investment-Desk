# A-share DESK PRO V15.9 Replicable Backtest Standard

Version date: 2026-05-23

## 1. Goal

V15.9 does not promise that backtest return will equal live return. Its goal is
to make the backtest auditable enough that the gap between backtest and live
trading is explainable.

Any report that fails one of the gates below must be labeled as a proxy
backtest. It cannot be used as a live return expectation.

## 2. Formal Backtest Gates

| Gate | Requirement | If missing |
|---|---|---|
| No future universe | Use a date-stamped historical stock pool. Each symbol must have an effective_from and optional effective_to date. | Report is proxy only. |
| No survivor bias | Include delisted, ST, weak, and expired candidates if they were in the historical pool at the time. | Report is proxy only. |
| No current constituent backfill | Do not use the current stock pool to reconstruct the past. | Report is proxy only. |
| Trade friction | Include commission, stamp duty, slippage, and impact buffer. | Report is invalid. |
| Execution constraint | Enforce next-day execution, T+1 sell constraint, and limit-up/limit-down no-fill rules. | Report is invalid. |
| Out-of-sample check | Use rolling train/test windows and avoid choosing the best parameter set by total return. | Report is research only. |

## 3. Required Data Files

### 3.1 `pool_history_v15_9.csv`

Required columns:

```text
effective_from,effective_to,code,name,theme,segment,pool_level,is_core,source
```

Rules:

- `effective_from` is the first date the stock was allowed to enter the strategy.
- `effective_to` is blank if still active.
- A stock cannot be selected before its `effective_from`.
- A stock removed from the pool cannot be newly bought after `effective_to`.
- The file must be built from historical evidence, not from today's winners.

### 3.2 Price and Trading Data

Minimum viable data:

- Adjusted daily OHLCV.
- Trading calendar.
- Daily limit-up and limit-down state.
- ST and suspension state.
- Delisting history.

Daily close-only data is allowed only for conservative proxy testing.

## 4. Realistic Execution Model

V15.9 uses this execution model for formal testing:

1. Signal is generated after market close on signal date.
2. Order is executed no earlier than the next trading day.
3. If a buy target is limit-up on trade date, the buy is not filled.
4. If a sell target is limit-down on trade date, the sell is not filled.
5. Same-day newly bought position cannot be sold because of T+1.
6. Costs:
   - Buy: commission + slippage + impact buffer.
   - Sell: commission + stamp duty + slippage + impact buffer.
7. Report must show both gross and net results.

## 5. Parameter Discipline

The strategy may use V15.8's current robust parameters as defaults:

```text
core_cap = 15%
normal_cap = 10%
theme_cap = 30%
theme_threshold = 65
overheat = 20d return > 60% and price / MA20 > 1.25
drawdown gates = -8%, -12%, -15%
```

But live parameters must be selected by robustness and drawdown control, not by
the single highest backtest return.

## 6. Report Labels

Every backtest report must print exactly one of:

- `FORMAL_REPLICABLE`: all formal gates passed.
- `PROXY_RESEARCH`: useful for research, not live return expectation.
- `INVALID`: missing critical cost or execution constraints.

Current workspace status:

- `stock_pool_v15_7.csv` is a current static pool.
- Therefore historical results based on it remain `PROXY_RESEARCH`.
- To upgrade to `FORMAL_REPLICABLE`, create `pool_history_v15_9.csv`.

