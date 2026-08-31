"""Durable P10 entry supervisor.

Consumes persisted/reconciled five-minute bars, rebuilds two-bar trigger state
from Postgres, ranks simultaneous confirmations, and delegates every order
side effect to the execution engine after serialized risk allocation.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import signal
import statistics
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.domain.p10_caps import (
    CompetingCandidate,
    PortfolioState,
    RiskPolicyConfig,
    correlation_cluster_members,
    evaluate_portfolio_caps,
    sort_competing_candidates,
)
from app.domain.p10_sizing import (
    calculate_leg_sizing,
    solve_risk_reduction_exit,
    solve_stop_tightening,
)
from app.domain.p10_geometry import (
    CandleData,
    calculate_chase_ceiling,
    compute_atr14,
    entry_vwap_invalidates_t1_rr,
)
from app.domain.p10_triggers import (
    BALANCED_BREAKOUT_POLICY_V3,
    BREAKOUT_BAR_SIGNAL_POLICY_V2,
    CUMULATIVE_TWO_BAR_POLICY_V1,
    DailySessionBar,
    FiveMinuteBar,
    calculate_bar_relative_volume,
    calculate_relative_volume,
    entry_window_closed,
    evaluate_add_leg_gates,
    evaluate_balanced_breakout_signal,
    evaluate_intraday_trigger,
)
from app.domain.journal_charges import FillLeg, estimate_cnc_charges
from app.services.auth_service import AuthUnavailableError, get_valid_access_token
from app.redis_pool import create_async_redis
from app.redis_pubsub import consume_pubsub
from app.services.bar_aggregator import REDIS_CHANNEL_5M_BARS
from app.services.distributed_lease import (
    acquire_distributed_lease,
    release_distributed_lease,
    renew_distributed_lease,
)
from app.services.execution_engine import (
    BrokerSubmissionUnknownError,
    ChaseCeilingExceededError,
    ExecutionBlockedError,
    PriceAcceptanceLostError,
    _order_tag,
    create_exit_intent,
    create_proposal_entry_intent,
    submit_live_entry_intent,
    submit_live_exit_intent,
)
from app.services.fyers_broker_reads import (
    BrokerPreflightSnapshot,
    FyersBrokerReadClient,
)
from app.services.paper_broker import PaperBrokerError, fetch_preflight as fetch_paper_preflight
from app.services.p9_allocation_gate import emit_p9_gate_event, load_p9_allocation_gate
from app.services.risk_stop_streak import synchronize_stop_streak


logger = logging.getLogger("entry_supervisor")
IST_TZ = ZoneInfo("Asia/Kolkata")
PG_ALLOCATION_LOCK_KEY = 987654321
BROKER_SNAPSHOT_MAX_AGE_SECONDS = 15.0

_LOCK_KEY = "entry_supervisor:singleton"
_STATUS_KEY = "entry_supervisor:status"
_LOCK_TTL_SECONDS = 30
_LOCK_REFRESH_SECONDS = 10

REDIS_STATUS_KEY = _STATUS_KEY
REDIS_LTP_PREFIX = "ltp:"

_shutdown = asyncio.Event()


def _next_nse_session(day: dt.date) -> dt.date:
    holidays = {dt.date.fromisoformat(value) for value in settings.nse_trading_holidays}
    candidate = day + dt.timedelta(days=1)
    while candidate.weekday() >= 5 or candidate in holidays:
        candidate += dt.timedelta(days=1)
    return candidate


def _add_nse_sessions(day: dt.date, count: int) -> dt.date:
    candidate = day
    for _ in range(count):
        candidate = _next_nse_session(candidate)
    return candidate


def _ema21(values: list[Decimal]) -> list[Decimal]:
    if not values:
        return []
    alpha = Decimal("2") / Decimal("22")
    result = [values[0]]
    for value in values[1:]:
        result.append((value * alpha) + (result[-1] * (Decimal("1") - alpha)))
    return result


@dataclass(frozen=True)
class VolumeProfilePoint:
    adv20_robust: int
    expected_cumulative_fraction: Decimal
    expected_bar_fraction: Decimal | None = None

    @property
    def expected_fraction(self) -> Decimal:
        """Compatibility alias for the v1 cumulative-RVOL path."""
        return self.expected_cumulative_fraction


@dataclass(frozen=True)
class ConfirmedLeg:
    leg_id: UUID
    proposal_id: UUID
    symbol: str
    leg_index: int
    risk_allocation_pct: Decimal
    trigger_price: Decimal
    chase_ceiling: Decimal
    initial_stop: Decimal
    scanner_score: Decimal
    confidence: Decimal
    conservative_rr: Decimal
    bar_time: dt.datetime


async def get_active_risk_policy_config(db: AsyncSession) -> RiskPolicyConfig:
    row = (
        await db.execute(
            text(
                """
                SELECT version, name, risk_per_trade_pct, max_total_open_risk_pct,
                       max_single_name_notional_pct, max_sector_notional_pct,
                       max_cluster_notional_pct, correlation_cluster_threshold,
                       correlation_lookback_sessions, daily_loss_limit_pct,
                       max_open_positions, deployable_capital_override
                FROM risk_policies
                WHERE is_active = true
                ORDER BY version DESC
                LIMIT 1
                """
            )
        )
    ).mappings().one_or_none()
    if row is None or row["deployable_capital_override"] is None:
        raise ExecutionBlockedError(
            "Active risk policy or operator deployable-capital baseline is unavailable."
        )
    return RiskPolicyConfig(
        version=int(row["version"]),
        name=row["name"],
        risk_per_trade_pct=Decimal(row["risk_per_trade_pct"]),
        max_total_open_risk_pct=Decimal(row["max_total_open_risk_pct"]),
        max_single_name_notional_pct=Decimal(row["max_single_name_notional_pct"]),
        max_sector_notional_pct=Decimal(row["max_sector_notional_pct"]),
        max_cluster_notional_pct=Decimal(row["max_cluster_notional_pct"]),
        correlation_cluster_threshold=Decimal(row["correlation_cluster_threshold"]),
        correlation_lookback_sessions=int(row["correlation_lookback_sessions"]),
        daily_loss_limit_pct=Decimal(row["daily_loss_limit_pct"]),
        max_open_positions=int(row["max_open_positions"]),
        deployable_capital_override=Decimal(row["deployable_capital_override"]),
    )


async def _load_return_series(
    db: AsyncSession,
    *,
    symbols: list[str],
    lookback: int,
) -> dict[str, list[float]]:
    rows = (
        await db.execute(
            text(
                """
                WITH ranked AS (
                    SELECT i.fyers_symbol AS symbol,
                           c.close_price,
                           row_number() OVER (
                               PARTITION BY i.fyers_symbol ORDER BY c.candle_start DESC
                           ) AS row_number
                    FROM market_candles c
                    JOIN instruments i ON i.id = c.instrument_id
                    WHERE c.timeframe = '1d'
                      AND i.fyers_symbol = ANY(:symbols)
                )
                SELECT symbol, close_price
                FROM ranked
                WHERE row_number <= :limit
                ORDER BY symbol, row_number DESC
                """
            ),
            {"symbols": symbols, "limit": lookback + 1},
        )
    ).mappings().all()
    closes: dict[str, list[Decimal]] = {symbol: [] for symbol in symbols}
    for row in rows:
        closes[row["symbol"]].append(Decimal(row["close_price"]))
    returns: dict[str, list[float]] = {}
    for symbol, values in closes.items():
        if len(values) < lookback + 1:
            raise ExecutionBlockedError(f"Insufficient correlation history for {symbol}.")
        returns[symbol] = [
            float((current / previous) - Decimal("1"))
            for previous, current in zip(values, values[1:])
            if previous > 0
        ]
    return returns


async def load_portfolio_state_under_lock(
    db: AsyncSession,
    *,
    policy: RiskPolicyConfig,
    candidate_symbol: str,
    broker_snapshot: BrokerPreflightSnapshot,
) -> PortfolioState:
    positions = (
        await db.execute(
            text(
                """
                SELECT p.id, i.fyers_symbol AS symbol,
                       COALESCE(i.metadata ->> 'industry', i.metadata ->> 'sector', 'unknown') AS sector,
                       p.state, p.quantity, p.open_quantity, p.average_entry_price,
                       p.current_stop_loss,
                       COALESCE(p.average_entry_price, tp.pivot_price) AS reference_price
                FROM positions p
                JOIN instruments i ON i.id = p.instrument_id
                LEFT JOIN trade_proposals tp ON tp.id = p.proposal_id
                WHERE p.execution_mode = :execution_mode
                  AND p.state IN (
                    'pending_entry', 'open', 'trailing_active', 'exit_pending'
                )
                """
            ),
            {"execution_mode": settings.execution_mode},
        )
    ).mappings().all()

    candidate_sector = (
        await db.execute(
            text(
                """
                SELECT COALESCE(metadata ->> 'industry', metadata ->> 'sector', 'unknown')
                FROM instruments WHERE fyers_symbol = :symbol
                """
            ),
            {"symbol": candidate_symbol},
        )
    ).scalar_one_or_none()
    if candidate_sector is None:
        raise ExecutionBlockedError("Candidate instrument metadata is unavailable.")

    stage_row = (
        await db.execute(
            text("SELECT stage FROM p10_rollout_state WHERE id = true")
        )
    ).mappings().one_or_none()
    current_stage = stage_row["stage"] if stage_row else "shadow"

    base_deployable = min(
        policy.deployable_capital_override or Decimal("0"),
        broker_snapshot.available_funds,
    )
    scale_factor = Decimal("0.25") if current_stage == "reduced_live" else Decimal("1.0")
    deployable = base_deployable * scale_factor
    if deployable <= 0:
        raise ExecutionBlockedError("Broker-available deployable capital is zero.")

    total_open_risk = Decimal("0")
    name_notional = Decimal("0")
    sector_notional = Decimal("0")
    notional_by_symbol: dict[str, Decimal] = {}
    broker_prices: dict[str, Decimal] = {}
    for broker_row in (*broker_snapshot.books.positions, *broker_snapshot.books.holdings):
        symbol = str(broker_row.get("symbol") or "")
        raw_price = (
            broker_row.get("ltp")
            or broker_row.get("marketPrice")
        )
        if symbol and raw_price is not None and Decimal(str(raw_price)) > 0:
            broker_prices[symbol] = Decimal(str(raw_price))
    for position in positions:
        reference = Decimal(position["reference_price"] or 0)
        quantity = max(int(position["open_quantity"]), 0)
        if quantity > 0 and settings.execution_mode == "live":
            if position["symbol"] not in broker_prices:
                raise ExecutionBlockedError(
                    f"Fresh broker mark is unavailable for {position['symbol']}."
                )
            reference = broker_prices[position["symbol"]]
        if position["state"] == "pending_entry":
            quantity = max(quantity, int(position["quantity"]))
        notional = Decimal(quantity) * reference
        notional_by_symbol[position["symbol"]] = (
            notional_by_symbol.get(position["symbol"], Decimal("0")) + notional
        )
        stop = Decimal(position["current_stop_loss"] or 0)
        entry = Decimal(position["average_entry_price"] or reference)
        total_open_risk += Decimal(quantity) * max(Decimal("0"), entry - stop)
        if position["symbol"] == candidate_symbol:
            name_notional += notional
        if position["sector"] == candidate_sector:
            sector_notional += notional

    symbols = sorted(set(notional_by_symbol) | {candidate_symbol})
    if len(symbols) == 1:
        cluster_notional = notional_by_symbol.get(candidate_symbol, Decimal("0"))
    else:
        returns = await _load_return_series(
            db,
            symbols=symbols,
            lookback=policy.correlation_lookback_sessions,
        )
        try:
            cluster = correlation_cluster_members(
                returns,
                candidate_symbol=candidate_symbol,
                threshold=policy.correlation_cluster_threshold,
                lookback_sessions=policy.correlation_lookback_sessions,
            )
        except ValueError as exc:
            raise ExecutionBlockedError(str(exc)) from exc
        cluster_notional = sum(
            (notional_by_symbol.get(symbol, Decimal("0")) for symbol in cluster),
            Decimal("0"),
        )

    today_ist = dt.datetime.now(dt.timezone.utc).astimezone(IST_TZ).date()
    loss_rows = (
        await db.execute(
            text(
                """
                SELECT p.id AS position_id, p.side, p.average_entry_price,
                       f.quantity, f.price
                FROM order_fills f
                JOIN order_intents oi ON oi.id = f.order_intent_id
                JOIN positions p ON p.id = oi.position_id
                WHERE oi.intent_type <> 'entry'
                  AND p.execution_mode = :execution_mode
                  AND (f.filled_at AT TIME ZONE 'Asia/Kolkata')::date = :today
                """
            ),
            {"today": today_ist, "execution_mode": settings.execution_mode},
        )
    ).mappings().all()
    daily_losses = Decimal("0")
    loss_fills: list[FillLeg] = []
    for fill in loss_rows:
        if fill["average_entry_price"] is None:
            continue
        entry = Decimal(fill["average_entry_price"])
        exit_price = Decimal(fill["price"])
        quantity = int(fill["quantity"])
        pnl = (
            (exit_price - entry) * quantity
            if fill["side"] == "long"
            else (entry - exit_price) * quantity
        )
        if pnl < 0:
            daily_losses += abs(pnl)
            loss_fills.append(
                FillLeg(
                    side="sell" if fill["side"] == "long" else "buy",
                    quantity=quantity,
                    price=exit_price,
                )
            )
    if loss_fills:
        daily_losses += estimate_cnc_charges(loss_fills).total
    return PortfolioState(
        deployable_capital=deployable,
        current_open_risk=total_open_risk,
        current_open_positions_count=len(positions),
        daily_realized_losses=daily_losses,
        existing_name_notional=name_notional,
        existing_sector_notional=sector_notional,
        existing_cluster_notional=cluster_notional,
    )


def _bucket_label(bar_time: dt.datetime) -> str:
    aware = bar_time.replace(tzinfo=IST_TZ) if bar_time.tzinfo is None else bar_time.astimezone(IST_TZ)
    return aware.strftime("%H:%M")


async def _load_volume_profile(
    db: AsyncSession,
    *,
    symbol: str,
    bar_time: dt.datetime,
    require_bar_fraction: bool = False,
) -> VolumeProfilePoint:
    row = (
        await db.execute(
            text(
                """
                SELECT vp.adv20_robust, vp.bucket_medians, vp.sessions_used,
                       vp.as_of_date,
                       (
                           SELECT MAX(
                               (fb.bar_time AT TIME ZONE 'Asia/Kolkata')::date
                           )
                           FROM five_minute_bars fb
                           WHERE fb.symbol = :symbol
                             AND fb.reconciliation_status = 'verified'
                             AND (
                                 fb.bar_time AT TIME ZONE 'Asia/Kolkata'
                             )::date < :session_date
                       ) AS latest_verified_session
                FROM volume_profiles vp
                WHERE vp.symbol = :symbol AND vp.as_of_date < :session_date
                ORDER BY vp.as_of_date DESC
                LIMIT 1
                """
            ),
            {
                "symbol": symbol,
                "session_date": bar_time.astimezone(IST_TZ).date()
                if bar_time.tzinfo else bar_time.date(),
            },
        )
    ).mappings().one_or_none()
    if row is None or int(row["sessions_used"]) < 15:
        raise ExecutionBlockedError("At least 15 valid volume-profile sessions are required.")
    latest_verified_session = row.get("latest_verified_session")
    if (
        latest_verified_session is not None
        and row.get("as_of_date") != latest_verified_session
    ):
        raise ExecutionBlockedError(
            "Volume profile is stale relative to the latest verified prior session."
        )
    points = [
        point
        for point in list(row["bucket_medians"] or [])
        if isinstance(point, dict) and point.get("time")
    ]
    label = _bucket_label(bar_time)
    for index, point in enumerate(points):
        if point.get("time") != label:
            continue
        cumulative_fraction = Decimal(
            str(point.get("cumulative_fraction", "0"))
        )
        if cumulative_fraction <= 0:
            break
        expected_bar_fraction: Decimal | None = None
        if require_bar_fraction:
            if index == 0:
                raise ExecutionBlockedError(
                    f"Volume profile has no previous bucket for {label}."
                )
            current_bucket = dt.datetime.strptime(label, "%H:%M")
            expected_previous_label = (
                current_bucket - dt.timedelta(minutes=5)
            ).strftime("%H:%M")
            if points[index - 1].get("time") != expected_previous_label:
                raise ExecutionBlockedError(
                    f"Volume profile has no adjacent previous bucket for {label}."
                )
            previous_fraction = Decimal(
                str(points[index - 1].get("cumulative_fraction", "0"))
            )
            expected_bar_fraction = cumulative_fraction - previous_fraction
            if previous_fraction < 0 or expected_bar_fraction <= 0:
                raise ExecutionBlockedError(
                    f"Volume profile has a non-positive bucket fraction for {label}."
                )
        return VolumeProfilePoint(
            int(row["adv20_robust"]),
            cumulative_fraction,
            expected_bar_fraction,
        )
    raise ExecutionBlockedError(f"Volume profile has no cumulative fraction for {label}.")


async def _recent_base_volume_diagnostic(
    db: AsyncSession,
    *,
    symbol: str,
    bar_time: dt.datetime,
    signal_volume: int,
) -> tuple[Decimal | None, Decimal | None]:
    """Return median preceding-12 same-session volume and signal/base ratio."""
    session_date = (
        bar_time.astimezone(IST_TZ).date()
        if bar_time.tzinfo
        else bar_time.date()
    )
    rows = (
        await db.execute(
            text(
                """
                SELECT volume
                FROM five_minute_bars
                WHERE symbol = :symbol
                  AND reconciliation_status = 'verified'
                  AND (bar_time AT TIME ZONE 'Asia/Kolkata')::date = :session_date
                  AND bar_time < :bar_time
                ORDER BY bar_time DESC
                LIMIT 12
                """
            ),
            {
                "symbol": symbol,
                "session_date": session_date,
                "bar_time": bar_time,
            },
        )
    ).all()
    volumes = [int(row[0]) for row in rows if int(row[0]) >= 0]
    if len(volumes) != 12:
        return None, None
    median_volume = Decimal(str(statistics.median(volumes)))
    if median_volume <= 0:
        return median_volume, None
    ratio = (Decimal(signal_volume) / median_volume).quantize(Decimal("0.0001"))
    return median_volume, ratio


async def _fresh_ltp(redis: aioredis.Redis, symbol: str) -> Decimal:
    raw = await redis.get(f"{REDIS_LTP_PREFIX}{symbol}")
    if raw is None:
        raise ExecutionBlockedError("Fresh LTP is unavailable.")
    payload = json.loads(raw)
    received_at = dt.datetime.fromisoformat(payload["received_at"])
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=dt.timezone.utc)
    if (dt.datetime.now(dt.timezone.utc) - received_at).total_seconds() > 15:
        raise ExecutionBlockedError("Fresh LTP is older than 15 seconds.")
    return Decimal(str(payload["ltp"]))


async def _fetch_broker_preflight(redis: aioredis.Redis) -> BrokerPreflightSnapshot:
    if settings.execution_mode == "paper":
        async with async_session() as db:
            try:
                return await fetch_paper_preflight(db)
            except PaperBrokerError as exc:
                raise ExecutionBlockedError(str(exc)) from exc
    try:
        token = await get_valid_access_token(redis)
    except AuthUnavailableError as exc:
        raise ExecutionBlockedError(str(exc)) from exc
    client = FyersBrokerReadClient(
        app_id=settings.fyers_app_id,
        timeout_seconds=settings.fyers_order_timeout_seconds,
    )
    return await client.fetch_preflight(access_token=token)


async def verify_broker_state_under_lock(
    db: AsyncSession,
    snapshot: BrokerPreflightSnapshot,
) -> None:
    """Fail closed when fresh broker books are ahead of durable local state."""
    local_positions = (
        await db.execute(
            text(
                """
                SELECT i.fyers_symbol AS symbol, SUM(p.open_quantity)::integer AS quantity
                FROM positions p
                JOIN instruments i ON i.id = p.instrument_id
                WHERE p.state IN ('open', 'trailing_active', 'exit_pending')
                  AND p.execution_mode = :execution_mode
                  AND p.open_quantity > 0
                GROUP BY i.fyers_symbol
                """
            ),
            {"execution_mode": settings.execution_mode},
        )
    ).mappings().all()
    local_qty = {row["symbol"]: int(row["quantity"]) for row in local_positions}
    broker_qty: dict[str, int] = {}
    for row in snapshot.books.positions:
        if str(row.get("productType", "")).upper() != "CNC":
            continue
        symbol = str(row.get("symbol") or "")
        quantity = abs(int(row.get("netQty") or 0))
        if symbol and quantity:
            broker_qty[symbol] = max(broker_qty.get(symbol, 0), quantity)
    for row in snapshot.books.holdings:
        symbol = str(row.get("symbol") or "")
        quantity = int(row.get("remainingQuantity") or 0)
        if symbol and quantity > 0:
            broker_qty[symbol] = max(broker_qty.get(symbol, 0), quantity)
    if local_qty != broker_qty:
        raise ExecutionBlockedError(
            "Fresh broker/local position quantities disagree; reconciliation is required."
        )

    intents = (
        await db.execute(
            text(
                """
                SELECT id, fyers_async_id, fyers_order_id, exchange_order_id
                FROM order_intents
                WHERE execution_mode = :execution_mode
                  AND status NOT IN ('filled', 'rejected', 'cancelled')
                """
            ),
            {"execution_mode": settings.execution_mode},
        )
    ).mappings().all()
    known_order_keys: set[str] = set()
    for intent in intents:
        known_order_keys.add(_order_tag(intent["id"]))
        for value in (
            intent["fyers_async_id"], intent["fyers_order_id"], intent["exchange_order_id"]
        ):
            if value:
                known_order_keys.add(str(value))
    terminal_statuses = {"1", "2", "5", "7", "cancelled", "canceled", "filled", "traded", "rejected", "expired"}
    for order in snapshot.books.orders:
        if str(order.get("productType", "")).upper() != "CNC":
            continue
        status = str(order.get("status") or "").strip().lower()
        if status in terminal_statuses:
            continue
        keys = {
            str(value)
            for value in (
                order.get("id_fyers"), order.get("id"), order.get("exchOrdId"),
                order.get("orderTag"),
            )
            if value
        }
        if not (keys & known_order_keys):
            raise ExecutionBlockedError(
                "Fresh broker order book contains an unmatched active CNC order."
            )

    known_trade_ids = {
        str(row[0])
        for row in (
            await db.execute(
                text(
                    """
                    SELECT f.fyers_trade_id
                    FROM order_fills f
                    JOIN order_intents oi ON oi.id = f.order_intent_id
                    WHERE oi.execution_mode = :execution_mode
                    """
                ),
                {"execution_mode": settings.execution_mode},
            )
        ).all()
        if row[0]
    }
    for trade in snapshot.books.trades:
        if str(trade.get("productType", "")).upper() not in {"", "CNC"}:
            continue
        trade_id = trade.get("tradeNumber")
        if trade_id and str(trade_id) not in known_trade_ids:
            raise ExecutionBlockedError(
                "Fresh broker trade book contains an unmatched fill; reconciliation is required."
            )


def _entry_rejection_outcome(exc: BaseException) -> str:
    if isinstance(exc, PriceAcceptanceLostError):
        return "rejected_price_reversal"
    if isinstance(exc, ChaseCeilingExceededError):
        return "rejected_chase"
    message = str(exc).lower()
    if "chase ceiling" in message:
        return "rejected_chase"
    if "not above trigger" in message or "below trigger" in message or "price acceptance" in message:
        return "rejected_price_reversal"
    if any(
        marker in message
        for marker in (
            "capacity",
            "risk cap",
            "not viable",
            "circuit breaker",
            "allocation gate",
            "daily loss",
        )
    ):
        return "rejected_capacity"
    if any(
        marker in message
        for marker in (
            "preflight",
            "broker",
            "reconciliation",
            "fresh price",
            "fresh ltp",
            "snapshot",
        )
    ):
        return "rejected_preflight"
    return "rejected_other"


async def _record_entry_eligibility(
    confirmed: ConfirmedLeg,
    *,
    outcome: str,
    reason: str | None = None,
) -> None:
    """Audit entry eligibility separately from the confirmed trigger."""
    async with async_session() as db:
        policy_version = (
            await db.execute(
                text(
                    """
                    SELECT tp.entry_trigger_policy_version
                    FROM entry_legs el
                    JOIN trade_proposals tp ON tp.id = el.proposal_id
                    WHERE el.id = :leg_id
                    """
                ),
                {"leg_id": confirmed.leg_id},
            )
        ).scalar_one_or_none() or CUMULATIVE_TWO_BAR_POLICY_V1
        requires_reset = policy_version in {
            BREAKOUT_BAR_SIGNAL_POLICY_V2,
            BALANCED_BREAKOUT_POLICY_V3,
        }
        await db.execute(
            text(
                """
                UPDATE trigger_events
                SET entry_eligibility_outcome = :outcome,
                    entry_rejection_reason = :reason
                WHERE leg_id = :leg_id
                  AND bar_timestamp = :bar_time
                  AND is_confirmed = true
                  AND trigger_outcome = 'confirmed'
                """
            ),
            {
                "outcome": outcome,
                "reason": reason,
                "leg_id": confirmed.leg_id,
                "bar_time": confirmed.bar_time,
            },
        )
        if outcome != "eligible":
            await db.execute(
                text(
                    """
                    UPDATE entry_legs
                    SET status = :status, signal_bar_timestamp = NULL
                    WHERE id = :leg_id AND status = 'trigger_observed'
                    """
                ),
                {
                    "status": (
                        "waiting_for_reset"
                        if requires_reset
                        else "armed"
                    ),
                    "leg_id": confirmed.leg_id,
                },
            )
        await db.commit()


async def execute_confirmed_leg_allocation(
    redis: aioredis.Redis,
    confirmed: ConfirmedLeg,
) -> bool:
    broker_snapshot = await _fetch_broker_preflight(redis)

    async def ensure_pre_submit_chase() -> Decimal:
        price = await _fresh_ltp(redis, confirmed.symbol)
        if price <= confirmed.trigger_price:
            raise PriceAcceptanceLostError(
                f"Fresh pre-submission price ({price}) is not above trigger ({confirmed.trigger_price})."
            )
        if price > confirmed.chase_ceiling:
            raise ChaseCeilingExceededError(
                f"Fresh pre-submission price ({price}) exceeds the immutable chase ceiling ({confirmed.chase_ceiling})."
            )
        return price

    current_price = await ensure_pre_submit_chase()

    async with async_session() as db:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": PG_ALLOCATION_LOCK_KEY},
        )
        age = (dt.datetime.now(dt.timezone.utc) - broker_snapshot.fetched_at).total_seconds()
        if age > BROKER_SNAPSHOT_MAX_AGE_SECONDS:
            raise ExecutionBlockedError("Broker preflight snapshot is older than 15 seconds.")
        await verify_broker_state_under_lock(db, broker_snapshot)

        leg = (
            await db.execute(
                text(
                    """
                    SELECT el.id, el.status, el.risk_allocation_pct, el.position_id,
                           tp.approved_risk_budget_amount, tp.status AS proposal_status,
                           tp.instrument_id, tp.entry_trigger_policy_version,
                           i.lot_size,
                           i.metadata ->> 'industry' AS industry
                    FROM entry_legs el
                    JOIN trade_proposals tp ON tp.id = el.proposal_id
                    JOIN instruments i ON i.id = tp.instrument_id
                    WHERE el.id = :leg_id
                    FOR UPDATE OF el, tp
                    """
                ),
                {"leg_id": confirmed.leg_id},
            )
        ).mappings().one_or_none()
        if (
            leg is None
            or leg["status"] != "trigger_observed"
            or leg["proposal_status"] != "approved"
            or leg["approved_risk_budget_amount"] is None
        ):
            return False

        stop_streak = await synchronize_stop_streak(db, settings.execution_mode)
        if stop_streak.tripped:
            generation = int(
                (
                    await db.execute(
                        text("SELECT COALESCE(MAX(generation), 0) + 1 FROM allocation_ledger")
                    )
                ).scalar_one()
            )
            await db.execute(
                text(
                    """
                    UPDATE entry_legs
                    SET status = :status, signal_bar_timestamp = NULL
                    WHERE id = :leg_id AND status = 'trigger_observed'
                    """
                ),
                {
                    "leg_id": confirmed.leg_id,
                    "status": (
                        "waiting_for_reset"
                        if leg["entry_trigger_policy_version"] in (
                            BREAKOUT_BAR_SIGNAL_POLICY_V2,
                            BALANCED_BREAKOUT_POLICY_V3,
                        )
                        else "armed"
                    ),
                },
            )
            await db.execute(
                text(
                    """
                    INSERT INTO allocation_ledger (
                        generation, leg_id, event_type, broker_funds_available,
                        broker_snapshot_at, open_risk_before, open_risk_after,
                        allocated_shares, allocated_risk_amount, allocated_notional,
                        context_adjusted_risk_ceiling, context_gate_reasons, details
                    ) VALUES (
                        :generation, :leg_id, 'allocation_blocked', :funds,
                        :snapshot_at, 0, 0, 0, 0, 0, 0,
                        CAST(:reasons AS jsonb), CAST(:details AS jsonb)
                    )
                    """
                ),
                {
                    "generation": generation,
                    "leg_id": confirmed.leg_id,
                    "funds": broker_snapshot.available_funds,
                    "snapshot_at": broker_snapshot.fetched_at,
                    "reasons": json.dumps(["three_stop_circuit_tripped"]),
                    "details": json.dumps(
                        {
                            "execution_mode": settings.execution_mode,
                            "consecutive_count": stop_streak.consecutive_count,
                            "limit": stop_streak.limit,
                            "fresh_trigger_required": True,
                        }
                    ),
                },
            )
            await db.commit()
            raise ExecutionBlockedError(
                f"Three-stop circuit breaker is tripped for {settings.execution_mode}; "
                "owner reset is required."
            )

        policy = await get_active_risk_policy_config(db)
        state = await load_portfolio_state_under_lock(
            db,
            policy=policy,
            candidate_symbol=confirmed.symbol,
            broker_snapshot=broker_snapshot,
        )
        cap_check = evaluate_portfolio_caps(
            policy,
            state,
            symbol=confirmed.symbol,
            is_new_position=confirmed.leg_index == 1,
        )
        if cap_check.is_blocked:
            raise ExecutionBlockedError(cap_check.blocking_reason or "Risk cap blocked allocation.")

        gate = await load_p9_allocation_gate(
            db,
            industry=leg["industry"],
            session_date=confirmed.bar_time.astimezone(IST_TZ).date(),
        )
        await emit_p9_gate_event(
            db,
            position_id=leg["position_id"],
            instrument_id=leg["instrument_id"],
            gate=gate,
        )
        if gate.is_blocked:
            generation = int(
                (
                    await db.execute(
                        text("SELECT COALESCE(MAX(generation), 0) + 1 FROM allocation_ledger")
                    )
                ).scalar_one()
            )
            await db.execute(
                text(
                    """
                    UPDATE entry_legs
                    SET status = :status, signal_bar_timestamp = NULL
                    WHERE id = :leg_id AND status = 'trigger_observed'
                    """
                ),
                {
                    "leg_id": confirmed.leg_id,
                    "status": (
                        "waiting_for_reset"
                        if leg["entry_trigger_policy_version"] in (
                            BREAKOUT_BAR_SIGNAL_POLICY_V2,
                            BALANCED_BREAKOUT_POLICY_V3,
                        )
                        else "armed"
                    ),
                },
            )
            await db.execute(
                text(
                    """
                    INSERT INTO allocation_ledger (
                        generation, leg_id, event_type, broker_funds_available,
                        broker_snapshot_at, open_risk_before, open_risk_after,
                        allocated_shares, allocated_risk_amount, allocated_notional,
                        market_regime_snapshot_id, sector_strength_result_id,
                        market_context_policy_id, market_context_mode,
                        context_multiplier, context_adjusted_risk_ceiling,
                        context_gate_reasons, details
                    ) VALUES (
                        :generation, :leg_id, 'allocation_blocked', :funds,
                        :snapshot_at, :open_risk, :open_risk, 0, 0, 0,
                        :regime_id, :sector_result_id, :policy_id, :mode,
                        :multiplier, 0, CAST(:reasons AS jsonb), CAST(:details AS jsonb)
                    )
                    """
                ),
                {
                    "generation": generation,
                    "leg_id": confirmed.leg_id,
                    "funds": broker_snapshot.available_funds,
                    "snapshot_at": broker_snapshot.fetched_at,
                    "open_risk": state.current_open_risk,
                    "regime_id": gate.regime_snapshot_id,
                    "sector_result_id": gate.sector_strength_result_id,
                    "policy_id": gate.policy_id,
                    "mode": gate.mode,
                    "multiplier": gate.observed_multiplier,
                    "reasons": json.dumps(gate.reasons),
                    "details": json.dumps(
                        {
                            "policy_version": gate.policy_version,
                            "reference_eod_date": gate.reference_eod_date.isoformat(),
                            "fresh_trigger_required": True,
                        }
                    ),
                },
            )
            await db.commit()
            raise ExecutionBlockedError(
                "P9 allocation gate blocked the confirmed leg: " + ", ".join(gate.reasons)
            )

        approved_leg_budget = (
            Decimal(leg["approved_risk_budget_amount"])
            * Decimal(leg["risk_allocation_pct"])
        )
        active_policy_leg_cap = (
            state.deployable_capital
            * policy.risk_per_trade_pct
            * Decimal(leg["risk_allocation_pct"])
        )
        allowed_risk = min(
            approved_leg_budget,
            active_policy_leg_cap,
            cap_check.allowed_risk_budget,
        ) * gate.effective_multiplier
        allowed_notional = min(
            cap_check.allowed_notional_budget,
            broker_snapshot.available_funds,
        )
        generation = int(
            (
                await db.execute(
                    text("SELECT COALESCE(MAX(generation), 0) + 1 FROM allocation_ledger")
                )
            ).scalar_one()
        )
        # The early check avoids expensive allocation work for an already
        # chased entry. This second read is the authoritative pre-submission
        # eligibility check and also drives final sizing.
        current_price = await ensure_pre_submit_chase()
        sizing = calculate_leg_sizing(
            leg_risk_budget=allowed_risk,
            approved_leg_risk_budget=approved_leg_budget,
            entry_price=current_price,
            stop_price=confirmed.initial_stop,
            max_notional_cap=allowed_notional,
            lot_size=int(leg["lot_size"]),
        )
        if not sizing.is_viable:
            raise ExecutionBlockedError(sizing.rejection_reason or "Leg is not viable.")

        intent, position_id = await create_proposal_entry_intent(
            db,
            proposal_id=confirmed.proposal_id,
            entry_leg_id=confirmed.leg_id,
            quantity=sizing.shares,
            observed_price=current_price,
            trigger_event_timestamp=confirmed.bar_time,
        )
        await db.execute(
            text(
                """
                INSERT INTO allocation_ledger (
                    generation, leg_id, event_type, broker_funds_available,
                    broker_snapshot_at, open_risk_before, open_risk_after,
                    allocated_shares, allocated_risk_amount, allocated_notional,
                    market_regime_snapshot_id, sector_strength_result_id,
                    market_context_policy_id, market_context_mode,
                    context_multiplier, context_adjusted_risk_ceiling,
                    context_gate_reasons, details
                ) VALUES (
                    :generation, :leg_id, 'sizing_allocated', :funds,
                    :snapshot_at, :before, :after, :shares, :risk, :notional,
                    :regime_id, :sector_result_id, :context_policy_id,
                    :context_mode, :context_multiplier, :context_ceiling,
                    CAST(:context_reasons AS jsonb), CAST(:details AS jsonb)
                )
                """
            ),
            {
                "generation": generation,
                "leg_id": confirmed.leg_id,
                "funds": broker_snapshot.available_funds,
                "snapshot_at": broker_snapshot.fetched_at,
                "before": state.current_open_risk,
                "after": state.current_open_risk + sizing.allocated_risk,
                "shares": sizing.shares,
                "risk": sizing.allocated_risk,
                "notional": sizing.allocated_notional,
                "regime_id": gate.regime_snapshot_id,
                "sector_result_id": gate.sector_strength_result_id,
                "context_policy_id": gate.policy_id,
                "context_mode": gate.mode,
                "context_multiplier": gate.observed_multiplier,
                "context_ceiling": allowed_risk,
                "context_reasons": json.dumps(gate.reasons),
                "details": json.dumps(
                    {
                        "fresh_price": str(current_price),
                        "stop": str(confirmed.initial_stop),
                        "policy_version": policy.version,
                        "market_context_policy_version": gate.policy_version,
                        "market_context_reference_eod": gate.reference_eod_date.isoformat(),
                        "market_light": gate.market_light,
                        "sector_tier": gate.sector_tier,
                    }
                ),
            },
        )

        if settings.execution_mode == "paper":
            await db.commit()
            result = await submit_live_entry_intent(
                db,
                redis,
                order_intent_id=intent.id,
                fill_price=current_price,
                pre_submit_check=ensure_pre_submit_chase,
            )
            return result.outcome == "submitted"

        # Reservation + intent become durable before the only broker caller
        # releases the HTTP request. Its internal claim remains replay-safe.
        await db.commit()
        result = await submit_live_entry_intent(
            db,
            redis,
            order_intent_id=intent.id,
            pre_submit_check=ensure_pre_submit_chase,
        )
        leg_status = {
            "submitted": "submitted",
            "submission_unknown": "submission_unknown",
            "rejected": "cancelled",
        }.get(result.outcome)
        if leg_status:
            await db.execute(
                text("UPDATE entry_legs SET status = :status WHERE id = :leg_id"),
                {"status": leg_status, "leg_id": confirmed.leg_id},
            )
            await db.commit()
        return result.outcome == "submitted"


async def _bar_is_verified(db: AsyncSession, symbol: str, bar_time: dt.datetime) -> bool:
    status = (
        await db.execute(
            text(
                """
                SELECT reconciliation_status
                FROM five_minute_bars
                WHERE symbol = :symbol AND bar_time = :bar_time
                """
            ),
            {"symbol": symbol, "bar_time": bar_time},
        )
    ).scalar_one_or_none()
    return status == "verified"


async def _persist_trigger_event(
    db: AsyncSession,
    *,
    leg_id: UUID,
    bar: FiveMinuteBar,
    profile: VolumeProfilePoint,
    bar_type: str,
    bar_rvol: Decimal | None,
    session_rvol: Decimal,
    price_gate_passed: bool,
    volume_gate_passed: bool | None,
    trigger_outcome: str,
    is_confirmed: bool = False,
    chase_valid: bool = True,
    recent_base_median_volume: Decimal | None = None,
    volume_vs_recent_base: Decimal | None = None,
    entry_eligibility_outcome: str | None = None,
) -> None:
    expected_cumulative = int(
        Decimal(profile.adv20_robust) * profile.expected_cumulative_fraction
    )
    expected_bar = (
        int(Decimal(profile.adv20_robust) * profile.expected_bar_fraction)
        if profile.expected_bar_fraction is not None
        else None
    )
    await db.execute(
        text(
            """
            INSERT INTO trigger_events (
                leg_id, bar_timestamp, bar_open, bar_high, bar_low,
                bar_close, bar_volume, cumulative_volume,
                expected_cumulative_volume, relative_volume,
                expected_bar_volume, bar_relative_volume,
                session_cumulative_relative_volume,
                recent_base_median_volume, volume_vs_recent_base,
                price_gate_passed, volume_gate_passed, trigger_outcome,
                entry_eligibility_outcome,
                bar_type, is_confirmed, chase_valid
            ) VALUES (
                :leg_id, :bar_time, :open, :high, :low, :close,
                :volume, :cumulative, :expected_cumulative, :session_rvol,
                :expected_bar, :bar_rvol, :session_rvol,
                :recent_base_median, :base_ratio,
                :price_gate_passed, :volume_gate_passed, :trigger_outcome,
                :entry_eligibility_outcome,
                :bar_type, :is_confirmed, :chase_valid
            )
            ON CONFLICT (leg_id, bar_timestamp, bar_type) DO UPDATE SET
                expected_bar_volume = EXCLUDED.expected_bar_volume,
                bar_relative_volume = EXCLUDED.bar_relative_volume,
                session_cumulative_relative_volume =
                    EXCLUDED.session_cumulative_relative_volume,
                recent_base_median_volume = EXCLUDED.recent_base_median_volume,
                volume_vs_recent_base = EXCLUDED.volume_vs_recent_base,
                price_gate_passed = EXCLUDED.price_gate_passed,
                volume_gate_passed = EXCLUDED.volume_gate_passed,
                trigger_outcome = EXCLUDED.trigger_outcome,
                entry_eligibility_outcome = EXCLUDED.entry_eligibility_outcome,
                is_confirmed = EXCLUDED.is_confirmed,
                chase_valid = EXCLUDED.chase_valid
            """
        ),
        {
            "leg_id": leg_id,
            "bar_time": bar.bar_time,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "cumulative": bar.cumulative_volume,
            "expected_cumulative": expected_cumulative,
            "session_rvol": session_rvol,
            "expected_bar": expected_bar,
            "bar_rvol": bar_rvol,
            "recent_base_median": recent_base_median_volume,
            "base_ratio": volume_vs_recent_base,
            "price_gate_passed": price_gate_passed,
            "volume_gate_passed": volume_gate_passed,
            "trigger_outcome": trigger_outcome,
            "entry_eligibility_outcome": entry_eligibility_outcome,
            "bar_type": bar_type,
            "is_confirmed": is_confirmed,
            "chase_valid": chase_valid,
        },
    )


async def handle_five_minute_bar_event(bar_data: dict[str, Any]) -> list[ConfirmedLeg]:
    symbol = str(bar_data.get("symbol") or "")
    if not symbol or not bar_data.get("bar_time"):
        return []
    bar_time = dt.datetime.fromisoformat(str(bar_data["bar_time"]))
    bar = FiveMinuteBar(
        bar_time=bar_time,
        open=Decimal(str(bar_data["open"])),
        high=Decimal(str(bar_data["high"])),
        low=Decimal(str(bar_data["low"])),
        close=Decimal(str(bar_data["close"])),
        volume=int(bar_data["volume"]),
        cumulative_volume=int(bar_data["cumulative_volume"]),
    )
    session_date = bar_time.astimezone(IST_TZ).date() if bar_time.tzinfo else bar_time.date()
    confirmed: list[ConfirmedLeg] = []

    async with async_session() as db:
        if not await _bar_is_verified(db, symbol, bar_time):
            return []
        legs = (
            await db.execute(
                text(
                    """
                    SELECT el.id AS leg_id, el.proposal_id, el.leg_index,
                           el.risk_allocation_pct, el.status, el.trigger_price,
                           el.chase_ceiling, el.relative_volume_threshold,
                           COALESCE(p.current_stop_loss, tp.initial_stop) AS effective_stop,
                           tp.confidence, tp.t1,
                           tp.entry_trigger_policy_version,
                           latest_te.bar_timestamp AS last_trigger_event_timestamp,
                           latest_te.trigger_outcome AS last_trigger_outcome,
                           latest_te.entry_eligibility_outcome
                               AS last_entry_eligibility_outcome,
                           sr.technical_score
                    FROM entry_legs el
                    JOIN trade_proposals tp ON tp.id = el.proposal_id
                    JOIN screening_results sr ON sr.id = tp.screening_result_id
                    LEFT JOIN positions p ON p.id = el.position_id
                    LEFT JOIN LATERAL (
                        SELECT te.bar_timestamp, te.trigger_outcome,
                               te.entry_eligibility_outcome
                        FROM trigger_events te
                        WHERE te.leg_id = el.id
                        ORDER BY te.bar_timestamp DESC, te.id DESC
                        LIMIT 1
                    ) latest_te ON true
                    WHERE tp.symbol = :symbol
                      AND tp.status = 'approved'
                      AND tp.live_eligible = true
                      AND el.status IN (
                          'armed', 'trigger_observed', 'waiting_for_reset'
                      )
                      AND el.eligible_session_start <= :session_date
                      AND el.eligible_session_end >= :session_date
                    """
                ),
                {"symbol": symbol, "session_date": session_date},
            )
        ).mappings().all()

        for leg in legs:
            trigger = Decimal(leg["trigger_price"])
            chase = Decimal(leg["chase_ceiling"])
            required_rvol = Decimal(leg["relative_volume_threshold"])
            policy_version = (
                leg["entry_trigger_policy_version"]
                or CUMULATIVE_TWO_BAR_POLICY_V1
            )
            uses_bar_rvol = policy_version in {
                BREAKOUT_BAR_SIGNAL_POLICY_V2,
                BALANCED_BREAKOUT_POLICY_V3,
            }
            requires_confirmation_bar = policy_version in {
                CUMULATIVE_TWO_BAR_POLICY_V1,
                BREAKOUT_BAR_SIGNAL_POLICY_V2,
            }
            requires_reset_after_consumed_trigger = policy_version in {
                BREAKOUT_BAR_SIGNAL_POLICY_V2,
                BALANCED_BREAKOUT_POLICY_V3,
            }
            is_v3 = policy_version == BALANCED_BREAKOUT_POLICY_V3
            is_v2 = policy_version == BREAKOUT_BAR_SIGNAL_POLICY_V2
            last_event_at = leg["last_trigger_event_timestamp"]
            if last_event_at is not None and bar_time <= last_event_at:
                # Durable trigger/reset evidence is authoritative during
                # startup replay and duplicate Redis delivery.
                continue
            if (
                leg["status"] == "trigger_observed"
                and leg["last_trigger_outcome"] == "confirmed"
                and leg["last_entry_eligibility_outcome"] == "pending"
            ):
                # Allocation recovery (or an open capacity conflict), not a
                # later market bar, owns this durable confirmed trigger.
                continue
            profile = await _load_volume_profile(
                db,
                symbol=symbol,
                bar_time=bar_time,
                require_bar_fraction=uses_bar_rvol,
            )
            session_rvol = calculate_relative_volume(
                bar.cumulative_volume,
                profile.adv20_robust,
                profile.expected_cumulative_fraction,
            )
            bar_rvol = (
                calculate_bar_relative_volume(
                    bar.volume,
                    profile.adv20_robust,
                    profile.expected_bar_fraction or Decimal("0"),
                )
                if uses_bar_rvol
                else None
            )
            local_time = (
                bar_time.astimezone(IST_TZ).time()
                if bar_time.tzinfo
                else bar_time.time()
            )
            if leg["status"] == "waiting_for_reset":
                if bar.close <= trigger:
                    await _persist_trigger_event(
                        db,
                        leg_id=leg["leg_id"],
                        bar=bar,
                        profile=profile,
                        bar_type="reset_bar",
                        bar_rvol=bar_rvol,
                        session_rvol=session_rvol,
                        price_gate_passed=True,
                        volume_gate_passed=None,
                        trigger_outcome="reset",
                        chase_valid=bar.close <= chase,
                    )
                    await db.execute(
                        text(
                            """
                            UPDATE entry_legs
                            SET status = 'armed', signal_bar_timestamp = NULL
                            WHERE id = :leg_id AND status = 'waiting_for_reset'
                            """
                        ),
                        {"leg_id": leg["leg_id"]},
                    )
                continue

            if leg["status"] == "armed":
                if is_v3:
                    if bar.close > trigger and local_time >= dt.time(9, 30):
                        recent_median, base_ratio = (
                            await _recent_base_volume_diagnostic(
                                db,
                                symbol=symbol,
                                bar_time=bar_time,
                                signal_volume=bar.volume,
                            )
                        )
                        evaluation = evaluate_balanced_breakout_signal(
                            signal_bar=bar,
                            trigger_price=trigger,
                            adv20_robust=profile.adv20_robust,
                            expected_bar_fraction=profile.expected_bar_fraction or Decimal("0"),
                            expected_cumulative_fraction=profile.expected_cumulative_fraction,
                            required_rvol=required_rvol,
                        )
                        if evaluation.is_triggered:
                            await _persist_trigger_event(
                                db,
                                leg_id=leg["leg_id"],
                                bar=bar,
                                profile=profile,
                                bar_type="signal_bar",
                                bar_rvol=evaluation.signal_rvol,
                                session_rvol=evaluation.signal_session_rvol,
                                price_gate_passed=True,
                                volume_gate_passed=True,
                                trigger_outcome="confirmed",
                                is_confirmed=True,
                                chase_valid=bar.close <= chase,
                                recent_base_median_volume=recent_median,
                                volume_vs_recent_base=base_ratio,
                                entry_eligibility_outcome="pending",
                            )
                            await db.execute(
                                text(
                                    """
                                    UPDATE entry_legs
                                    SET status = 'trigger_observed', signal_bar_timestamp = :bar_time
                                    WHERE id = :leg_id AND status = 'armed'
                                    """
                                ),
                                {"leg_id": leg["leg_id"], "bar_time": bar_time},
                            )
                            worst_r = chase - Decimal(leg["effective_stop"])
                            conservative_rr = (
                                (Decimal(leg["t1"]) - chase) / worst_r if worst_r > 0 else Decimal("0")
                            )
                            confirmed.append(
                                ConfirmedLeg(
                                    leg_id=leg["leg_id"],
                                    proposal_id=leg["proposal_id"],
                                    symbol=symbol,
                                    leg_index=int(leg["leg_index"]),
                                    risk_allocation_pct=Decimal(leg["risk_allocation_pct"]),
                                    trigger_price=trigger,
                                    chase_ceiling=chase,
                                    initial_stop=Decimal(leg["effective_stop"]),
                                    scanner_score=Decimal(leg["technical_score"] or 0),
                                    confidence=Decimal(leg["confidence"]),
                                    conservative_rr=conservative_rr,
                                    bar_time=bar_time,
                                )
                            )
                        else:
                            await _persist_trigger_event(
                                db,
                                leg_id=leg["leg_id"],
                                bar=bar,
                                profile=profile,
                                bar_type="signal_bar",
                                bar_rvol=evaluation.signal_rvol,
                                session_rvol=evaluation.signal_session_rvol,
                                price_gate_passed=True,
                                volume_gate_passed=False,
                                trigger_outcome="signal_rejected",
                                is_confirmed=False,
                                chase_valid=bar.close <= chase,
                                recent_base_median_volume=recent_median,
                                volume_vs_recent_base=base_ratio,
                            )
                    continue

                if is_v2:
                    if bar.close > trigger and local_time >= dt.time(9, 30):
                        observed_rvol = bar_rvol
                        volume_passed = bool(observed_rvol >= required_rvol)
                        recent_median, base_ratio = (
                            await _recent_base_volume_diagnostic(
                                db,
                                symbol=symbol,
                                bar_time=bar_time,
                                signal_volume=bar.volume,
                            )
                        )
                        await _persist_trigger_event(
                            db,
                            leg_id=leg["leg_id"],
                            bar=bar,
                            profile=profile,
                            bar_type="signal_bar",
                            bar_rvol=bar_rvol,
                            session_rvol=session_rvol,
                            price_gate_passed=True,
                            volume_gate_passed=volume_passed,
                            trigger_outcome=(
                                "waiting_confirmation"
                                if volume_passed
                                else "signal_rejected"
                            ),
                            chase_valid=bar.close <= chase,
                            recent_base_median_volume=recent_median,
                            volume_vs_recent_base=base_ratio,
                        )
                        if not volume_passed:
                            await db.execute(
                                text(
                                    """
                                    UPDATE entry_legs
                                    SET status = 'waiting_for_reset',
                                        signal_bar_timestamp = NULL
                                    WHERE id = :leg_id AND status = 'armed'
                                    """
                                ),
                                {"leg_id": leg["leg_id"]},
                            )
                            continue
                        await db.execute(
                            text(
                                """
                                UPDATE entry_legs
                                SET status = 'trigger_observed', signal_bar_timestamp = :bar_time
                                WHERE id = :leg_id AND status = 'armed'
                                """
                            ),
                            {"leg_id": leg["leg_id"], "bar_time": bar_time},
                        )
                    continue

                if bar.close > trigger and local_time >= dt.time(9, 30):
                    observed_rvol = session_rvol
                    volume_passed = bool(observed_rvol >= required_rvol)
                    if volume_passed:
                        await _persist_trigger_event(
                            db,
                            leg_id=leg["leg_id"],
                            bar=bar,
                            profile=profile,
                            bar_type="signal_bar",
                            bar_rvol=bar_rvol,
                            session_rvol=session_rvol,
                            price_gate_passed=True,
                            volume_gate_passed=volume_passed,
                            trigger_outcome="waiting_confirmation",
                            chase_valid=bar.close <= chase,
                        )
                        await db.execute(
                            text(
                                """
                                UPDATE entry_legs
                                SET status = 'trigger_observed', signal_bar_timestamp = :bar_time
                                WHERE id = :leg_id AND status = 'armed'
                                """
                            ),
                            {"leg_id": leg["leg_id"], "bar_time": bar_time},
                        )
                continue

            signal_row = (
                await db.execute(
                    text(
                        """
                        SELECT bar_timestamp, bar_open, bar_high, bar_low, bar_close,
                               bar_volume, cumulative_volume
                        FROM trigger_events
                        WHERE leg_id = :leg_id AND bar_type = 'signal_bar'
                        ORDER BY bar_timestamp DESC
                        LIMIT 1
                        """
                    ),
                    {"leg_id": leg["leg_id"]},
                )
            ).mappings().one_or_none()
            if signal_row is None:
                await db.execute(
                    text(
                        """
                        UPDATE entry_legs
                        SET status = :status, signal_bar_timestamp = NULL
                        WHERE id = :leg_id
                        """
                    ),
                    {
                        "leg_id": leg["leg_id"],
                        "status": "waiting_for_reset" if is_v2 else "armed",
                    },
                )
                continue
            if bar_time <= signal_row["bar_timestamp"]:
                continue
            signal_profile = await _load_volume_profile(
                db,
                symbol=symbol,
                bar_time=signal_row["bar_timestamp"],
                require_bar_fraction=is_v2,
            )
            evaluation = evaluate_intraday_trigger(
                signal_bar=FiveMinuteBar(
                    bar_time=signal_row["bar_timestamp"],
                    open=Decimal(signal_row["bar_open"]),
                    high=Decimal(signal_row["bar_high"]),
                    low=Decimal(signal_row["bar_low"]),
                    close=Decimal(signal_row["bar_close"]),
                    volume=int(signal_row["bar_volume"]),
                    cumulative_volume=int(signal_row["cumulative_volume"]),
                ),
                confirmation_bar=bar,
                trigger_price=trigger,
                chase_ceiling=chase,
                adv20_robust=profile.adv20_robust,
                signal_expected_fraction=signal_profile.expected_cumulative_fraction,
                conf_expected_fraction=profile.expected_cumulative_fraction,
                required_rvol=required_rvol,
                current_market_price=bar.close,
                policy_version=policy_version,
                signal_expected_bar_fraction=signal_profile.expected_bar_fraction,
                conf_expected_bar_fraction=profile.expected_bar_fraction,
            )
            if not evaluation.is_triggered:
                if is_v2:
                    await _persist_trigger_event(
                        db,
                        leg_id=leg["leg_id"],
                        bar=bar,
                        profile=profile,
                        bar_type="confirmation_bar",
                        bar_rvol=bar_rvol,
                        session_rvol=session_rvol,
                        price_gate_passed=bar.close > trigger,
                        volume_gate_passed=None,
                        trigger_outcome="confirmation_rejected",
                        chase_valid=bar.close <= chase,
                    )
                await db.execute(
                    text(
                        """
                        UPDATE entry_legs
                        SET status = :status, signal_bar_timestamp = NULL
                        WHERE id = :leg_id
                        """
                    ),
                    {
                        "leg_id": leg["leg_id"],
                        "status": "waiting_for_reset" if is_v2 else "armed",
                    },
                )
                continue
            await _persist_trigger_event(
                db,
                leg_id=leg["leg_id"],
                bar=bar,
                profile=profile,
                bar_type="confirmation_bar",
                bar_rvol=bar_rvol,
                session_rvol=session_rvol,
                price_gate_passed=True,
                volume_gate_passed=(
                    evaluation.confirmation_rvol >= required_rvol
                    if not is_v2
                    else None
                ),
                trigger_outcome="confirmed",
                is_confirmed=True,
                chase_valid=evaluation.chase_valid,
                entry_eligibility_outcome="pending",
            )
            worst_r = chase - Decimal(leg["effective_stop"])
            conservative_rr = (
                (Decimal(leg["t1"]) - chase) / worst_r if worst_r > 0 else Decimal("0")
            )
            confirmed.append(
                ConfirmedLeg(
                    leg_id=leg["leg_id"],
                    proposal_id=leg["proposal_id"],
                    symbol=symbol,
                    leg_index=int(leg["leg_index"]),
                    risk_allocation_pct=Decimal(leg["risk_allocation_pct"]),
                    trigger_price=trigger,
                    chase_ceiling=chase,
                    initial_stop=Decimal(leg["effective_stop"]),
                    scanner_score=Decimal(leg["technical_score"] or 0),
                    confidence=Decimal(leg["confidence"]),
                    conservative_rr=conservative_rr,
                    bar_time=bar_time,
                )
            )
        await db.commit()
    return confirmed


async def _leg_has_persisted_intent(leg_id: UUID) -> bool:
    async with async_session() as db:
        return bool(
            (
                await db.execute(
                    text(
                        """
                        SELECT order_intent_id IS NOT NULL
                        FROM entry_legs
                        WHERE id = :leg_id
                        """
                    ),
                    {"leg_id": leg_id},
                )
            ).scalar_one_or_none()
        )


async def _attempt_confirmed_allocation(
    redis: aioredis.Redis,
    candidate: ConfirmedLeg,
) -> bool:
    try:
        submitted = await execute_confirmed_leg_allocation(redis, candidate)
        if submitted:
            await _record_entry_eligibility(candidate, outcome="eligible")
            return True
        if await _leg_has_persisted_intent(candidate.leg_id):
            # Eligibility passed once the idempotent intent was durably
            # created. Unknown/rejected transport outcomes belong to order
            # lifecycle audit and must not rewrite that fact.
            await _record_entry_eligibility(candidate, outcome="eligible")
            return False
        await _record_entry_eligibility(
            candidate,
            outcome="rejected_other",
            reason="Confirmed trigger did not produce a submitted order.",
        )
    except BrokerSubmissionUnknownError as exc:
        if await _leg_has_persisted_intent(candidate.leg_id):
            await _record_entry_eligibility(candidate, outcome="eligible")
        else:
            await _record_entry_eligibility(
                candidate,
                outcome="rejected_other",
                reason=str(exc),
            )
        logger.exception(
            "Submission outcome is unknown for %s leg %s",
            candidate.symbol,
            candidate.leg_index,
        )
    except Exception as exc:
        await _record_entry_eligibility(
            candidate,
            outcome=_entry_rejection_outcome(exc),
            reason=str(exc),
        )
        logger.exception(
            "Allocation failed closed for %s leg %s",
            candidate.symbol,
            candidate.leg_index,
        )
    return False


async def _process_competing_batch(
    redis: aioredis.Redis,
    candidates: list[ConfirmedLeg],
) -> None:
    if not candidates:
        return
    priority = sort_competing_candidates(
        [
            CompetingCandidate(
                candidate_id=str(candidate.leg_id),
                symbol=candidate.symbol,
                scanner_score=candidate.scanner_score,
                gemini_confidence=candidate.confidence,
                conservative_rr=candidate.conservative_rr,
                trigger_timestamp=candidate.bar_time,
                requested_risk=Decimal("0"),
                requested_notional=Decimal("0"),
            )
            for candidate in candidates
        ]
    )
    by_id = {str(candidate.leg_id): candidate for candidate in candidates}
    if priority.has_capacity_conflict:
        tied = priority.conflict_candidate_ids
        first_tied_index = min(
            index
            for index, ranked in enumerate(priority.ranked_candidates)
            if ranked.candidate_id in tied
        )
        for ranked in priority.ranked_candidates[:first_tied_index]:
            candidate = by_id[ranked.candidate_id]
            await _attempt_confirmed_allocation(redis, candidate)
        async with async_session() as db:
            await db.execute(
                text(
                    """
                    INSERT INTO capacity_conflicts (
                        bar_timestamp, competing_leg_ids, scanner_score
                    ) VALUES (:bar_time, CAST(:leg_ids AS jsonb), :score)
                    """
                ),
                {
                    "bar_time": candidates[0].bar_time,
                    "leg_ids": json.dumps(tied),
                    "score": max(by_id[item].scanner_score for item in tied),
                },
            )
            await db.commit()
        return

    for ranked in priority.ranked_candidates:
        candidate = by_id[ranked.candidate_id]
        await _attempt_confirmed_allocation(redis, candidate)


async def refresh_add_leg_eligibility() -> int:
    """Rebuild Hold/Base/EMA add gates from durable daily candles."""
    armed = 0
    now_ist = dt.datetime.now(dt.timezone.utc).astimezone(IST_TZ)
    async with async_session() as db:
        legs = (
            await db.execute(
                text(
                    """
                    SELECT el.id, el.proposal_id, el.leg_index,
                           el.hold_required, el.base_required,
                           tp.entry_template, tp.instrument_id,
                           p.id AS position_id, p.state AS position_state,
                           p.current_stop_loss,
                           p.t1_filled_shares, i.tick_size,
                           previous.trigger_price AS preceding_trigger,
                           previous.first_filled_at AS preceding_filled_at,
                           first_leg.first_filled_at AS first_filled_at,
                           COALESCE((
                               SELECT te.bar_high
                               FROM trigger_events te
                               WHERE te.leg_id = previous.id
                                 AND te.is_confirmed = true
                                 AND te.trigger_outcome = 'confirmed'
                               ORDER BY te.bar_timestamp DESC LIMIT 1
                           ), previous.trigger_price) AS preceding_high
                    FROM entry_legs el
                    JOIN trade_proposals tp ON tp.id = el.proposal_id
                    JOIN entry_legs previous
                      ON previous.proposal_id = el.proposal_id
                     AND previous.leg_index = el.leg_index - 1
                    JOIN entry_legs first_leg
                      ON first_leg.proposal_id = el.proposal_id
                     AND first_leg.leg_index = 1
                    JOIN positions p ON p.id = previous.position_id
                    JOIN instruments i ON i.id = tp.instrument_id
                    WHERE el.leg_index > 1
                      AND el.status = 'planned'
                      AND previous.status = 'filled'
                      AND tp.status = 'approved'
                      AND p.state IN ('open', 'trailing_active')
                    FOR UPDATE OF el
                    """
                )
            )
        ).mappings().all()
        for leg in legs:
            if int(leg["t1_filled_shares"]) > 0:
                await db.execute(
                    text("UPDATE entry_legs SET status = 'cancelled' WHERE id = :id"),
                    {"id": leg["id"]},
                )
                continue
            if leg["preceding_filled_at"] is None or leg["first_filled_at"] is None:
                continue
            rows = (
                await db.execute(
                    text(
                        """
                        SELECT candle_start, open_price, high_price, low_price,
                               close_price, volume
                        FROM market_candles
                        WHERE instrument_id = :instrument_id
                          AND timeframe = '1d'
                          AND candle_start < :today_ist
                        ORDER BY candle_start ASC
                        """
                    ),
                    {
                        "instrument_id": leg["instrument_id"],
                        "today_ist": dt.datetime.combine(
                            now_ist.date(), dt.time.min, tzinfo=IST_TZ
                        ),
                    },
                )
            ).mappings().all()
            if len(rows) < 26:
                continue
            candle_data = [
                CandleData(
                    open=float(row["open_price"]), high=float(row["high_price"]),
                    low=float(row["low_price"]), close=float(row["close_price"]),
                    volume=int(row["volume"]), date=row["candle_start"].date().isoformat(),
                )
                for row in rows
            ]
            atr14 = compute_atr14(candle_data)
            closes = [Decimal(row["close_price"]) for row in rows]
            ema_values = _ema21(closes)
            preceding_fill_date = leg["preceding_filled_at"].astimezone(IST_TZ).date()
            first_fill_date = leg["first_filled_at"].astimezone(IST_TZ).date()
            completed: list[DailySessionBar] = []
            for index, row in enumerate(rows):
                session_date = row["candle_start"].date()
                if session_date <= preceding_fill_date or index < 20:
                    continue
                avg_volume = sum(int(item["volume"]) for item in rows[index - 19:index + 1]) // 20
                completed.append(
                    DailySessionBar(
                        date=session_date,
                        open=Decimal(row["open_price"]),
                        high=Decimal(row["high_price"]),
                        low=Decimal(row["low_price"]),
                        close=Decimal(row["close_price"]),
                        volume=int(row["volume"]),
                        ema21=ema_values[index],
                        ema21_5d_ago=ema_values[index - 5],
                        sma_volume_20=avg_volume,
                    )
                )
            expiry_date = _add_nse_sessions(first_fill_date, 10)
            if now_ist.date() > expiry_date:
                await db.execute(
                    text("UPDATE entry_legs SET status = 'expired' WHERE id = :id"),
                    {"id": leg["id"]},
                )
                continue
            gate = evaluate_add_leg_gates(
                template=leg["entry_template"],
                leg_index=int(leg["leg_index"]),
                preceding_leg_trigger=Decimal(leg["preceding_trigger"]),
                preceding_leg_high=Decimal(leg["preceding_high"]),
                atr14=atr14,
                completed_sessions_since_fill=completed,
                current_stop=Decimal(leg["current_stop_loss"]),
                tick_size=Decimal(leg["tick_size"]),
            )
            if not gate.is_gate_open or gate.base_high is None or gate.base_low is None:
                await db.execute(
                    text(
                        """
                        UPDATE entry_legs
                        SET hold_count = :hold_count, base_count = :base_count
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": leg["id"],
                        "hold_count": gate.current_hold_count,
                        "base_count": gate.current_base_count,
                    },
                )
                continue
            new_stop = gate.recommended_new_stop or Decimal(leg["current_stop_loss"])
            chase, _ = calculate_chase_ceiling(
                gate.base_high,
                new_stop,
                Decimal(leg["tick_size"]),
            )
            eligible_start = _next_nse_session(rows[-1]["candle_start"].date())
            if eligible_start > expiry_date:
                await db.execute(
                    text("UPDATE entry_legs SET status = 'expired' WHERE id = :id"),
                    {"id": leg["id"]},
                )
                continue
            await db.execute(
                text(
                    """
                    UPDATE positions
                    SET current_stop_loss = GREATEST(current_stop_loss, :new_stop)
                    WHERE id = :position_id
                    """
                ),
                {"position_id": leg["position_id"], "new_stop": new_stop},
            )
            await db.execute(
                text(
                    """
                    UPDATE entry_legs
                    SET status = 'armed', position_id = :position_id,
                        trigger_price = :trigger_price, chase_ceiling = :chase,
                        base_low = :base_low, base_high = :base_high,
                        hold_count = :hold_count, base_count = :base_count,
                        eligible_session_start = :eligible_start,
                        eligible_session_end = :eligible_end
                    WHERE id = :id AND status = 'planned'
                    """
                ),
                {
                    "id": leg["id"], "position_id": leg["position_id"],
                    "trigger_price": gate.base_high, "chase": chase,
                    "base_low": gate.base_low, "base_high": gate.base_high,
                    "hold_count": gate.current_hold_count,
                    "base_count": gate.current_base_count,
                    "eligible_start": eligible_start, "eligible_end": expiry_date,
                },
            )
            await db.execute(
                text(
                    """
                    INSERT INTO position_events (
                        position_id, event_type, from_state, to_state,
                        trigger_source, stop_loss_price, target_price, details
                    ) VALUES (
                        :position_id, 'add_leg_armed', :state, :state,
                        'entry_supervisor', :new_stop, :trigger_price,
                        CAST(:details AS jsonb)
                    )
                    """
                ),
                {
                    "position_id": leg["position_id"],
                    "state": leg["position_state"],
                    "new_stop": new_stop,
                    "trigger_price": gate.base_high,
                    "details": json.dumps(
                        {
                            "leg_id": str(leg["id"]),
                            "leg_index": int(leg["leg_index"]),
                            "eligible_start": eligible_start.isoformat(),
                            "eligible_end": expiry_date.isoformat(),
                        }
                    ),
                },
            )
            armed += 1
        await db.commit()
    return armed


