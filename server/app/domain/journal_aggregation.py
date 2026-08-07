"""Pure journal aggregation: fills, P&L, R-multiples, period buckets."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from app.domain.trading import realized_pnl_on_exit

INDIA_TZ = ZoneInfo("Asia/Kolkata")

ExitReason = Literal["stop_loss", "target", "trailing", "manual"]
ExitOutcome = Literal["stop_loss", "target", "trailing", "manual", "mixed"]
PeriodBucket = Literal["day", "week", "month", "year"]

INTENT_TO_EXIT_REASON: dict[str, ExitReason] = {
    "stop_loss_exit": "stop_loss",
    "target_exit": "target",
    "trailing_exit": "trailing",
    "manual_exit": "manual",
}


@dataclass(frozen=True)
class ExitFillRecord:
    order_fill_id: str
    order_intent_id: str
    intent_type: str
    quantity: int
    price: Decimal
    filled_at: datetime.datetime
    exit_reason: ExitReason


def weighted_average_price(fills: list[tuple[int, Decimal]]) -> Decimal | None:
    total_qty = sum(qty for qty, _ in fills)
    if total_qty <= 0:
        return None
    total_value = sum(Decimal(qty) * price for qty, price in fills)
    return total_value / Decimal(total_qty)


def aggregate_exit_outcome(reasons: list[ExitReason]) -> ExitOutcome | None:
    if not reasons:
        return None
    unique = set(reasons)
    if len(unique) == 1:
        return next(iter(unique))
    return "mixed"


def compute_gross_pnl(
    *,
    side: Literal["long", "short"],
    entry_fills: list[tuple[int, Decimal]],
    exit_fills: list[tuple[int, Decimal]],
) -> Decimal | None:
    entry_avg = weighted_average_price(entry_fills)
    exit_avg = weighted_average_price(exit_fills)
    if entry_avg is None or exit_avg is None:
        return None
    total_qty = min(
        sum(qty for qty, _ in entry_fills),
        sum(qty for qty, _ in exit_fills),
    )
    if total_qty <= 0:
        return Decimal("0")
    return realized_pnl_on_exit(
        side=side,
        average_entry_price=entry_avg,
        quantity=total_qty,
        exit_price=exit_avg,
    )


def compute_r_multiple(*, gross_or_net_pnl: Decimal, risk_amount: Decimal | None) -> Decimal | None:
    if risk_amount is None or risk_amount <= 0:
        return None
    return gross_or_net_pnl / risk_amount


def hold_duration_hours(
    opened_at: datetime.datetime | None,
    closed_at: datetime.datetime | None,
) -> Decimal | None:
    if opened_at is None or closed_at is None:
        return None
    delta = closed_at - opened_at
    return Decimal(str(delta.total_seconds())) / Decimal("3600")


def closure_date_ist(closed_at: datetime.datetime) -> datetime.date:
    if closed_at.tzinfo is None:
        closed_at = closed_at.replace(tzinfo=datetime.timezone.utc)
    return closed_at.astimezone(INDIA_TZ).date()


def period_key(closed_at: datetime.datetime, bucket: PeriodBucket) -> str:
    local_date = closure_date_ist(closed_at)
    if bucket == "day":
        return local_date.isoformat()
    if bucket == "week":
        iso = local_date.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if bucket == "month":
        return f"{local_date.year}-{local_date.month:02d}"
    return str(local_date.year)


@dataclass(frozen=True)
class PeriodStats:
    period_key: str
    trade_count: int
    wins: int
    losses: int
    gross_pnl: Decimal
    net_pnl: Decimal
    total_charges: Decimal

    @property
    def win_rate(self) -> Decimal | None:
        if self.trade_count == 0:
            return None
        return Decimal(self.wins * 100) / Decimal(self.trade_count)

    @property
    def profit_factor(self) -> Decimal | None:
        gross_wins = Decimal("0")
        gross_losses = Decimal("0")
        return None


def summarize_periods(
    trades: list[dict],
    bucket: PeriodBucket,
) -> list[PeriodStats]:
    buckets: dict[str, PeriodStats] = {}
    for trade in trades:
        closed_at = trade["closed_at"]
        if closed_at is None:
            continue
        key = period_key(closed_at, bucket)
        gross = Decimal(str(trade.get("gross_pnl") or 0))
        net = Decimal(str(trade.get("net_pnl") or 0))
        charges = Decimal(str(trade.get("total_charges") or 0))
        existing = buckets.get(key)
        if existing is None:
            buckets[key] = PeriodStats(
                period_key=key,
                trade_count=1,
                wins=1 if net > 0 else 0,
                losses=1 if net < 0 else 0,
                gross_pnl=gross,
                net_pnl=net,
                total_charges=charges,
            )
        else:
            buckets[key] = PeriodStats(
                period_key=key,
                trade_count=existing.trade_count + 1,
                wins=existing.wins + (1 if net > 0 else 0),
                losses=existing.losses + (1 if net < 0 else 0),
                gross_pnl=existing.gross_pnl + gross,
                net_pnl=existing.net_pnl + net,
                total_charges=existing.total_charges + charges,
            )
    return sorted(buckets.values(), key=lambda item: item.period_key, reverse=True)


def compute_summary_metrics(trades: list[dict]) -> dict:
    if not trades:
        return {
            "trade_count": 0,
            "win_rate": None,
            "profit_factor": None,
            "expectancy": None,
            "avg_win": None,
            "avg_loss": None,
            "avg_r": None,
            "max_drawdown": None,
            "gross_pnl": Decimal("0"),
            "net_pnl": Decimal("0"),
            "total_charges": Decimal("0"),
            "avg_hold_hours": None,
            "best_trade_id": None,
            "worst_trade_id": None,
        }

    nets = [Decimal(str(t.get("net_pnl") or 0)) for t in trades]
    gross_total = sum(Decimal(str(t.get("gross_pnl") or 0)) for t in trades)
    net_total = sum(nets)
    charges_total = sum(Decimal(str(t.get("total_charges") or 0)) for t in trades)
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    r_values = [
        Decimal(str(t["net_r_multiple"]))
        for t in trades
        if t.get("net_r_multiple") is not None
    ]
    hold_hours = [
        Decimal(str(t["hold_duration_hours"]))
        for t in trades
        if t.get("hold_duration_hours") is not None
    ]

    gross_wins = sum(wins, Decimal("0"))
    gross_losses = abs(sum(losses, Decimal("0")))
    profit_factor = (
        gross_wins / gross_losses if gross_losses > 0 else None
    )
    expectancy = net_total / Decimal(len(trades))

    equity = Decimal("0")
    peak = Decimal("0")
    max_dd = Decimal("0")
    sorted_trades = sorted(
        trades,
        key=lambda t: t["closed_at"] or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
    )
    for trade in sorted_trades:
        net = Decimal(str(trade.get("net_pnl") or 0))
        equity += net
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    best = max(trades, key=lambda t: Decimal(str(t.get("net_pnl") or 0)))
    worst = min(trades, key=lambda t: Decimal(str(t.get("net_pnl") or 0)))

    return {
        "trade_count": len(trades),
        "win_rate": Decimal(len(wins) * 100) / Decimal(len(trades)),
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "avg_win": (gross_wins / Decimal(len(wins))) if wins else None,
        "avg_loss": (-sum(losses, Decimal("0")) / Decimal(len(losses))) if losses else None,
        "avg_r": (sum(r_values, Decimal("0")) / Decimal(len(r_values))) if r_values else None,
        "max_drawdown": max_dd if nets else None,
        "gross_pnl": gross_total,
        "net_pnl": net_total,
        "total_charges": charges_total,
        "avg_hold_hours": (
            sum(hold_hours, Decimal("0")) / Decimal(len(hold_hours))
            if hold_hours
            else None
        ),
        "best_trade_id": best["id"],
        "worst_trade_id": worst["id"],
    }
