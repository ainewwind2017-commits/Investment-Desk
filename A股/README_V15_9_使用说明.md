# A股 DESK PRO V15.9 使用说明

这个文件夹里放的是 V15.9 回测闸门的最小可运行文件。

## 先运行这个

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\run_v15_9_replicable_backtest.py
```

运行后会生成或更新：

```text
backtest_v15_9_replicable_report.md
```

## 文件说明

- `run_v15_9_replicable_backtest.py`：入口脚本。
- `backtest_v15_9_realistic.py`：V15.9 核心回测闸门。
- `backtest_v15_7.py`：行情拉取和基础统计函数，V15.9 仍依赖它。
- `stock_pool_v15_7.csv`：当前静态股票池。使用它回测历史只能标记为 `PROXY_RESEARCH`。
- `pool_history_v15_9_template.csv`：历史动态股票池模板。
- `A-share_DESK_PRO_V15.9_replicable.md`：V15.9 标准说明。
- `backtest_v15_9_replicable_report.md`：当前生成的样例报告。

## 关键边界

当前没有正式的 `pool_history_v15_9.csv`，也没有完整的 ST、停牌、退市、官方涨跌停状态数据。

所以即使脚本能运行，报告也必须保持：

```text
PROXY_RESEARCH
```

不能声称为：

```text
FORMAL_REPLICABLE
```

要升级为正式可复制回测，需要先把 `pool_history_v15_9_template.csv` 另存为 `pool_history_v15_9.csv`，并用历史当时已知信息补齐每只股票的 `effective_from`、`effective_to` 和 `source`。