async def refresh_p10_daily_atr() -> int:
    """Refresh the runner's ATR14 from the latest completed daily candle."""
    updated = 0
    today_start = dt.datetime.combine(
        dt.datetime.now(dt.timezone.utc).astimezone(IST_TZ).date(),
        dt.time.min,
        tzinfo=IST_TZ,
    )
    async with async_session() as db:
        positions = (
            await db.execute(
                text(
                    """
                    SELECT id, instrument_id
                    FROM positions
                    WHERE trailing_rule_type = 'p10_staged_atr'
                      AND state IN ('open', 'trailing_active')
                    """
                )
            )
        ).mappings().all()
        for position in positions:
            rows = list(
                reversed(
                    (
                        await db.execute(
                            text(
                                """
                                SELECT open_price, high_price, low_price,
                                       close_price, volume, candle_start
                                FROM market_candles
                                WHERE instrument_id = :instrument_id
                                  AND timeframe = '1d'
                                  AND candle_start < :today_start
                                ORDER BY candle_start DESC LIMIT 100
                                """
                            ),
                            {
                                "instrument_id": position["instrument_id"],
                                "today_start": today_start,
                            },
                        )
                    ).mappings().all()
                )
            )
            if len(rows) < 15:
                continue
            atr14 = compute_atr14(
                [
                    CandleData(
                        open=float(row["open_price"]), high=float(row["high_price"]),
                        low=float(row["low_price"]), close=float(row["close_price"]),
                        volume=int(row["volume"]),
                    )
                    for row in rows
                ]
            )
            await db.execute(
                text(
                    """
                    UPDATE positions
                    SET trailing_rule = jsonb_set(
                        COALESCE(trailing_rule, '{}'::jsonb),
                        '{atr14}', to_jsonb(CAST(:atr14 AS text)), true
                    )
                    WHERE id = :position_id
                    """
                ),
                {"position_id": position["id"], "atr14": str(atr14)},
            )
            updated += 1
        await db.commit()
    return updated


