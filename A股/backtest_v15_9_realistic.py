#!/usr/bin/env python3
"""
V15.9 realistic backtest gate.

This script adds execution realism to the existing V15.8 research engine:
- date-stamped stock-pool support;
- one-trading-day signal lag;
- limit-up / limit-down no-fill proxy;
- buy/sell cost split with stamp duty, slippage, and impact buffer;
- report labels that prevent proxy results from being treated as live-repeatable.

The script still uses daily close data. Without a historical pool file and
proper OHLCV / suspension / ST / delisting data, the result remains
PROXY_RESEARCH.
"""

from __future__ import annotations

import csv
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import backtest_v15_7 as base


POOL_HISTORY = "pool_history_v15_9.csv"
STATIC_POOL = "stock_pool_v15_7.csv"
TRADE_STATE = "trade_state_v15_9.csv"
CAPACITY_LIMITS = "capacity_limits_v15_9.csv"
REPORT = "backtest_v15_9_replicable_report.md"

START = base.START
END = base.END

COMMISSION = 0.0003
STAMP_DUTY = 0.0005
SLIPPAGE = 0.0010
IMPACT_BUFFER = 0.0007

CORE_CAP = 0.15
NORMAL_CAP = 0.10
DEFENSIVE_CAP = 0.10
THEME_CAP = 0.30
THEME_THRESHOLD = 65
MIN_HISTORY_DAYS = 250
MAX_FILL_NOTE_SAMPLES = 30
ROLLING_WINDOW_DAYS = 504
ROLLING_STEP_DAYS = 252


@dataclass(frozen=True)
class PoolRow:
    effective_from: str
    effective_to: str
    asset: base.Asset
    source: str


@dataclass(frozen=True)
class TradeState:
    is_suspended: bool
    is_st: bool
    is_delisted: bool
    limit_up: bool
    limit_down: bool
    source: str


@dataclass(frozen=True)
class CapacityLimit:
    max_buy_delta: float
    max_sell_delta: float
    source: str


@dataclass
class FillStats:
    buy_blocked: int = 0
    sell_blocked: int = 0
    no_data_blocked: int = 0
    proxy_limit_blocked: int = 0
    official_limit_blocked: int = 0
    suspended_blocked: int = 0
    delisted_blocked: int = 0
    st_blocked: int = 0
    capacity_limited: int = 0
    blocked_sell_targets: dict[str, float] = field(default_factory=dict)
    sample_notes: list[str] = field(default_factory=list)

    def add(self, side: str, trade_date: str, code: str, reason: str, target_weight: float | None = None) -> None:
        if side == "buy":
            self.buy_blocked += 1
        else:
            self.sell_blocked += 1
            if target_weight is not None:
                current = self.blocked_sell_targets.get(code, target_weight)
                self.blocked_sell_targets[code] = min(current, target_weight)
        if reason == "no_data":
            self.no_data_blocked += 1
        elif reason in {"limit_up_proxy", "limit_down_proxy"}:
            self.proxy_limit_blocked += 1
        elif reason in {"official_limit_up", "official_limit_down"}:
            self.official_limit_blocked += 1
        elif reason == "suspended":
            self.suspended_blocked += 1
        elif reason == "delisted":
            self.delisted_blocked += 1
        elif reason == "st_buy_blocked":
            self.st_blocked += 1
        else:
            self.capacity_limited += 1
        if len(self.sample_notes) < MAX_FILL_NOTE_SAMPLES:
            self.sample_notes.append(f"{trade_date} {side} blocked by {reason}: {code}")

    def add_capacity_note(self, trade_date: str, code: str, side: str) -> None:
        self.capacity_limited += 1
        if len(self.sample_notes) < MAX_FILL_NOTE_SAMPLES:
            self.sample_notes.append(f"{trade_date} {side} limited by capacity: {code}")

    def merge(self, other: "FillStats") -> None:
        self.buy_blocked += other.buy_blocked
        self.sell_blocked += other.sell_blocked
        self.no_data_blocked += other.no_data_blocked
        self.proxy_limit_blocked += other.proxy_limit_blocked
        self.official_limit_blocked += other.official_limit_blocked
        self.suspended_blocked += other.suspended_blocked
        self.delisted_blocked += other.delisted_blocked
        self.st_blocked += other.st_blocked
        self.capacity_limited += other.capacity_limited
        for code, target_weight in other.blocked_sell_targets.items():
            current = self.blocked_sell_targets.get(code, target_weight)
            self.blocked_sell_targets[code] = min(current, target_weight)
        remaining = MAX_FILL_NOTE_SAMPLES - len(self.sample_notes)
        if remaining > 0:
            self.sample_notes.extend(other.sample_notes[:remaining])


