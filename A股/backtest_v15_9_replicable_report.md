# V15.9 Replicable Backtest Gate Report

Period: 2021-05-24 to 2026-05-22
Audit label: PROXY_RESEARCH

## Result

| Strategy | Total return | Annual return | Max drawdown | Sharpe | Calmar | Turnover |
|---|---:|---:|---:|---:|---:|---:|
| V15.9 realistic execution | 45.99% | 8.20% | -20.37% | 0.57 | 0.40 | 149.8 |

## Execution Assumptions

- Buy cost = commission 0.0300% + slippage 0.1000% + impact 0.0700%.
- Sell cost = commission 0.0300% + stamp duty 0.0500% + slippage 0.1000% + impact 0.0700%.
- Signals are delayed by one trading day before execution.
- Limit-up buys and limit-down sells are blocked by a daily close proxy with board-specific thresholds.
- Defensive minimum weight is enforced at 8% / 5% / 3% according to total-position regime when eligible defensive assets exist.
- Selection requires at least 250 historical trading days.

## Audit Issues

- pool_history_v15_9.csv not found; falling back to stock_pool_v15_7.csv.
- Static current pool creates survivor and current-constituent bias.
- historical stock-pool file is missing; current static pool was used.
- daily close data cannot fully prove intraday fill, suspension, ST, or true limit order availability.
- ST, suspension, delisting, and official daily limit-state files are not supplied.

## Fill Constraint Summary

| Block type | Count |
|---|---:|
| Buy blocked | 42 |
| Sell blocked | 8 |
| Limit proxy blocked | 50 |
| No-data blocked | 0 |

## Sample Blocked Fills

- 2024-03-08 buy blocked by limit_up_proxy: 002463
- 2024-03-13 buy blocked by limit_up_proxy: 000099
- 2024-03-27 buy blocked by limit_up_proxy: 000099
- 2024-03-28 buy blocked by limit_up_proxy: 001696
- 2024-03-28 buy blocked by limit_up_proxy: 000099
- 2024-03-29 buy blocked by limit_up_proxy: 001696
- 2024-03-29 buy blocked by limit_up_proxy: 000099
- 2024-04-10 buy blocked by limit_up_proxy: 001696
- 2024-04-10 buy blocked by limit_up_proxy: 000099
- 2024-04-11 sell blocked by limit_down_proxy: 001696
- 2024-04-11 sell blocked by limit_down_proxy: 002085
- 2024-04-18 buy blocked by limit_up_proxy: 000099
- 2024-04-19 buy blocked by limit_up_proxy: 002085
- 2024-04-19 buy blocked by limit_up_proxy: 000099
- 2024-04-25 buy blocked by limit_up_proxy: 002085
- 2024-04-29 buy blocked by limit_up_proxy: 002085
- 2024-08-01 buy blocked by limit_up_proxy: 001696
- 2024-08-02 buy blocked by limit_up_proxy: 001696
- 2024-08-06 buy blocked by limit_up_proxy: 002389
- 2024-09-27 buy blocked by limit_up_proxy: 002050
- 2024-09-30 buy blocked by limit_up_proxy: 601689
- 2024-09-30 buy blocked by limit_up_proxy: 002050
- 2024-10-08 buy blocked by limit_up_proxy: 300124
- 2024-10-08 buy blocked by limit_up_proxy: 688041
- 2024-10-18 buy blocked by limit_up_proxy: 001696
- 2024-10-21 buy blocked by limit_up_proxy: 001696
- 2024-10-22 buy blocked by limit_up_proxy: 001696
- 2024-11-01 buy blocked by limit_up_proxy: 002085
- 2024-11-05 buy blocked by limit_up_proxy: 000099
- 2024-11-06 buy blocked by limit_up_proxy: 000099

## Rolling Window Check

| Window | Total return | Annual return | Max drawdown | Sharpe | Calmar | Blocked fills |
|---|---:|---:|---:|---:|---:|---:|
| 2021-05-24 to 2023-06-16 | -0.24% | -0.12% | -4.56% | -0.02 | -0.03 | 0 |
| 2022-06-08 to 2024-07-03 | 3.74% | 1.86% | -13.32% | 0.21 | 0.14 | 16 |
| 2023-06-19 to 2025-07-17 | 0.49% | 0.25% | -20.14% | 0.09 | 0.01 | 26 |
| 2024-04-22 to 2026-05-22 | 98.33% | 40.93% | -17.31% | 1.83 | 2.36 | 9 |

## Decision

- This report is not allowed to be quoted as live-repeatable unless the label is FORMAL_REPLICABLE.
- Current implementation remains PROXY_RESEARCH until a true historical pool and fuller trade-state data are supplied.