async def recheck_filled_entry_risk(redis: aioredis.Redis) -> int:
    """Apply deterministic chase/R:R, stop-tighten, then trim correction."""
    corrected = 0
    async with async_session() as discovery_db:
        due = (
            await discovery_db.execute(
                text(
                    """
                    SELECT el.id AS leg_id
                    FROM entry_legs el
                    JOIN positions p ON p.id = el.position_id
                    WHERE el.status = 'filled'
                      AND p.state IN ('open', 'trailing_active')
                      AND el.filled_shares > COALESCE((
                          SELECT MAX((al.details ->> 'entry_filled_quantity')::integer)
                          FROM allocation_ledger al
                          WHERE al.leg_id = el.id
                            AND al.details ? 'entry_filled_quantity'
                      ), 0)
                    ORDER BY el.first_filled_at
                    """
                )
            )
        ).scalars().all()
    for leg_id in due:
        snapshot = await _fetch_broker_preflight(redis)
        intent_to_submit: UUID | None = None
        async with async_session() as db:
            await db.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": PG_ALLOCATION_LOCK_KEY},
            )
            if (dt.datetime.now(dt.timezone.utc) - snapshot.fetched_at).total_seconds() > 15:
                raise ExecutionBlockedError("Post-fill broker snapshot is stale.")
            await verify_broker_state_under_lock(db, snapshot)
            row = (
                await db.execute(
                    text(
                        """
                        SELECT el.id AS leg_id, el.filled_shares, el.filled_avg_price,
                               el.leg_index,
                               el.base_low, el.position_id, i.fyers_symbol AS symbol,
                               i.lot_size, i.tick_size, p.open_quantity,
                               p.average_entry_price, p.current_stop_loss,
                               p.state, tp.id AS proposal_id, el.chase_ceiling,
                               tp.t1, tp.t2, tp.t3,
                               tp.approved_risk_budget_amount,
                               (tp.geometry ->> 'final_contraction_low')::numeric AS final_low
                        FROM entry_legs el
                        JOIN trade_proposals tp ON tp.id = el.proposal_id
                        JOIN positions p ON p.id = el.position_id
                        JOIN instruments i ON i.id = tp.instrument_id
                        WHERE el.id = :leg_id
                        FOR UPDATE OF el, p
                        """
                    ),
                    {"leg_id": leg_id},
                )
            ).mappings().one_or_none()
            if (
                row is None
                or row["average_entry_price"] is None
                or int(row["open_quantity"]) <= 0
            ):
                continue
            current_price = await _fresh_ltp(redis, row["symbol"])
            policy = await get_active_risk_policy_config(db)
            state = await load_portfolio_state_under_lock(
                db,
                policy=policy,
                candidate_symbol=row["symbol"],
                broker_snapshot=snapshot,
            )
            quantity = int(row["open_quantity"])
            vwap = Decimal(row["average_entry_price"])
            current_stop = Decimal(row["current_stop_loss"])
            current_risk = Decimal(quantity) * max(Decimal("0"), vwap - current_stop)
            current_notional = Decimal(quantity) * current_price
            approved_risk = min(
                Decimal(row["approved_risk_budget_amount"]),
                state.deployable_capital * policy.risk_per_trade_pct,
                max(
                    Decimal("0"),
                    state.deployable_capital * policy.max_total_open_risk_pct
                    - (state.current_open_risk - current_risk),
                ),
            )
            persisted_context_ceiling = Decimal(
                (
                    await db.execute(
                        text(
                            """
                            SELECT COALESCE(SUM(context_adjusted_risk_ceiling), 0)
                            FROM (
                                SELECT DISTINCT ON (leg_id)
                                       leg_id, context_adjusted_risk_ceiling
                                FROM allocation_ledger
                                WHERE leg_id IN (
                                    SELECT id FROM entry_legs
                                    WHERE position_id = :position_id
                                )
                                  AND event_type = 'sizing_allocated'
                                ORDER BY leg_id, created_at DESC
                            ) context_allocations
                            """
                        ),
                        {"position_id": row["position_id"]},
                    )
                ).scalar_one()
            )
            if persisted_context_ceiling > 0:
                approved_risk = min(approved_risk, persisted_context_ceiling)
            cash_budget = Decimal(
                (
                    await db.execute(
                        text(
                            """
                            SELECT COALESCE(SUM(allocated_notional), 0)
                            FROM (
                                SELECT DISTINCT ON (leg_id) leg_id, allocated_notional
                                FROM allocation_ledger
                                WHERE leg_id IN (
                                    SELECT id FROM entry_legs
                                    WHERE position_id = :position_id
                                ) AND event_type = 'sizing_allocated'
                                ORDER BY leg_id, created_at DESC
                            ) allocated
                            """
                        ),
                        {"position_id": row["position_id"]},
                    )
                ).scalar_one()
            )
            name_limit = (
                state.deployable_capital * policy.max_single_name_notional_pct
                - (state.existing_name_notional - current_notional)
            )
            sector_limit = (
                state.deployable_capital * policy.max_sector_notional_pct
                - (state.existing_sector_notional - current_notional)
            )
            cluster_limit = (
                state.deployable_capital * policy.max_cluster_notional_pct
                - (state.existing_cluster_notional - current_notional)
            )
            cash_shares = int(cash_budget / vwap) if vwap > 0 else 0
            cash_equivalent_at_market = Decimal(cash_shares) * current_price
            allowed_notional = max(
                Decimal("0"),
                min(
                    cash_equivalent_at_market,
                    name_limit,
                    sector_limit,
                    cluster_limit,
                ),
            )
            rr_invalid = entry_vwap_invalidates_t1_rr(
                t1=Decimal(row["t1"]),
                entry_vwap=vwap,
                current_stop=current_stop,
            )
            generation = int(
                (
                    await db.execute(
                        text("SELECT COALESCE(MAX(generation), 0) + 1 FROM allocation_ledger")
                    )
                ).scalar_one()
            )
            event_type = "fill_recalculated"
            details: dict[str, Any] = {
                "entry_filled_quantity": int(row["filled_shares"]),
                "position_open_quantity": quantity,
                "actual_vwap": str(vwap),
                "reference_price": str(current_price),
            }
            leg_vwap = Decimal(row["filled_avg_price"] or vwap)
            if leg_vwap > Decimal(row["chase_ceiling"]) or rr_invalid:
                exit_ref = await create_exit_intent(
                    db,
                    position_id=row["position_id"], intent_type="invalid_fill_exit",
                    side="sell", quantity=quantity, product_type="CNC",
                    observed_price=current_price,
                    reason="P10 actual fill exceeded chase ceiling or invalidated approved R:R.",
                    idempotency_suffix=f"p10:invalid-fill:{row['filled_shares']}",
                    exit_purpose="invalid_fill", is_partial=False,
                )
                intent_to_submit = exit_ref.id if exit_ref else None
                event_type = "full_invalid_exit"
            else:
                structural_low = Decimal(row["base_low"] or row["final_low"])
                tightening = solve_stop_tightening(
                    position_shares=quantity, entry_vwap=vwap,
                    current_stop=current_stop, base_low=structural_low,
                    approved_max_risk=approved_risk,
                    tick_size=Decimal(row["tick_size"]),
                )
                effective_stop = current_stop
                if current_risk > approved_risk and tightening.can_tighten:
                    effective_stop = tightening.new_stop
                    await db.execute(
                        text(
                            """
                            UPDATE positions
                            SET current_stop_loss = GREATEST(current_stop_loss, :stop)
                            WHERE id = :position_id
                            """
                        ),
                        {"position_id": row["position_id"], "stop": effective_stop},
                    )
                    event_type = "tightened_stop"
                    details["tightened_stop"] = str(effective_stop)
                reduction = solve_risk_reduction_exit(
                    position_shares=quantity, entry_vwap=vwap,
                    effective_stop=effective_stop,
                    approved_max_risk=approved_risk,
                    max_notional_cap=allowed_notional,
                    lot_size=int(row["lot_size"]), current_price=current_price,
                )
                if reduction.exit_shares > 0:
                    exit_ref = await create_exit_intent(
                        db,
                        position_id=row["position_id"], intent_type="risk_reduction_exit",
                        side="sell", quantity=reduction.exit_shares,
                        product_type="CNC", observed_price=current_price,
                        reason="P10 post-fill risk/concentration correction.",
                        idempotency_suffix=f"p10:risk-reduction:{row['filled_shares']}",
                        exit_purpose="risk_reduction",
                        is_partial=reduction.exit_shares < quantity,
                    )
                    intent_to_submit = exit_ref.id if exit_ref else None
                    event_type = "risk_reduced_exit"
                    details.update(
                        {
                            "exit_shares": reduction.exit_shares,
                            "rounding_residual": str(reduction.rounding_residual),
                        }
                    )
            await db.execute(
                text(
                    """
                    INSERT INTO allocation_ledger (
                        generation, leg_id, event_type, broker_funds_available,
                        broker_snapshot_at, open_risk_before, open_risk_after,
                        allocated_shares, allocated_risk_amount, allocated_notional,
                        rounding_residual, details
                    ) VALUES (
                        :generation, :leg_id, :event_type, :funds, :snapshot_at,
                        :risk_before, :risk_after, :shares, :risk, :notional,
                        :rounding_residual, CAST(:details AS jsonb)
                    )
                    """
                ),
                {
                    "generation": generation, "leg_id": leg_id,
                    "event_type": event_type, "funds": snapshot.available_funds,
                    "snapshot_at": snapshot.fetched_at, "risk_before": current_risk,
                    "risk_after": min(current_risk, approved_risk), "shares": quantity,
                    "risk": current_risk, "notional": current_notional,
                    "rounding_residual": details.get("rounding_residual", "0"),
                    "details": json.dumps(details),
                },
            )
            await db.commit()
        if intent_to_submit is not None:
            async with async_session() as submit_db:
                await submit_live_exit_intent(
                    submit_db,
                    redis,
                    order_intent_id=intent_to_submit,
                    fill_price=current_price,
                )
        corrected += 1
    return corrected