@dataclass(frozen=True)
class PendingOrder:
    signal_date: str
    target: dict[str, float]


def to_iso(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if "-" in value:
        return value
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def parse_bool(value: str) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y"}


def parse_float(value: str, default: float = 0.0) -> float:
    value = (value or "").strip()
    if not value:
        return default
    return float(value)


def read_pool_history(root: Path) -> tuple[list[PoolRow], list[str], str]:
    history_path = root / POOL_HISTORY
    warnings: list[str] = []
    if history_path.exists():
        rows: list[PoolRow] = []
        with history_path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            required = {
                "effective_from",
                "effective_to",
                "code",
                "name",
                "theme",
                "segment",
                "pool_level",
                "is_core",
                "source",
            }
            missing = sorted(required - set(reader.fieldnames or []))
            if missing:
                raise RuntimeError(f"{POOL_HISTORY} missing columns: {', '.join(missing)}")
            for row in reader:
                rows.append(
                    PoolRow(
                        effective_from=to_iso(row["effective_from"]),
                        effective_to=to_iso(row.get("effective_to", "")),
                        asset=base.Asset(
                            code=row["code"].strip(),
                            name=row["name"].strip(),
                            theme=row["theme"].strip(),
                            segment=row["segment"].strip(),
                            pool_level=row["pool_level"].strip(),
                            is_core=row["is_core"].strip() in {"1", "true", "TRUE", "yes"},
                        ),
                        source=row["source"].strip(),
                    )
                )
        if not rows:
            raise RuntimeError(f"{POOL_HISTORY} has no rows")
        if any(not row.effective_from for row in rows):
            warnings.append(f"{POOL_HISTORY} has rows without effective_from.")
        if not any(row.effective_to for row in rows):
            warnings.append(f"{POOL_HISTORY} has no expired rows; survivor-bias coverage is not proven.")
        if any(not row.source for row in rows):
            warnings.append(f"{POOL_HISTORY} has rows without source evidence.")
        return rows, warnings, "history"

    warnings.append(f"{POOL_HISTORY} not found; falling back to {STATIC_POOL}.")
    warnings.append("Static current pool creates survivor and current-constituent bias.")
    rows = [
        PoolRow(
            effective_from=f"{START[:4]}-{START[4:6]}-{START[6:8]}",
            effective_to="",
            asset=asset,
            source="static_current_pool_proxy",
        )
        for asset in base.read_pool(root / STATIC_POOL)
    ]
    return rows, warnings, "static_proxy"


def read_trade_state(root: Path) -> tuple[dict[tuple[str, str], TradeState], list[str], str]:
    path = root / TRADE_STATE
    warnings: list[str] = []
    if not path.exists():
        warnings.append(f"{TRADE_STATE} not found; official suspension/ST/delist/limit-state checks are missing.")
        warnings.append("Daily return limit checks remain proxy-only and cannot prove real execution availability.")
        return {}, warnings, "missing"

    states: dict[tuple[str, str], TradeState] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        required = {
            "date",
            "code",
            "is_suspended",
            "is_st",
            "is_delisted",
            "limit_up",
            "limit_down",
            "source",
        }
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise RuntimeError(f"{TRADE_STATE} missing columns: {', '.join(missing)}")
        for row in reader:
            date = to_iso(row["date"])
            code = row["code"].strip()
            if not date or not code:
                continue
            states[(date, code)] = TradeState(
                is_suspended=parse_bool(row["is_suspended"]),
                is_st=parse_bool(row["is_st"]),
                is_delisted=parse_bool(row["is_delisted"]),
                limit_up=parse_bool(row["limit_up"]),
                limit_down=parse_bool(row["limit_down"]),
                source=row["source"].strip(),
            )

    if not states:
        warnings.append(f"{TRADE_STATE} has no usable rows; execution-state checks are missing.")
        return states, warnings, "missing"
    if any(not item.source for item in states.values()):
        warnings.append(f"{TRADE_STATE} has rows without source evidence.")
    return states, warnings, "official"


def read_capacity_limits(root: Path) -> tuple[dict[tuple[str, str], CapacityLimit], list[str], str]:
    path = root / CAPACITY_LIMITS
    warnings: list[str] = []
    if not path.exists():
        warnings.append(f"{CAPACITY_LIMITS} not found; trade-size capacity is not proven.")
        return {}, warnings, "missing"

    limits: dict[tuple[str, str], CapacityLimit] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        required = {"date", "code", "max_buy_delta", "max_sell_delta", "source"}
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise RuntimeError(f"{CAPACITY_LIMITS} missing columns: {', '.join(missing)}")
        for row in reader:
            date = to_iso(row["date"])
            code = row["code"].strip()
            if not date or not code:
                continue
            limits[(date, code)] = CapacityLimit(
                max_buy_delta=max(0.0, parse_float(row["max_buy_delta"])),
                max_sell_delta=max(0.0, parse_float(row["max_sell_delta"])),
                source=row["source"].strip(),
            )

    if not limits:
        warnings.append(f"{CAPACITY_LIMITS} has no usable rows; capacity checks are missing.")
        return limits, warnings, "missing"
    if any(not item.source for item in limits.values()):
        warnings.append(f"{CAPACITY_LIMITS} has rows without source evidence.")
    return limits, warnings, "official"


def active_assets(pool_rows: list[PoolRow], date: str) -> list[base.Asset]:
    active: list[base.Asset] = []
    for row in pool_rows:
        if row.effective_from and date < row.effective_from:
            continue
        if row.effective_to and date > row.effective_to:
            continue
        active.append(row.asset)
    return active


def daily_return(price: dict[str, dict[str, float]], code: str, prev_date: str, date: str) -> float | None:
    p0 = price.get(code, {}).get(prev_date)
    p1 = price.get(code, {}).get(date)
    if not p0 or not p1:
        return None
    return p1 / p0 - 1


def limit_threshold(code: str) -> float:
    if code.startswith(("688", "689", "300", "301")):
        return 0.198
    if code.startswith(("43", "83", "87", "88", "92")):
        return 0.298
    return 0.098


def trade_block_reason(
    price: dict[str, dict[str, float]],
    code: str,
    prev_date: str,
    trade_date: str,
    side: str,
    trade_state: dict[tuple[str, str], TradeState],
) -> str:
    state = trade_state.get((trade_date, code))
    if state:
        # 中文注释：正式交易状态优先于涨跌幅代理，避免把停牌、ST、退市或封板误判为可成交。
        if state.is_delisted:
            return "delisted"
        if state.is_suspended:
            return "suspended"
        if side == "buy" and state.is_st:
            return "st_buy_blocked"
        if side == "buy" and state.limit_up:
            return "official_limit_up"
        if side == "sell" and state.limit_down:
            return "official_limit_down"

    ret = daily_return(price, code, prev_date, trade_date)
    if ret is None:
        return "no_data"
    threshold = limit_threshold(code)
    if side == "buy" and ret >= threshold:
        return "limit_up_proxy"
    if side == "sell" and ret <= -threshold:
        return "limit_down_proxy"
    return ""


def can_buy(
    price: dict[str, dict[str, float]],
    code: str,
    prev_date: str,
    trade_date: str,
    trade_state: dict[tuple[str, str], TradeState],
) -> bool:
    return not trade_block_reason(price, code, prev_date, trade_date, "buy", trade_state)


def can_sell(
    price: dict[str, dict[str, float]],
    code: str,
    prev_date: str,
    trade_date: str,
    trade_state: dict[tuple[str, str], TradeState],
) -> bool:
    return not trade_block_reason(price, code, prev_date, trade_date, "sell", trade_state)


def apply_capacity_limits(
    old: dict[str, float],
    target: dict[str, float],
    capacity_limits: dict[tuple[str, str], CapacityLimit],
    trade_date: str,
) -> tuple[dict[str, float], FillStats]:
    if not capacity_limits:
        return target, FillStats()

    adjusted = target.copy()
    stats = FillStats()
    for code in set(old) | set(target):
        before = old.get(code, 0.0)
        after = target.get(code, 0.0)
        limit = capacity_limits.get((trade_date, code))
        if not limit:
            continue
        delta = after - before
        if delta > limit.max_buy_delta:
            # 中文注释：容量约束只限制单日变动幅度，不改变策略信号本身。
            adjusted[code] = before + limit.max_buy_delta
            stats.add_capacity_note(trade_date, code, "buy")
        elif -delta > limit.max_sell_delta:
            adjusted[code] = before - limit.max_sell_delta
            stats.add_capacity_note(trade_date, code, "sell")
    return {k: v for k, v in adjusted.items() if v > 1e-9}, stats


def apply_fill_constraints(
    old: dict[str, float],
    target: dict[str, float],
    price: dict[str, dict[str, float]],
    prev_date: str,
    trade_date: str,
    trade_state: dict[tuple[str, str], TradeState],
) -> tuple[dict[str, float], FillStats]:
    filled = old.copy()
    stats = FillStats()
    keys = set(old) | set(target)
    for code in keys:
        before = old.get(code, 0.0)
        after = target.get(code, 0.0)
        if after > before:
            reason = trade_block_reason(price, code, prev_date, trade_date, "buy", trade_state)
            if not reason:
                filled[code] = after
            else:
                filled[code] = before
                stats.add("buy", trade_date, code, reason)
        elif after < before:
            reason = trade_block_reason(price, code, prev_date, trade_date, "sell", trade_state)
            if not reason:
                filled[code] = after
            else:
                filled[code] = before
                stats.add("sell", trade_date, code, reason, after)
    return {k: v for k, v in filled.items() if v > 1e-9}, stats


def execution_cost(old: dict[str, float], new: dict[str, float]) -> float:
    buy_cost = COMMISSION + SLIPPAGE + IMPACT_BUFFER
    sell_cost = COMMISSION + STAMP_DUTY + SLIPPAGE + IMPACT_BUFFER
    cost = 0.0
    for code in set(old) | set(new):
        delta = new.get(code, 0.0) - old.get(code, 0.0)
        if delta > 0:
            cost += delta * buy_cost
        elif delta < 0:
            cost += abs(delta) * sell_cost
    return cost


def cap_theme_weights(weights: dict[str, float], asset_map: dict[str, base.Asset], cap: float) -> dict[str, float]:
    result = weights.copy()
    totals: dict[str, float] = {}
    for code, weight in result.items():
        asset = asset_map.get(code)
        if asset:
            totals[asset.theme] = totals.get(asset.theme, 0.0) + weight
    for theme, total in totals.items():
        if total > cap:
            scale = cap / total
            for code in list(result):
                asset = asset_map.get(code)
                if asset and asset.theme == theme:
                    result[code] *= scale
    return result


def defensive_floor(max_total: float) -> float:
    if max_total <= 0.45:
        return 0.08
    if max_total <= 0.65:
        return 0.05
    return 0.03


def eligible_defensive_codes(
    pool: list[base.Asset],
    price: dict[str, dict[str, float]],
    dates: list[str],
    i: int,
) -> list[str]:
    candidates: list[tuple[float, str]] = []
    for asset in pool:
        if asset.theme != "defensive":
            continue
        series = [price.get(asset.code, {}).get(d) for d in dates[: i + 1]]
        if len(series) < max(MIN_HISTORY_DAYS, 61):
            continue
        if not series[-1] or not series[-61] or not all(series[-60:]):
            continue
        ma30 = sum(series[-30:]) / 30
        mom60 = series[-1] / series[-61] - 1
        score = 0.30 + mom60 * 0.6 + (0.10 if series[-1] > ma30 else 0.0)
        candidates.append((score, asset.code))
    return [code for _, code in sorted(candidates, reverse=True)]


def enforce_defensive_floor(
    weights: dict[str, float],
    pool: list[base.Asset],
    asset_map: dict[str, base.Asset],
    price: dict[str, dict[str, float]],
    dates: list[str],
    i: int,
    max_total: float,
) -> dict[str, float]:
    floor_value = min(defensive_floor(max_total), max_total)
    if floor_value <= 0:
        return weights

    result = weights.copy()
    defensive_weight = sum(
        weight for code, weight in result.items() if asset_map.get(code) and asset_map[code].theme == "defensive"
    )
    if defensive_weight >= floor_value:
        return result

    defensive_codes = eligible_defensive_codes(pool, price, dates, i)
    if not defensive_codes:
        return result

    add = floor_value - defensive_weight
    current_total = sum(result.values())
    excess = max(0.0, current_total + add - max_total)
    if excess > 0:
        growth_codes = [
            code for code in result if asset_map.get(code) and asset_map[code].theme != "defensive"
        ]
        reducible = sum(result[code] for code in growth_codes)
        if reducible > 0:
            scale = max(0.0, (reducible - excess) / reducible)
            for code in growth_codes:
                result[code] *= scale
        else:
            add = max(0.0, max_total - current_total)

    slots = defensive_codes[:2]
    if add <= 0 or not slots:
        return {code: weight for code, weight in result.items() if weight > 1e-9}
    for code in slots:
        result[code] = result.get(code, 0.0) + add / len(slots)
    return {code: weight for code, weight in result.items() if weight > 1e-9}


def generate_target(
    pool_rows: list[PoolRow],
    price: dict[str, dict[str, float]],
    volume: dict[str, dict[str, float]],
    index_price: dict[str, dict[str, float]],
    dates: list[str],
    i: int,
    equity: list[float],
) -> dict[str, float]:
    date = dates[i]
    pool = active_assets(pool_rows, date)
    asset_map = {asset.code: asset for asset in pool}
    code_by_theme: dict[str, list[str]] = {}
    for asset in pool:
        code_by_theme.setdefault(asset.theme, []).append(asset.code)

    hs300_series = [index_price["000300"].get(d, 0.0) for d in dates[: i + 1]]
    zz1000_series = [index_price["000852"].get(d, 0.0) for d in dates[: i + 1]]
    hs300_ma20 = base.ma(hs300_series, len(hs300_series) - 1, 20)
    zz1000_ma20 = base.ma(zz1000_series, len(zz1000_series) - 1, 20)

    confirmations = 0
    confirmations += int(bool(hs300_ma20 and hs300_series[-1] > hs300_ma20))
    confirmations += int(bool(zz1000_ma20 and zz1000_series[-1] > zz1000_ma20))
    if hs300_series[-2] and zz1000_series[-2]:
        confirmations += int((zz1000_series[-1] / zz1000_series[-2]) > (hs300_series[-1] / hs300_series[-2]))

    theme_scores: dict[str, float] = {}
    for theme, codes in code_by_theme.items():
        rets20 = []
        vol_expanded = []
        above = []
        for code in codes:
            series = [price.get(code, {}).get(d) for d in dates[: i + 1]]
            vols = [volume.get(code, {}).get(d) for d in dates[: i + 1]]
            if len(series) < 61 or not series[-1] or not series[-21] or not series[-61] or not all(series[-60:]):
                continue
            ret20 = series[-1] / series[-21] - 1
            ma20 = sum(series[-20:]) / 20
            rets20.append(ret20)
            above.append(1 if series[-1] > ma20 else 0)
            if len(vols) >= 21 and all(vols[-20:]) and vols[-1]:
                vol_expanded.append(1 if vols[-1] > sum(vols[-20:]) / 20 else 0)
        if not rets20:
            theme_scores[theme] = 0.0
            continue
        raw = statistics.mean(rets20) * 100
        breadth = statistics.mean(above) * 30 if above else 0
        vol_score = statistics.mean(vol_expanded) * 20 if vol_expanded else 0
        theme_scores[theme] = max(0.0, min(100.0, 50 + raw * 2 + breadth + vol_score))

    confirmations += int(any(score >= THEME_THRESHOLD for score in theme_scores.values()))
    confirmations += int(any(score >= 75 for score in theme_scores.values()))

    if confirmations <= 1:
        max_total = 0.30
    elif confirmations == 2:
        max_total = 0.45
    elif confirmations == 3:
        max_total = 0.65
    else:
        max_total = 0.80

    peak = max(equity)
    current_dd = equity[-1] / peak - 1 if peak else 0.0
    if current_dd <= -0.15:
        max_total = min(max_total, 0.30)
    elif current_dd <= -0.12:
        max_total = min(max_total, 0.40)
    elif current_dd <= -0.08:
        max_total = min(max_total, 0.50)

    candidates = []
    for asset in pool:
        series = [price.get(asset.code, {}).get(d) for d in dates[: i + 1]]
        if len(series) < max(MIN_HISTORY_DAYS, 61):
            continue
        if not series[-1] or not series[-21] or not series[-61] or not all(series[-60:]):
            continue
        ma20 = sum(series[-20:]) / 20
        ma30 = sum(series[-30:]) / 30
        mom20 = series[-1] / series[-21] - 1
        mom60 = series[-1] / series[-61] - 1
        if asset.theme == "defensive":
            score = 0.30 + mom60 * 0.6 + (0.10 if series[-1] > ma30 else 0.0)
            candidates.append((score, asset.code))
            continue
        overheat = mom20 > 0.60 and series[-1] / ma20 > 1.25
        if theme_scores.get(asset.theme, 0.0) >= THEME_THRESHOLD and series[-1] > ma20 and series[-1] > ma30 and not overheat:
            score = theme_scores[asset.theme] / 100 + mom20 + mom60 * 0.5
            candidates.append((score, asset.code))

    selected = [code for _, code in sorted(candidates, reverse=True)[:5]]
    target: dict[str, float] = {}
    for code in selected:
        asset = asset_map[code]
        if asset.theme == "defensive":
            cap = DEFENSIVE_CAP
        else:
            cap = CORE_CAP if asset.is_core else NORMAL_CAP
        target[code] = cap

    target = cap_theme_weights(target, asset_map, THEME_CAP)
    target = base.normalize_weights(target, max_total)
    target = enforce_defensive_floor(target, pool, asset_map, price, dates, i, max_total)
    target = cap_theme_weights(target, asset_map, THEME_CAP)
    return base.normalize_weights(target, max_total)


def run_realistic_backtest(
    pool_rows: list[PoolRow],
    price: dict[str, dict[str, float]],
    volume: dict[str, dict[str, float]],
    index_price: dict[str, dict[str, float]],
    dates: list[str],
) -> tuple[dict, FillStats]:
    equity = [base.INITIAL_CAPITAL]
    weights: dict[str, float] = {}
    pending_order: PendingOrder | None = None
    total_turnover = 0.0
    fill_stats = FillStats()

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

        if pending_order is not None:
            # 中文注释：信号只能在收盘后生成，最早下一交易日执行；这里用硬校验防止同日信号同日成交。
            if pending_order.signal_date >= date:
                raise RuntimeError(f"future/same-day execution blocked: signal {pending_order.signal_date}, trade {date}")
            filled, stats = apply_fill_constraints(weights, pending_order.target, price, prev_date, date)
            total_turnover += sum(abs(filled.get(k, 0.0) - weights.get(k, 0.0)) for k in set(filled) | set(weights))
            equity[-1] *= 1 - execution_cost(weights, filled)
            weights = filled
            fill_stats.merge(stats)

        if i >= MIN_HISTORY_DAYS:
            # 中文注释：目标仓位只使用截至 signal_date 收盘的数据，保存为待执行订单，下一轮循环才允许成交。
            pending_order = PendingOrder(
                signal_date=date,
                target=generate_target(pool_rows, price, volume, index_price, dates, i, equity),
            )

    result = base.summarize("V15.9 realistic execution", dates, equity, total_turnover)
    return result, fill_stats


def run_rolling_oos(
    pool_rows: list[PoolRow],
    price: dict[str, dict[str, float]],
    volume: dict[str, dict[str, float]],
    index_price: dict[str, dict[str, float]],
    dates: list[str],
) -> list[dict]:
    if len(dates) < ROLLING_WINDOW_DAYS:
        return []
    starts = list(range(0, len(dates) - ROLLING_WINDOW_DAYS + 1, ROLLING_STEP_DAYS))
    last_start = len(dates) - ROLLING_WINDOW_DAYS
    if starts[-1] != last_start:
        starts.append(last_start)

    windows = []
    for start in starts:
        window_dates = dates[start : start + ROLLING_WINDOW_DAYS]
        if len(window_dates) < MIN_HISTORY_DAYS + 60:
            continue
        result, stats = run_realistic_backtest(pool_rows, price, volume, index_price, window_dates)
        result["start"] = window_dates[0]
        result["end"] = window_dates[-1]
        result["blocked_buys"] = stats.buy_blocked
        result["blocked_sells"] = stats.sell_blocked
        windows.append(result)
    return windows


def audit_label(pool_mode: str, failed: list[str], rolling_results: list[dict]) -> tuple[str, list[str]]:
    issues: list[str] = []
    if pool_mode != "history":
        issues.append("historical stock-pool file is missing; current static pool was used.")
    if failed:
        issues.append("some symbols failed data fetch; universe coverage is incomplete.")
    if not rolling_results:
        issues.append("rolling out-of-sample window check is missing or too short.")
    issues.append("daily close data cannot fully prove intraday fill, suspension, ST, or true limit order availability.")
    issues.append("ST, suspension, delisting, and official daily limit-state files are not supplied.")
    if pool_mode == "history" and not failed and rolling_results:
        return "PROXY_RESEARCH", issues
    return "PROXY_RESEARCH", issues


def format_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def write_report(
    path: Path,
    result: dict,
    label: str,
    warnings: list[str],
    failed: list[str],
    fill_stats: FillStats,
    rolling_results: list[dict],
) -> None:
    lines = [
        "# V15.9 Replicable Backtest Gate Report",
        "",
        f"Period: {START[:4]}-{START[4:6]}-{START[6:8]} to {END[:4]}-{END[4:6]}-{END[6:8]}",
        f"Audit label: {label}",
        "",
        "## Result",
        "",
        "| Strategy | Total return | Annual return | Max drawdown | Sharpe | Calmar | Turnover |",
        "|---|---:|---:|---:|---:|---:|---:|",
        (
            f"| {result['name']} | {format_pct(result['total_return'])} | "
            f"{format_pct(result['annual_return'])} | {format_pct(result['max_drawdown'])} | "
            f"{result['sharpe']:.2f} | {result['calmar']:.2f} | {result['turnover']:.1f} |"
        ),
        "",
        "## Execution Assumptions",
        "",
        f"- Buy cost = commission {COMMISSION:.4%} + slippage {SLIPPAGE:.4%} + impact {IMPACT_BUFFER:.4%}.",
        f"- Sell cost = commission {COMMISSION:.4%} + stamp duty {STAMP_DUTY:.4%} + slippage {SLIPPAGE:.4%} + impact {IMPACT_BUFFER:.4%}.",
        "- Signals are delayed by one trading day before execution.",
        "- Limit-up buys and limit-down sells are blocked by a daily close proxy with board-specific thresholds.",
        "- Defensive minimum weight is enforced at 8% / 5% / 3% according to total-position regime when eligible defensive assets exist.",
        "- Selection requires at least 250 historical trading days.",
        "",
        "## Audit Issues",
        "",
    ]
    if warnings:
        lines.extend(f"- {item}" for item in warnings)
    else:
        lines.append("- No stock-pool audit warning.")
    if failed:
        lines.append(f"- Data fetch failed for: {', '.join(failed)}")
    lines.extend(
        [
            "",
            "## Fill Constraint Summary",
            "",
            "| Block type | Count |",
            "|---|---:|",
            f"| Buy blocked | {fill_stats.buy_blocked} |",
            f"| Sell blocked | {fill_stats.sell_blocked} |",
            f"| Limit proxy blocked | {fill_stats.limit_blocked} |",
            f"| No-data blocked | {fill_stats.no_data_blocked} |",
        ]
    )
    if fill_stats.sample_notes:
        lines.append("")
        lines.append("## Sample Blocked Fills")
        lines.append("")
        lines.extend(f"- {note}" for note in fill_stats.sample_notes)
    if rolling_results:
        lines.extend(
            [
                "",
                "## Rolling Window Check",
                "",
                "| Window | Total return | Annual return | Max drawdown | Sharpe | Calmar | Blocked fills |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for item in rolling_results:
            blocked = item["blocked_buys"] + item["blocked_sells"]
            lines.append(
                f"| {item['start']} to {item['end']} | {format_pct(item['total_return'])} | "
                f"{format_pct(item['annual_return'])} | {format_pct(item['max_drawdown'])} | "
                f"{item['sharpe']:.2f} | {item['calmar']:.2f} | {blocked} |"
            )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            "- This report is not allowed to be quoted as live-repeatable unless the label is FORMAL_REPLICABLE.",
            "- Current implementation remains PROXY_RESEARCH until a true historical pool and fuller trade-state data are supplied.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    root = Path.cwd()
    pool_rows, warnings, pool_mode = read_pool_history(root)
    codes = sorted({row.asset.code for row in pool_rows})
    all_assets = [row.asset for row in pool_rows] + [
        base.Asset(code=code, name=name, theme="index", segment="index", pool_level="index", is_core=True)
        for code, name in base.INDEX_CODES.items()
    ]

    price: dict[str, dict[str, float]] = {}
    volume: dict[str, dict[str, float]] = {}
    failed: list[str] = []
    seen = set()
    for asset in all_assets:
        if asset.code in seen:
            continue
        seen.add(asset.code)
        try:
            bars = base.fetch_bars(asset.code, is_index=asset.theme == "index")
        except Exception:
            failed.append(f"{asset.code}{asset.name}")
            continue
        price[asset.code] = {bar.date: bar.close for bar in bars}
        volume[asset.code] = {bar.date: bar.volume for bar in bars}

    if "000300" not in price or "000852" not in price:
        raise RuntimeError("index data missing; cannot run")

    dates = sorted(set(price["000300"]) & set(price["000852"]))
    active_price_codes = set(price) - set(base.INDEX_CODES)
    missing_pool_codes = sorted(set(codes) - active_price_codes)
    if missing_pool_codes:
        warnings.append(f"missing pool price data: {', '.join(missing_pool_codes[:20])}")

    index_price = {code: price[code] for code in base.INDEX_CODES}
    result, fill_stats = run_realistic_backtest(pool_rows, price, volume, index_price, dates)
    rolling_results = run_rolling_oos(pool_rows, price, volume, index_price, dates)
    label, audit_issues = audit_label(pool_mode, failed, rolling_results)
    warnings.extend(audit_issues)
    out = root / REPORT
    write_report(out, result, label, warnings, failed, fill_stats, rolling_results)
    print(out.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
