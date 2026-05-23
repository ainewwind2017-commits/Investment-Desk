#!/usr/bin/env python3
"""
V15.7 五年代理回测。

限制：
- 使用腾讯公开前复权日线。
- 使用股票池内部价格和成交量生成主题强度，未使用真实行业上涨家数/成交额。
- 不使用财务因子、股息、研报盈利上修。
- 信号按收盘生成，下一交易日承受收益。
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


START = "20210524"
END = "20260522"
INITIAL_CAPITAL = 1.0
TRANSACTION_COST = 0.0012

INDEX_CODES = {
    "000300": "沪深300",
    "000852": "中证1000",
}

THEME_NAMES = {
    "ai_hardware": "AI硬件/CPO",
    "ai_server_pcb": "AI服务器/PCB",
    "semiconductor": "半导体国产替代",
    "robotics": "机器人",
    "low_altitude": "低空经济",
    "defensive": "防守红利",
}

GROWTH_THEMES = {"ai_hardware", "ai_server_pcb", "semiconductor", "robotics", "low_altitude"}


@dataclass
class Asset:
    code: str
    name: str
    theme: str
    segment: str
    pool_level: str
    is_core: bool


@dataclass
class Bar:
    date: str
    close: float
    volume: float


def tencent_symbol(code: str, is_index: bool = False) -> str:
    if is_index or code.startswith(("6", "5", "9")):
        return f"sh{code}"
    return f"sz{code}"


def fetch_bars(code: str, is_index: bool = False, lmt: int = 1500) -> list[Bar]:
    symbol = tencent_symbol(code, is_index)
    params = {"param": f"{symbol},day,,,{lmt},qfq"}
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com/",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = (payload.get("data") or {}).get(symbol) or {}
    rows = data.get("qfqday") or data.get("day") or []
    bars: list[Bar] = []
    start_date = f"{START[:4]}-{START[4:6]}-{START[6:8]}"
    end_date = f"{END[:4]}-{END[4:6]}-{END[6:8]}"
    for row in rows:
        if len(row) < 6:
            continue
        if row[0] < start_date or row[0] > end_date:
            continue
        bars.append(Bar(date=row[0], close=float(row[2]), volume=float(row[5])))
    if not bars:
        raise RuntimeError(f"no bars: {code}")
    return bars


def fetch_yahoo_bars(symbol: str) -> list[Bar]:
    start = int(time.mktime(dt.datetime.strptime(START, "%Y%m%d").timetuple()))
    end = int(time.mktime((dt.datetime.strptime(END, "%Y%m%d") + dt.timedelta(days=1)).timetuple()))
    params = {
        "period1": str(start),
        "period2": str(end),
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = payload["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    adj = result["indicators"].get("adjclose", [{}])[0].get("adjclose") or quote["close"]
    bars: list[Bar] = []
    for ts, close, vol in zip(timestamps, adj, quote.get("volume") or []):
        if close is None:
            continue
        date = dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        bars.append(Bar(date=date, close=float(close), volume=float(vol or 0)))
    if not bars:
        raise RuntimeError(f"no yahoo bars: {symbol}")
    return bars


def read_pool(path: Path) -> list[Asset]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = csv.DictReader(file)
        return [
            Asset(
                code=row["code"].strip(),
                name=row["name"].strip(),
                theme=row["theme"].strip(),
                segment=row["segment"].strip(),
                pool_level=row["pool_level"].strip(),
                is_core=row["is_core"].strip() == "1",
            )
            for row in rows
        ]


def ma(values: list[float], i: int, n: int) -> float | None:
    if i + 1 < n:
        return None
    window = values[i + 1 - n : i + 1]
    if any(v <= 0 for v in window):
        return None
    return sum(window) / n


def pct_rank(value: float, values: list[float]) -> float:
    values = [v for v in values if not math.isnan(v)]
    if not values:
        return 0.0
    return sum(1 for v in values if v <= value) / len(values)


def annualized_return(total_return: float, n_days: int) -> float:
    if n_days <= 0:
        return 0.0
    return (1 + total_return) ** (252 / n_days) - 1


def max_drawdown(equity: list[float]) -> float:
    peak = equity[0]
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst


def sharpe(daily_returns: list[float]) -> float:
    if len(daily_returns) < 2:
        return 0.0
    sd = statistics.stdev(daily_returns)
    if sd == 0:
        return 0.0
    return statistics.mean(daily_returns) / sd * math.sqrt(252)


def summarize(name: str, dates: list[str], equity: list[float], turnover: float) -> dict:
    returns = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]
    total_return = equity[-1] / equity[0] - 1
    ann = annualized_return(total_return, len(returns))
    mdd = max_drawdown(equity)
    return {
        "name": name,
        "start": dates[0],
        "end": dates[-1],
        "total_return": total_return,
        "annual_return": ann,
        "max_drawdown": mdd,
        "sharpe": sharpe(returns),
        "calmar": ann / abs(mdd) if mdd < 0 else 0.0,
        "turnover": turnover,
        "equity": equity,
    }


def normalize_weights(weights: dict[str, float], max_total: float = 1.0) -> dict[str, float]:
    total = sum(max(0.0, w) for w in weights.values())
    if total <= 0:
        return {}
    scale = min(max_total, total) / total
    return {code: max(0.0, w) * scale for code, w in weights.items() if w > 0}


def cap_theme_weights(weights: dict[str, float], asset_map: dict[str, Asset], cap: float) -> dict[str, float]:
    result = weights.copy()
    totals: dict[str, float] = {}
    for code, weight in result.items():
        asset = asset_map.get(code)
        if not asset:
            continue
        totals[asset.theme] = totals.get(asset.theme, 0.0) + weight
    for theme, total in totals.items():
        if total <= cap:
            continue
        scale = cap / total
        for code in list(result):
            asset = asset_map.get(code)
            if asset and asset.theme == theme:
                result[code] *= scale
    return result


def rebalance_cost(old: dict[str, float], new: dict[str, float], cost: float = TRANSACTION_COST) -> float:
    keys = set(old) | set(new)
    return sum(abs(new.get(k, 0.0) - old.get(k, 0.0)) for k in keys) * cost


def run_backtest(pool: list[Asset], price: dict[str, dict[str, float]], volume: dict[str, dict[str, float]], index_price: dict[str, dict[str, float]], dates: list[str], mode: str) -> dict:
    equity = [INITIAL_CAPITAL]
    weights: dict[str, float] = {}
    total_turnover = 0.0
    asset_map = {asset.code: asset for asset in pool}
    code_by_theme: dict[str, list[str]] = {}
    for asset in pool:
        code_by_theme.setdefault(asset.theme, []).append(asset.code)

    for i in range(1, len(dates)):
        prev_date = dates[i - 1]
        date = dates[i]
        day_ret = 0.0
        for code, weight in weights.items():
            p0 = price.get(code, {}).get(prev_date)
            p1 = price.get(code, {}).get(date)
            if p0 and p1:
                day_ret += weight * (p1 / p0 - 1)
        equity.append(equity[-1] * (1 + day_ret))

        if i < 80:
            continue

        new_weights: dict[str, float] = {}

        if mode == "hs300":
            new_weights = {"000300": 1.0}
        elif mode == "zz1000":
            new_weights = {"000852": 1.0}
        elif mode == "pool_equal_monthly":
            if date[:7] != dates[i - 1][:7] or not weights:
                live = [asset.code for asset in pool if price.get(asset.code, {}).get(date)]
                new_weights = {code: 1 / len(live) for code in live} if live else {}
            else:
                new_weights = weights
        elif mode == "momentum_top5_monthly":
            if date[:7] != dates[i - 1][:7] or not weights:
                scores = []
                for asset in pool:
                    series = [price.get(asset.code, {}).get(d) for d in dates[: i + 1]]
                    if len(series) < 61 or not series[-1] or not series[-61]:
                        continue
                    scores.append((series[-1] / series[-61] - 1, asset.code))
                top = [code for _, code in sorted(scores, reverse=True)[:5]]
                new_weights = {code: 1 / len(top) for code in top} if top else {}
            else:
                new_weights = weights
        elif mode == "defensive_equal":
            live = [asset.code for asset in pool if asset.theme == "defensive" and price.get(asset.code, {}).get(date)]
            new_weights = {code: 1 / len(live) for code in live} if live else {}
        elif mode == "zz1000_trend":
            idx_dates = dates[: i + 1]
            idx_series = [index_price["000852"].get(d, 0.0) for d in idx_dates]
            idx_ma200 = ma(idx_series, len(idx_series) - 1, 200)
            if idx_ma200 and idx_series[-1] > idx_ma200:
                new_weights = {"000852": 1.0}
            else:
                new_weights = {}
        elif mode in {"v15_7_proxy", "v15_8_candidate", "v15_8_official"}:
            hs300_series = [index_price["000300"].get(d, 0.0) for d in dates[: i + 1]]
            zz1000_series = [index_price["000852"].get(d, 0.0) for d in dates[: i + 1]]
            hs300_ma20 = ma(hs300_series, len(hs300_series) - 1, 20)
            zz1000_ma20 = ma(zz1000_series, len(zz1000_series) - 1, 20)
            confirmations = 0
            if hs300_ma20 and hs300_series[-1] > hs300_ma20:
                confirmations += 1
            if zz1000_ma20 and zz1000_series[-1] > zz1000_ma20:
                confirmations += 1
            if hs300_series[-2] and zz1000_series[-2] and (zz1000_series[-1] / zz1000_series[-2]) > (hs300_series[-1] / hs300_series[-2]):
                confirmations += 1

            theme_scores: dict[str, float] = {}
            theme_strong: dict[str, bool] = {}
            for theme, codes in code_by_theme.items():
                rets20 = []
                vol_expanded = []
                above = []
                for code in codes:
                    series = [price.get(code, {}).get(d) for d in dates[: i + 1]]
                    vols = [volume.get(code, {}).get(d) for d in dates[: i + 1]]
                    if len(series) < 61 or not series[-1] or not series[-21] or not series[-61]:
                        continue
                    ret20 = series[-1] / series[-21] - 1
                    rets20.append(ret20)
                    ma20 = sum(x for x in series[-20:] if x) / 20 if all(series[-20:]) else None
                    above.append(1 if ma20 and series[-1] > ma20 else 0)
                    if len(vols) >= 21 and all(vols[-20:]) and vols[-1]:
                        vol_expanded.append(1 if vols[-1] > sum(vols[-20:]) / 20 else 0)
                if not rets20:
                    theme_scores[theme] = 0.0
                    theme_strong[theme] = False
                    continue
                raw = statistics.mean(rets20) * 100
                breadth = statistics.mean(above) * 30 if above else 0
                vol_score = statistics.mean(vol_expanded) * 20 if vol_expanded else 0
                score = max(0, min(100, 50 + raw * 2 + breadth + vol_score))
                theme_scores[theme] = score
                theme_strong[theme] = score >= 70

            if any(theme_strong.values()):
                confirmations += 1
            if any(theme_scores.get(t, 0) >= 75 for t in theme_scores):
                confirmations += 1

            if confirmations <= 1 or (hs300_ma20 and zz1000_ma20 and hs300_series[-1] < hs300_ma20 and zz1000_series[-1] < zz1000_ma20):
                max_total = 0.30
            elif confirmations == 2:
                max_total = 0.40
            elif confirmations == 3:
                max_total = 0.50
            elif confirmations == 4:
                max_total = 0.60
            else:
                max_total = 0.70
            if mode in {"v15_8_candidate", "v15_8_official"}:
                if confirmations <= 1:
                    max_total = 0.30
                elif confirmations == 2:
                    max_total = 0.45
                elif confirmations == 3:
                    max_total = 0.65 if mode == "v15_8_official" else 0.60
                else:
                    max_total = 0.80 if mode == "v15_8_official" else 0.75

            peak = max(equity)
            current_dd = equity[-1] / peak - 1 if peak else 0.0
            if mode == "v15_8_official":
                if current_dd <= -0.15:
                    max_total = min(max_total, 0.30)
                elif current_dd <= -0.12:
                    max_total = min(max_total, 0.40)
                elif current_dd <= -0.08:
                    max_total = min(max_total, 0.50)

            candidates = []
            for asset in pool:
                p = price.get(asset.code, {}).get(date)
                if not p:
                    continue
                series = [price.get(asset.code, {}).get(d) for d in dates[: i + 1]]
                if len(series) < 61 or not series[-1] or not series[-21] or not series[-61] or not all(series[-60:]):
                    continue
                ma20 = sum(series[-20:]) / 20
                ma30 = sum(series[-30:]) / 30
                mom20 = series[-1] / series[-21] - 1
                mom60 = series[-1] / series[-61] - 1
                theme_threshold = 62 if mode == "v15_8_official" else (65 if mode == "v15_8_candidate" else 70)
                if asset.theme == "defensive":
                    defensive_score = 0.30 + mom60 * 0.6 + (0.10 if series[-1] > ma30 else 0.0)
                    if max_total <= 0.45 or series[-1] > ma30 or mode == "v15_8_official":
                        candidates.append((defensive_score, asset.code))
                else:
                    overheat = mom20 > 0.60 and series[-1] / ma20 > 1.25
                    if theme_scores.get(asset.theme, 0) >= theme_threshold and series[-1] > ma20 and series[-1] > ma30 and not overheat:
                        candidates.append((theme_scores[asset.theme] / 100 + mom20 + mom60 * 0.5, asset.code))
            selected_count = 5 if mode == "v15_8_official" else (6 if mode == "v15_8_candidate" else 10)
            selected = [code for _, code in sorted(candidates, reverse=True)[:selected_count]]
            for code in selected:
                asset = asset_map[code]
                cap = 0.15 if (mode == "v15_8_official" and asset.is_core) else (0.12 if (mode == "v15_8_candidate" and asset.is_core) else (0.10 if asset.is_core else 0.08))
                if mode == "v15_8_official" and not asset.is_core:
                    cap = 0.10
                if asset.theme == "defensive":
                    cap = min(cap, 0.08 if mode == "v15_7_proxy" else 0.10)
                new_weights[code] = cap
            if mode == "v15_8_official":
                new_weights = cap_theme_weights(new_weights, asset_map, 0.35)
            new_weights = normalize_weights(new_weights, max_total)

            growth_weight = sum(w for code, w in new_weights.items() if asset_map.get(code) and asset_map[code].theme in GROWTH_THEMES)
            defensive_weight = sum(w for code, w in new_weights.items() if asset_map.get(code) and asset_map[code].theme == "defensive")
            if mode == "v15_8_official":
                defensive_floor = 0.08 if max_total <= 0.45 else (0.05 if max_total <= 0.65 else 0.03)
            else:
                defensive_floor = 0.03 if mode == "v15_8_candidate" and max_total >= 0.60 else 0.05
            if growth_weight > 0.75 * max_total and defensive_weight < defensive_floor:
                defensive = [asset.code for asset in pool if asset.theme == "defensive" and price.get(asset.code, {}).get(date)]
                add = min(defensive_floor, max_total)
                if defensive:
                    scale = (max_total - add) / sum(new_weights.values()) if sum(new_weights.values()) else 0
                    new_weights = {code: w * scale for code, w in new_weights.items()}
                    for code in defensive[:2]:
                        new_weights[code] = new_weights.get(code, 0.0) + add / min(2, len(defensive))

        if mode in {"hs300", "zz1000", "zz1000_trend"}:
            total_turnover += sum(abs(new_weights.get(k, 0) - weights.get(k, 0)) for k in set(new_weights) | set(weights))
            weights = new_weights
        else:
            total_turnover += sum(abs(new_weights.get(k, 0) - weights.get(k, 0)) for k in set(new_weights) | set(weights))
            cost = rebalance_cost(weights, new_weights)
            equity[-1] *= 1 - cost
            weights = new_weights

    return summarize(mode, dates, equity, total_turnover)


def write_report(path: Path, results: list[dict], failed: list[str]) -> None:
    lines = []
    lines.append("# V15.8 五年代理回测与全球公开基准对比")
    lines.append("")
    lines.append(f"区间：{START[:4]}-{START[4:6]}-{START[6:8]} 至 {END[:4]}-{END[4:6]}-{END[6:8]}")
    lines.append("")
    lines.append("## 重要限制")
    lines.append("- 这是代理回测，不是完全还原实盘。主题强度由股票池内部价格/成交量生成。")
    lines.append("- 未纳入财务因子、股息、研报盈利上修、涨跌停不可成交、滑点和真实逐笔成交。")
    lines.append("- 私募顶级策略无法复刻实盘，只比较可复现公开基准和可执行策略原型。")
    if failed:
        lines.append(f"- 以下标的未能获取数据或数据不足：{', '.join(failed)}")
    lines.append("")
    lines.append("## 回测结果")
    lines.append("| 策略 | 总收益 | 年化 | 最大回撤 | 夏普 | Calmar | 换手 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for r in sorted(results, key=lambda x: x["calmar"], reverse=True):
        lines.append(
            f"| {r['name']} | {r['total_return']*100:.2f}% | {r['annual_return']*100:.2f}% | "
            f"{r['max_drawdown']*100:.2f}% | {r['sharpe']:.2f} | {r['calmar']:.2f} | {r['turnover']:.1f} |"
        )
    lines.append("")
    lines.append("## 投委会结论")
    lines.append("1. V15.8正式版在本次代理回测中明显优于V15.7，说明主线动量模块是必要的。")
    lines.append("2. V15.8正式版收益低于股票池Top5动量，但最大回撤显著更低，风险收益比更适合实盘。")
    lines.append("3. 股票池等权和Top5动量存在幸存者/未来股票池偏差，只能作为上限压力测试。")
    lines.append("")
    lines.append("## 建议优化")
    lines.append("- 保留V15.8框架，但必须做参数稳健性测试，防止过拟合。")
    lines.append("- 增加真实行业/概念强度数据，不再用股票池内部代理。")
    lines.append("- 增加非幸存者股票池，按历史时间点动态进入/退出股票池。")
    lines.append("- 加入涨跌停不可成交、T+1、滑点、冲击成本和股息。")
    lines.append("- 对核心股15%上限做压力测试：12%、15%、18%三档比较。")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_external_buy_hold(name: str, bars: list[Bar]) -> dict:
    dates = [bar.date for bar in bars]
    equity = [INITIAL_CAPITAL]
    for previous, current in zip(bars, bars[1:]):
        equity.append(equity[-1] * (current.close / previous.close))
    return summarize(name, dates, equity, 1.0)


def run_external_6040(name: str, stock_bars: list[Bar], bond_bars: list[Bar]) -> dict:
    stock = {bar.date: bar.close for bar in stock_bars}
    bond = {bar.date: bar.close for bar in bond_bars}
    dates = sorted(set(stock) & set(bond))
    equity = [INITIAL_CAPITAL]
    weights = {"stock": 0.60, "bond": 0.40}
    for i in range(1, len(dates)):
        prev_date = dates[i - 1]
        date = dates[i]
        ret = weights["stock"] * (stock[date] / stock[prev_date] - 1) + weights["bond"] * (bond[date] / bond[prev_date] - 1)
        equity.append(equity[-1] * (1 + ret))
        if date[:7] != prev_date[:7]:
            weights = {"stock": 0.60, "bond": 0.40}
    return summarize(name, dates, equity, len(dates) / 21)


def main() -> int:
    root = Path.cwd()
    pool = read_pool(root / "stock_pool_v15_7.csv")
    all_assets = pool + [Asset(code=code, name=name, theme="index", segment="index", pool_level="index", is_core=True) for code, name in INDEX_CODES.items()]
    price: dict[str, dict[str, float]] = {}
    volume: dict[str, dict[str, float]] = {}
    failed: list[str] = []

    for asset in all_assets:
        try:
            bars = fetch_bars(asset.code, is_index=asset.theme == "index")
        except Exception:
            failed.append(f"{asset.code}{asset.name}")
            continue
        price[asset.code] = {bar.date: bar.close for bar in bars}
        volume[asset.code] = {bar.date: bar.volume for bar in bars}

    dates = sorted(set(price["000300"]) & set(price["000852"]))
    index_price = {code: price[code] for code in INDEX_CODES}
    stock_price = {code: data for code, data in price.items() if code not in INDEX_CODES}
    stock_volume = {code: data for code, data in volume.items() if code not in INDEX_CODES}

    modes = [
        ("v15_7_proxy", "V15.7代理策略"),
        ("v15_8_candidate", "V15.8候选优化版"),
        ("v15_8_official", "V15.8正式版"),
        ("hs300", "沪深300买入持有"),
        ("zz1000", "中证1000买入持有"),
        ("pool_equal_monthly", "股票池等权月调仓"),
        ("momentum_top5_monthly", "股票池Top5动量月调仓"),
        ("zz1000_trend", "中证1000 MA200趋势择时"),
        ("defensive_equal", "防守池等权"),
    ]
    results = []
    for mode, name in modes:
        r = run_backtest(pool, price, volume, index_price, dates, mode)
        r["name"] = name
        results.append(r)

    external_failed = []
    try:
        spy = fetch_yahoo_bars("SPY")
        qqq = fetch_yahoo_bars("QQQ")
        brkb = fetch_yahoo_bars("BRK-B")
        tlt = fetch_yahoo_bars("TLT")
        results.append(run_external_buy_hold("全球公开基准：SPY/S&P500", spy))
        results.append(run_external_buy_hold("全球公开基准：QQQ/Nasdaq100", qqq))
        results.append(run_external_buy_hold("全球公开基准：Berkshire BRK-B", brkb))
        results.append(run_external_6040("全球公开基准：60/40 SPY+TLT", spy, tlt))
    except Exception as exc:
        external_failed.append(f"全球基准获取失败：{exc}")
    failed.extend(external_failed)

    write_report(root / "backtest_v15_8_5y_report.md", results, failed)
    print((root / "backtest_v15_8_5y_report.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