async def expire_stale_entry_legs() -> int:
    """Expire entry windows whose final eligible session has closed.

    AGENTS.md §5.4: an approved initial leg is armed for D1 only. The window is
    considered closed at 16:00 IST on `eligible_session_end` (after the final
    15:45 bar-reconciliation tick), so expiry no longer waits for the next
    calendar midnight and no longer depends on the supervisor being alive at
    midnight — the sweep catches up on the next maintenance tick whenever the
    process next runs. Expiring a leg also cancels its higher-index `planned`
    siblings: an add leg can never arm once its preceding leg has expired.
    """
    now_ist = dt.datetime.now(dt.timezone.utc).astimezone(IST_TZ)
    expired_leg_ids: list[UUID] = []
    async with async_session() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT id, proposal_id, leg_index, eligible_session_end
                    FROM entry_legs
                    WHERE status IN (
                        'armed', 'trigger_observed', 'waiting_for_reset'
                    )
                      AND eligible_session_end IS NOT NULL
                    """
                )
            )
        ).mappings().all()
        stale_rows = [
            row for row in rows
            if entry_window_closed(row["eligible_session_end"], now_ist)
        ]
        if not stale_rows:
            return 0
        expired_leg_ids = [row["id"] for row in stale_rows]
        await db.execute(
            text(
                """
                UPDATE entry_legs
                SET status = 'expired', signal_bar_timestamp = NULL
                WHERE id = ANY(:leg_ids)
                  AND status IN (
                      'armed', 'trigger_observed', 'waiting_for_reset'
                  )
                """
            ),
            {"leg_ids": expired_leg_ids},
        )
        for row in stale_rows:
            await db.execute(
                text(
                    """
                    UPDATE entry_legs
                    SET status = 'cancelled'
                    WHERE proposal_id = :proposal_id
                      AND leg_index > :leg_index
                      AND status = 'planned'
                    """
                ),
                {
                    "proposal_id": row["proposal_id"],
                    "leg_index": int(row["leg_index"]),
                },
            )
        await db.commit()
    return len(expired_leg_ids)


async def _confirmed_leg_from_id(db: AsyncSession, leg_id: UUID) -> ConfirmedLeg | None:
    row = (
        await db.execute(
            text(
                """
                SELECT el.id AS leg_id, el.proposal_id, el.leg_index,
                       el.risk_allocation_pct, el.trigger_price, el.chase_ceiling,
                       tp.symbol,
                       COALESCE(p.current_stop_loss, tp.initial_stop) AS effective_stop,
                       tp.confidence, tp.t1,
                       sr.technical_score, te.bar_timestamp
                FROM entry_legs el
                JOIN trade_proposals tp ON tp.id = el.proposal_id
                JOIN screening_results sr ON sr.id = tp.screening_result_id
                LEFT JOIN positions p ON p.id = el.position_id
                JOIN LATERAL (
                    SELECT bar_timestamp FROM trigger_events
                    WHERE leg_id = el.id AND is_confirmed = true
                      AND trigger_outcome = 'confirmed'
                    ORDER BY bar_timestamp DESC LIMIT 1
                ) te ON true
                WHERE el.id = :leg_id
                  AND el.status = 'trigger_observed'
                  AND tp.status = 'approved'
                """
            ),
            {"leg_id": leg_id},
        )
    ).mappings().one_or_none()
    if row is None:
        return None
    chase = Decimal(row["chase_ceiling"])
    stop = Decimal(row["effective_stop"])
    worst_r = chase - stop
    return ConfirmedLeg(
        leg_id=row["leg_id"], proposal_id=row["proposal_id"], symbol=row["symbol"],
        leg_index=int(row["leg_index"]),
        risk_allocation_pct=Decimal(row["risk_allocation_pct"]),
        trigger_price=Decimal(row["trigger_price"]), chase_ceiling=chase,
        initial_stop=stop, scanner_score=Decimal(row["technical_score"] or 0),
        confidence=Decimal(row["confidence"]),
        conservative_rr=((Decimal(row["t1"]) - chase) / worst_r if worst_r > 0 else Decimal("0")),
        bar_time=row["bar_timestamp"],
    )


async def process_resolved_capacity_conflicts(redis: aioredis.Redis) -> int:
    """Consume operator tie decisions exactly once while the signal is fresh."""
    processed = 0
    async with async_session() as db:
        conflicts = (
            await db.execute(
                text(
                    """
                    SELECT id, bar_timestamp, competing_leg_ids, chosen_leg_id,
                           resolution_type
                    FROM capacity_conflicts
                    WHERE status = 'resolved' AND executed_at IS NULL
                    ORDER BY decided_at
                    FOR UPDATE SKIP LOCKED
                    """
                )
            )
        ).mappings().all()
        for conflict in conflicts:
            leg_ids = [UUID(value) for value in conflict["competing_leg_ids"]]
            now = dt.datetime.now(dt.timezone.utc)
            expires_at = conflict["bar_timestamp"] + dt.timedelta(minutes=10)
            if (
                conflict["resolution_type"] != "operator_selected"
                or conflict["chosen_leg_id"] is None
                or now > expires_at
            ):
                await db.execute(
                    text(
                        """
                        UPDATE entry_legs el
                        SET status = CASE
                                WHEN tp.entry_trigger_policy_version IN (
                                    'breakout_bar_signal_v2',
                                    'balanced_breakout_v3'
                                )
                                THEN 'waiting_for_reset'
                                ELSE 'armed'
                            END,
                            signal_bar_timestamp = NULL
                        FROM trade_proposals tp
                        WHERE el.proposal_id = tp.id
                          AND el.id = ANY(:leg_ids)
                          AND el.status = 'trigger_observed'
                        """
                    ),
                    {"leg_ids": leg_ids},
                )
                await db.execute(
                    text(
                        """
                        UPDATE trigger_events
                        SET entry_eligibility_outcome = 'rejected_capacity',
                            entry_rejection_reason = :reason
                        WHERE leg_id = ANY(:leg_ids)
                          AND is_confirmed = true
                          AND trigger_outcome = 'confirmed'
                          AND entry_eligibility_outcome = 'pending'
                        """
                    ),
                    {
                        "leg_ids": leg_ids,
                        "reason": (
                            "Capacity conflict expired before selection."
                            if now > expires_at
                            else "Capacity conflict skipped by the operator."
                        ),
                    },
                )
                await db.execute(
                    text(
                        """
                        UPDATE capacity_conflicts
                        SET status = CASE WHEN :expired THEN 'expired_skipped' ELSE status END,
                            executed_at = now()
                        WHERE id = :id
                        """
                    ),
                    {"id": conflict["id"], "expired": now > expires_at},
                )
                processed += 1
                continue
            candidate = await _confirmed_leg_from_id(db, conflict["chosen_leg_id"])
            await db.execute(
                text(
                    """
                    UPDATE entry_legs el
                    SET status = CASE
                            WHEN tp.entry_trigger_policy_version IN (
                                'breakout_bar_signal_v2',
                                'balanced_breakout_v3'
                            )
                            THEN 'waiting_for_reset'
                            ELSE 'armed'
                        END,
                        signal_bar_timestamp = NULL
                    FROM trade_proposals tp
                    WHERE el.proposal_id = tp.id
                      AND el.id = ANY(:leg_ids)
                      AND el.id <> :chosen
                      AND el.status = 'trigger_observed'
                    """
                ),
                {"leg_ids": leg_ids, "chosen": conflict["chosen_leg_id"]},
            )
            await db.execute(
                text(
                    """
                    UPDATE trigger_events
                    SET entry_eligibility_outcome = 'rejected_capacity',
                        entry_rejection_reason =
                            'Another exact-tie candidate was selected.'
                    WHERE leg_id = ANY(:leg_ids)
                      AND leg_id <> :chosen
                      AND is_confirmed = true
                      AND trigger_outcome = 'confirmed'
                      AND entry_eligibility_outcome = 'pending'
                    """
                ),
                {"leg_ids": leg_ids, "chosen": conflict["chosen_leg_id"]},
            )
            await db.commit()
            if candidate is not None:
                await _attempt_confirmed_allocation(redis, candidate)
            async with async_session() as finish_db:
                await finish_db.execute(
                    text("UPDATE capacity_conflicts SET executed_at = now() WHERE id = :id"),
                    {"id": conflict["id"]},
                )
                await finish_db.commit()
            processed += 1
        await db.commit()
    return processed


async def replay_verified_current_session(redis: aioredis.Redis) -> None:
    """Replay durable verified bars after a crash or Redis message loss."""
    today = dt.datetime.now(dt.timezone.utc).astimezone(IST_TZ).date()
    async with async_session() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT symbol, bar_time, open, high, low, close, volume,
                           cumulative_volume
                    FROM five_minute_bars
                    WHERE reconciliation_status = 'verified'
                      AND (bar_time AT TIME ZONE 'Asia/Kolkata')::date = :today
                    ORDER BY bar_time, symbol
                    """
                ),
                {"today": today},
            )
        ).mappings().all()

        # A crash can occur after durable confirmation but before allocation
        # dispatch. Resume those facts before replaying any later bars.
        pending_leg_ids = (
            await db.execute(
                text(
                    """
                    SELECT DISTINCT el.id
                    FROM entry_legs el
                    JOIN trigger_events te ON te.leg_id = el.id
                    WHERE el.status = 'trigger_observed'
                      AND te.is_confirmed = true
                      AND te.trigger_outcome = 'confirmed'
                      AND te.entry_eligibility_outcome = 'pending'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM capacity_conflicts cc
                          WHERE cc.status = 'open'
                            AND jsonb_exists(
                                cc.competing_leg_ids, el.id::text
                            )
                      )
                    """
                )
            )
        ).scalars().all()
        recovered = [
            candidate
            for leg_id in pending_leg_ids
            if (candidate := await _confirmed_leg_from_id(db, leg_id))
            is not None
        ]
    recovery_batches: dict[dt.datetime, list[ConfirmedLeg]] = {}
    for candidate in recovered:
        recovery_batches.setdefault(candidate.bar_time, []).append(candidate)
    for candidates in recovery_batches.values():
        await _process_competing_batch(redis, candidates)

    batches: dict[dt.datetime, list[ConfirmedLeg]] = {}
    for row in rows:
        confirmed = await handle_five_minute_bar_event(
            {
                "symbol": row["symbol"], "bar_time": row["bar_time"].isoformat(),
                "open": str(row["open"]), "high": str(row["high"]),
                "low": str(row["low"]), "close": str(row["close"]),
                "volume": int(row["volume"]),
                "cumulative_volume": int(row["cumulative_volume"]),
            }
        )
        for candidate in confirmed:
            bucket = batches.setdefault(candidate.bar_time, [])
            if all(item.leg_id != candidate.leg_id for item in bucket):
                bucket.append(candidate)
    for candidates in batches.values():
        await _process_competing_batch(redis, candidates)


async def _maintenance_loop(redis: aioredis.Redis) -> None:
    while not _shutdown.is_set():
        try:
            await refresh_add_leg_eligibility()
            await refresh_p10_daily_atr()
            await recheck_filled_entry_risk(redis)
            await expire_stale_entry_legs()
            await process_resolved_capacity_conflicts(redis)
        except Exception:
            logger.exception("Entry-supervisor durable maintenance failed")
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass


async def _set_status(
    redis: aioredis.Redis,
    status: str,
    worker_id: str | None = None,
) -> None:
    payload = {
        "status": status,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    if worker_id is not None:
        payload["worker_id"] = worker_id
    await redis.set(_STATUS_KEY, json.dumps(payload), ex=_LOCK_TTL_SECONDS)


async def _heartbeat(redis: aioredis.Redis, worker_id: str) -> None:
    while not _shutdown.is_set():
        refreshed = await renew_distributed_lease(
            redis,
            _LOCK_KEY,
            worker_id,
            _LOCK_TTL_SECONDS,
        )
        if not refreshed:
            logger.critical("Entry supervisor lost its singleton lease; stopping.")
            _shutdown.set()
            return
        await _set_status(redis, "running", worker_id=worker_id)
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=_LOCK_REFRESH_SECONDS)
        except asyncio.TimeoutError:
            pass


async def run_entry_supervisor() -> None:
    redis = await create_async_redis()
    worker_id = str(uuid4())

    if not await acquire_distributed_lease(
        redis,
        _LOCK_KEY,
        worker_id,
        _LOCK_TTL_SECONDS,
    ):
        logger.error("Another entry supervisor owns the singleton lease.")
        await redis.aclose()
        return

    await _set_status(redis, "running", worker_id=worker_id)
    heartbeat = asyncio.create_task(_heartbeat(redis, worker_id))
    maintenance = asyncio.create_task(_maintenance_loop(redis))
    pending: dict[dt.datetime, list[ConfirmedLeg]] = {}
    dispatch_tasks: set[asyncio.Task[None]] = set()

    try:
        await replay_verified_current_session(redis)
    except Exception:
        logger.exception("Failed to replay durable verified bars at startup")

    async def delayed_dispatch(bar_time: dt.datetime) -> None:
        await asyncio.sleep(1)
        batch = pending.pop(bar_time, [])
        await _process_competing_batch(redis, batch)

    async def handle_bar_message(message: dict[str, Any]) -> None:
        try:
            confirmed = await handle_five_minute_bar_event(json.loads(message["data"]))
            for item in confirmed:
                new_bucket = item.bar_time not in pending
                bucket = pending.setdefault(item.bar_time, [])
                if any(existing.leg_id == item.leg_id for existing in bucket):
                    continue
                bucket.append(item)
                if new_bucket:
                    task = asyncio.create_task(delayed_dispatch(item.bar_time))
                    dispatch_tasks.add(task)
                    task.add_done_callback(dispatch_tasks.discard)
        except Exception:
            logger.exception("Failed to process completed 5-minute bar")

    try:
        await consume_pubsub(
            redis,
            [REDIS_CHANNEL_5M_BARS],
            component="entry_supervisor",
            handler=handle_bar_message,
            should_stop=_shutdown.is_set,
        )
    finally:
        heartbeat.cancel()
        maintenance.cancel()
        for task in dispatch_tasks:
            task.cancel()
        for task in (heartbeat, maintenance):
            try:
                await task
            except asyncio.CancelledError:
                pass
        await _set_status(redis, "stopped", worker_id=worker_id)
        await release_distributed_lease(redis, _LOCK_KEY, worker_id)
        await redis.aclose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    def stop(*_: Any) -> None:
        _shutdown.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    asyncio.run(run_entry_supervisor())


if __name__ == "__main__":
    main()
