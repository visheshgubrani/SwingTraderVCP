"""Idempotent paper/live order intent creation and Fyers submission."""

import asyncio
import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.trading import ExitIntentType, realized_pnl_on_exit
from app.domain.p10_sizing import apportion_staged_exits
from app.domain.p10_geometry import floor_to_tick
from app.services.auth_service import AuthUnavailableError, get_valid_access_token
from app.services.journal_outbox import enqueue_journal_fill_event
from app.services.risk_stop_streak import record_closed_position, synchronize_stop_streak
from app.services.staged_exit_manager import (
    StagedPositionState,
    allocate_cumulative_target_fill,
)

REDIS_TICK_SUBS_CHANNEL = "tick_subs"


class ExecutionBlockedError(RuntimeError):
    """Raised when an operational control blocks a new order intent."""


class ExecutionSafetyError(RuntimeError):
    """Raised when a money-path invariant is not satisfied."""


class BrokerOrderRejectedError(RuntimeError):
    """The broker returned a definite rejection without accepting the order."""

    def __init__(self, message: str, *, payload: dict[str, Any]):
        self.payload = payload
        super().__init__(message)


class BrokerSubmissionUnknownError(RuntimeError):
    """The request may have reached the broker; automatic retry is unsafe."""


@dataclass(frozen=True)
class AsyncOrderAcceptance:
    fyers_async_id: str
    payload: dict[str, Any]


class FyersAsyncOrderClient:
    """Execution-engine adapter for one `/orders/async` call without retries."""

    def __init__(
        self,
        *,
        app_id: str,
        endpoint: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._app_id = app_id
        self._endpoint = endpoint or settings.fyers_async_orders_url
        self._timeout = timeout_seconds or settings.fyers_order_timeout_seconds
        self._transport = transport

    async def place_order(
        self,
        *,
        access_token: str,
        payload: dict[str, Any],
    ) -> AsyncOrderAcceptance:
        headers = {
            "Authorization": f"{self._app_id}:{access_token}",
            "Content-Type": "application/json",
            "version": "3",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    self._endpoint,
                    headers=headers,
                    json=payload,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise BrokerSubmissionUnknownError(
                "Fyers async-order transport outcome is unknown; the order was not "
                "retried."
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise BrokerSubmissionUnknownError(
                f"Fyers returned non-JSON data (HTTP {response.status_code}); "
                "submission outcome is unknown."
            ) from exc

        if not isinstance(data, dict):
            raise BrokerSubmissionUnknownError(
                "Fyers returned an unexpected async-order response; submission "
                "outcome is unknown."
            )

        if response.status_code < 500 and response.status_code not in {408, 425}:
            if data.get("s") == "error" or response.is_error:
                message = str(data.get("message") or "Fyers rejected the order.")
                raise BrokerOrderRejectedError(message, payload=data)

        if response.is_error:
            raise BrokerSubmissionUnknownError(
                f"Fyers returned HTTP {response.status_code}; submission outcome "
                "is unknown."
            )

        async_id = _extract_async_id(data)
        if data.get("s") != "ok" or async_id is None:
            raise BrokerSubmissionUnknownError(
                "Fyers did not return id_fyers for the async order; submission "
                "outcome is unknown."
            )
        return AsyncOrderAcceptance(fyers_async_id=async_id, payload=data)


@dataclass(frozen=True)
class OrderIntentRef:
    id: UUID
    idempotency_key: str
    execution_mode: str


# Backward-compatible name used by the P3 tests/import surface.
PaperOrderIntent = OrderIntentRef


@dataclass(frozen=True)
class SubmissionResult:
    broker_call_made: bool
    outcome: str
    message: str


class RedisOrderRateLimiter:
    """Distributed token bucket shared by every execution-engine caller."""

    _SCRIPT = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local now = redis.call('TIME')
local now_ms = now[1] * 1000 + math.floor(now[2] / 1000)
local values = redis.call('HMGET', key, 'tokens', 'updated_ms')
local tokens = tonumber(values[1])
local updated_ms = tonumber(values[2])
if tokens == nil then tokens = capacity end
if updated_ms == nil then updated_ms = now_ms end
tokens = math.min(capacity, tokens + ((now_ms - updated_ms) * rate / 1000))
if tokens >= 1 then
  tokens = tokens - 1
  redis.call('HSET', key, 'tokens', tokens, 'updated_ms', now_ms)
  redis.call('PEXPIRE', key, 2000)
  return 0
end
local wait_ms = math.ceil((1 - tokens) * 1000 / rate)
redis.call('HSET', key, 'tokens', tokens, 'updated_ms', now_ms)
redis.call('PEXPIRE', key, 2000)
return wait_ms
"""

    def __init__(self, redis, *, rate: int) -> None:
        self._redis = redis
        self._rate = rate

    async def acquire(self) -> None:
        while True:
            wait_ms = int(
                await self._redis.eval(
                    self._SCRIPT,
                    1,
                    "execution_engine:order_ops_bucket",
                    self._rate,
                    1,
                )
            )
            if wait_ms <= 0:
                return
            await asyncio.sleep(wait_ms / 1000)


async def ensure_orders_allowed(db: AsyncSession) -> None:
    result = await db.execute(
        text(
            """
            SELECT enabled, reason
            FROM system_controls
            WHERE control_key = 'global_kill_switch'
            FOR SHARE
            """
        )
    )
    control = result.mappings().one_or_none()
    if control is None:
        raise ExecutionBlockedError(
            "Global kill switch state is unavailable; execution fails closed."
        )
    if control["enabled"]:
        reason = control["reason"] or "No reason recorded."
        raise ExecutionBlockedError(f"Global kill switch is engaged: {reason}")


async def ensure_order_gateway_ready(redis) -> None:
    raw = await redis.get("order_gateway:status")
    if raw is None:
        raise ExecutionBlockedError(
            "Order gateway heartbeat is unavailable; execution fails closed."
        )
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        status = json.loads(raw)
        heartbeat_at = datetime.fromisoformat(status["timestamp"])
        if heartbeat_at.tzinfo is None:
            heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - heartbeat_at).total_seconds()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ExecutionBlockedError(
            "Order gateway heartbeat is invalid; execution fails closed."
        ) from exc
    current_status = status.get("status")
    if current_status != "ready" or age_seconds > 30:
        raise ExecutionBlockedError(
            f"Order gateway is not ready (status={current_status}, age={age_seconds:.1f}s); execution fails closed."
        )


def ensure_execution_mode_armed() -> None:
    if settings.execution_mode == "live" and not settings.live_order_placement_enabled:
        raise ExecutionSafetyError(
            "Live execution mode is selected but LIVE_ORDER_PLACEMENT_ENABLED is "
            "not armed."
        )
    if settings.execution_mode == "live" and not settings.fyers_app_id:
        raise ExecutionSafetyError("FYERS_APP_ID is required for live orders.")


async def create_entry_intent(
    db: AsyncSession,
    *,
    trade_instruction_id: UUID,
    position_id: UUID,
    side: str,
    quantity: int,
    product_type: str,
    order_type: str,
    limit_price: Decimal | None,
) -> OrderIntentRef:
    """Persist one deterministic entry intent before any possible broker call."""
    ensure_execution_mode_armed()
    await ensure_orders_allowed(db)

    execution_mode = settings.execution_mode
    idempotency_key = f"trade-instruction:{trade_instruction_id}:entry:v1"
    intent_id = uuid4()
    reason = (
        "P3 paper mode: intent logged; no broker request was made."
        if execution_mode == "paper"
        else "P4 live mode: durable intent awaiting async Fyers submission."
    )
    result = await db.execute(
        text(
            """
            INSERT INTO order_intents (
                id,
                idempotency_key,
                trade_instruction_id,
                position_id,
                intent_type,
                side,
                quantity,
                product_type,
                order_type,
                limit_price,
                status,
                execution_mode,
                requested_by_component,
                reason
            )
            VALUES (
                :id,
                :idempotency_key,
                :trade_instruction_id,
                :position_id,
                'entry',
                :side,
                :quantity,
                :product_type,
                :order_type,
                :limit_price,
                'created',
                :execution_mode,
                'execution_engine',
                :reason
            )
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id, idempotency_key, execution_mode
            """
        ),
        {
            "id": intent_id,
            "idempotency_key": idempotency_key,
            "trade_instruction_id": trade_instruction_id,
            "position_id": position_id,
            "side": side,
            "quantity": quantity,
            "product_type": product_type,
            "order_type": order_type,
            "limit_price": limit_price,
            "execution_mode": execution_mode,
            "reason": reason,
        },
    )
    created = result.mappings().one_or_none()
    if created is None:
        existing_result = await db.execute(
            text(
                """
                SELECT
                    id,
                    idempotency_key,
                    trade_instruction_id,
                    position_id,
                    execution_mode
                FROM order_intents
                WHERE idempotency_key = :idempotency_key
                """
            ),
            {"idempotency_key": idempotency_key},
        )
        existing = existing_result.mappings().one()
        if (
            existing["trade_instruction_id"] != trade_instruction_id
            or existing["position_id"] != position_id
            or existing["execution_mode"] != execution_mode
        ):
            raise ExecutionSafetyError(
                "Idempotency key collision references a different trade or mode."
            )
        return OrderIntentRef(
            id=existing["id"],
            idempotency_key=existing["idempotency_key"],
            execution_mode=existing["execution_mode"],
        )

    event_type = (
        "paper_order_intent_logged"
        if execution_mode == "paper"
        else "live_order_intent_created"
    )
    await _emit_system_event(
        db,
        event_type=event_type,
        severity="info",
        correlation_id=trade_instruction_id,
        position_id=position_id,
        order_intent_id=created["id"],
        payload={
            "execution_mode": execution_mode,
            "broker_call_made": False,
            "idempotency_key": idempotency_key,
        },
    )
    return OrderIntentRef(
        id=created["id"],
        idempotency_key=created["idempotency_key"],
        execution_mode=created["execution_mode"],
    )


async def create_proposal_entry_intent(
    db: AsyncSession,
    *,
    proposal_id: UUID,
    entry_leg_id: UUID,
    quantity: int,
    observed_price: Decimal,
    trigger_event_timestamp: datetime,
) -> tuple[OrderIntentRef, UUID]:
    """Persist one approved P10 leg intent and its recoverable position state."""
    ensure_execution_mode_armed()
    await ensure_orders_allowed(db)
    if quantity <= 0:
        raise ExecutionSafetyError("Proposal entry quantity must be positive.")

    pause_result = await db.execute(
        text(
            """
            SELECT enabled
            FROM system_controls
            WHERE control_key = 'new_entries_paused'
            FOR SHARE
            """
        )
    )
    paused = pause_result.scalar_one_or_none()
    if paused is None or paused:
        raise ExecutionBlockedError(
            "New-entry control is unavailable or paused; execution fails closed."
        )

    result = await db.execute(
        text(
            """
            SELECT tp.id AS proposal_id, tp.instrument_id, tp.screening_result_id,
                   tp.status AS proposal_status, tp.proposal_hash, tp.live_eligible,
                   tp.entry_session_date, tp.entry_template, tp.initial_stop,
                   tp.t1, tp.t2, tp.t3, tp.geometry,
                   el.id AS leg_id, el.leg_index, el.status AS leg_status,
                   el.position_id, el.trigger_price,
                   pd.expected_proposal_hash
            FROM trade_proposals tp
            JOIN entry_legs el ON el.proposal_id = tp.id
            JOIN proposal_decisions pd ON pd.proposal_id = tp.id
                                    AND pd.decision = 'approved'
            WHERE tp.id = :proposal_id AND el.id = :entry_leg_id
            FOR UPDATE OF tp, el
            """
        ),
        {"proposal_id": proposal_id, "entry_leg_id": entry_leg_id},
    )
    plan = result.mappings().one_or_none()
    if plan is None:
        raise ExecutionSafetyError("Approved proposal leg was not found.")
    if (
        plan["proposal_status"] != "approved"
        or not plan["live_eligible"]
        or plan["expected_proposal_hash"] != plan["proposal_hash"]
    ):
        raise ExecutionSafetyError("Proposal approval/version is not live-eligible.")
    if plan["leg_status"] not in {"trigger_observed", "intent_created"}:
        raise ExecutionSafetyError(
            f"Proposal leg is in unexpected state '{plan['leg_status']}'."
        )

    today_ist = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    if int(plan["leg_index"]) == 1 and plan["entry_session_date"] != today_ist:
        raise ExecutionSafetyError("Initial proposal leg is outside its D1 entry session.")

    execution_mode = settings.execution_mode
    trigger_key = trigger_event_timestamp.astimezone(timezone.utc).isoformat()
    idempotency_key = (
        f"proposal:{proposal_id}:leg:{entry_leg_id}:entry:{trigger_key}"
    )
    existing = await db.execute(
        text(
            """
            SELECT id, idempotency_key, execution_mode, position_id
            FROM order_intents
            WHERE idempotency_key = :key
            """
        ),
        {"key": idempotency_key},
    )
    existing_row = existing.mappings().one_or_none()
    if existing_row is not None:
        return (
            OrderIntentRef(
                id=existing_row["id"],
                idempotency_key=existing_row["idempotency_key"],
                execution_mode=existing_row["execution_mode"],
            ),
            existing_row["position_id"],
        )

    position_id = plan["position_id"]
    if position_id is None:
        if int(plan["leg_index"]) != 1:
            raise ExecutionSafetyError("Add leg has no preceding app-managed position.")
        position_id = uuid4()
        await db.execute(
            text(
                """
                INSERT INTO positions (
                    id, instrument_id, screening_result_id, state, side,
                    quantity, open_quantity, product_type, current_stop_loss,
                    current_target, trailing_rule, proposal_id, entry_template,
                    trailing_rule_type, t1_target, t2_target, t3_target,
                    execution_mode
                ) VALUES (
                    :id, :instrument_id, :screening_result_id, 'pending_entry', 'long',
                    :quantity, 0, 'CNC', :stop, NULL, CAST(:trailing_rule AS jsonb),
                    :proposal_id, :entry_template, 'p10_staged_atr', :t1, :t2, :t3,
                    :execution_mode
                )
                """
            ),
            {
                "id": position_id,
                "instrument_id": plan["instrument_id"],
                "screening_result_id": plan["screening_result_id"],
                "quantity": quantity,
                "stop": plan["initial_stop"],
                "trailing_rule": json.dumps(
                    {
                        "type": "p10_staged_atr",
                        "atr14": str(dict(plan["geometry"] or {}).get("atr14", "0")),
                    }
                ),
                "proposal_id": proposal_id,
                "entry_template": plan["entry_template"],
                "t1": plan["t1"],
                "t2": plan["t2"],
                "t3": plan["t3"],
                "execution_mode": execution_mode,
            },
        )
    else:
        # Reserve add quantity durably before submission. Rejected/unfilled add
        # recovery removes this reservation in the gateway/reconciler.
        await db.execute(
            text(
                """
                UPDATE positions
                SET quantity = quantity + :quantity
                WHERE id = :position_id
                  AND state IN ('open', 'trailing_active')
                """
            ),
            {"position_id": position_id, "quantity": quantity},
        )

    intent_id = uuid4()
    await db.execute(
        text(
            """
            INSERT INTO order_intents (
                id, idempotency_key, position_id, intent_type, side, quantity,
                product_type, order_type, status, execution_mode,
                requested_by_component, reason, proposal_id, entry_leg_id
            ) VALUES (
                :id, :key, :position_id, 'entry', 'buy', :quantity,
                'CNC', 'market', 'created', :execution_mode,
                'execution_engine', :reason, :proposal_id, :entry_leg_id
            )
            """
        ),
        {
            "id": intent_id,
            "key": idempotency_key,
            "position_id": position_id,
            "quantity": quantity,
            "execution_mode": execution_mode,
            "reason": f"Approved P10 leg at observed price {observed_price}",
            "proposal_id": proposal_id,
            "entry_leg_id": entry_leg_id,
        },
    )
    await db.execute(
        text(
            """
            UPDATE entry_legs
            SET status = 'intent_created', position_id = :position_id,
                order_intent_id = :intent_id, updated_at = now()
            WHERE id = :entry_leg_id
            """
        ),
        {
            "position_id": position_id,
            "intent_id": intent_id,
            "entry_leg_id": entry_leg_id,
        },
    )
    return (
        OrderIntentRef(
            id=intent_id,
            idempotency_key=idempotency_key,
            execution_mode=execution_mode,
        ),
        position_id,
    )


async def publish_tick_subscriptions(redis, symbols: list[str]) -> None:
    """Ask the tick worker to subscribe to symbols for open positions."""
    if not symbols:
        return
    await redis.publish(
        REDIS_TICK_SUBS_CHANNEL,
        json.dumps({"action": "subscribe", "symbols": symbols}),
    )


async def complete_paper_entry_fill(
    db: AsyncSession,
    *,
    order_intent_id: UUID,
    position_id: UUID,
    trade_instruction_id: UUID | None = None,
    fill_price: Decimal,
    quantity: int,
) -> bool:
    """
    Immediately fill a paper entry intent and open the position.

    Returns False when the intent was already filled (idempotent replay).
    """
    if settings.execution_mode != "paper":
        raise ExecutionSafetyError(
            "complete_paper_entry_fill requires EXECUTION_MODE=paper."
        )

    stage_row = (
        await db.execute(
            text("SELECT stage FROM p10_rollout_state WHERE id = true")
        )
    ).mappings().one_or_none()
    if stage_row and stage_row["stage"] in {"paper", "reduced_live", "full_live"}:
        raise ExecutionBlockedError(
            "Manual paper entry fills are disabled while P10 automated paper trading is active. "
            "Use P10 proposal approvals."
        )

    intent_result = await db.execute(
        text(
            """
            SELECT id, status, quantity, proposal_id, entry_leg_id
            FROM order_intents
            WHERE id = :order_intent_id
            AND position_id = :position_id
            AND intent_type = 'entry'
            AND execution_mode = 'paper'
            FOR UPDATE
            """
        ),
        {
            "order_intent_id": order_intent_id,
            "position_id": position_id,
        },
    )
    intent = intent_result.mappings().one_or_none()
    if intent is None:
        raise ExecutionSafetyError("Paper entry intent was not found.")
    if intent["status"] == "filled":
        return False
    if intent["status"] != "created":
        raise ExecutionSafetyError(
            f"Paper entry intent is in unexpected status '{intent['status']}'."
        )
    if int(intent["quantity"]) != quantity:
        raise ExecutionSafetyError("Paper fill quantity differs from the durable intent.")

    filled_at = datetime.now(timezone.utc)
    paper_trade_id = f"paper:entry:{order_intent_id}"
    fill_insert = await db.execute(
        text(
            """
            INSERT INTO order_fills (
                id,
                order_intent_id,
                fyers_trade_id,
                filled_at,
                quantity,
                price,
                broker_payload,
                proposal_id,
                entry_leg_id
            )
            VALUES (
                :id,
                :order_intent_id,
                :fyers_trade_id,
                :filled_at,
                :quantity,
                :price,
                CAST(:broker_payload AS jsonb),
                :proposal_id,
                :entry_leg_id
            )
            ON CONFLICT (fyers_trade_id) DO NOTHING
            RETURNING id
            """
        ),
        {
            "id": uuid4(),
            "order_intent_id": order_intent_id,
            "fyers_trade_id": paper_trade_id,
            "filled_at": filled_at,
            "quantity": quantity,
            "price": fill_price,
            "broker_payload": json.dumps(
                {"source": "paper_entry_fill", "price": str(fill_price)}
            ),
            "proposal_id": intent["proposal_id"],
            "entry_leg_id": intent["entry_leg_id"],
        },
    )
    fill_row = fill_insert.mappings().one_or_none()
    if fill_row is None:
        return False
    await enqueue_journal_fill_event(
        db,
        order_fill_id=fill_row["id"],
        position_id=position_id,
        fill_side="entry",
    )
    await db.execute(
        text(
            """
            UPDATE order_intents
            SET
                status = 'filled',
                broker_responded_at = :filled_at,
                reason = 'P5 paper mode: entry filled at planned price for monitor testing.'
            WHERE id = :order_intent_id
              AND status = 'created'
            """
        ),
        {"order_intent_id": order_intent_id, "filled_at": filled_at},
    )
    previous_position = (
        await db.execute(
            text("SELECT state FROM positions WHERE id = :id FOR UPDATE"),
            {"id": position_id},
        )
    ).mappings().one()
    aggregate = (
        await db.execute(
            text(
                """
                SELECT SUM(f.quantity)::integer AS quantity,
                       SUM(f.quantity * f.price) / SUM(f.quantity) AS average_price
                FROM order_fills f
                JOIN order_intents oi ON oi.id = f.order_intent_id
                WHERE oi.position_id = :position_id AND oi.intent_type = 'entry'
                """
            ),
            {"position_id": position_id},
        )
    ).mappings().one()
    total_quantity = int(aggregate["quantity"])
    staged = apportion_staged_exits(total_quantity)
    position_update = await db.execute(
        text(
            """
            UPDATE positions
            SET
                state = 'open',
                open_quantity = :quantity,
                average_entry_price = :average_price,
                t1_shares = :t1_shares,
                t2_shares = :t2_shares,
                t3_shares = :t3_shares,
                runner_shares = :runner_shares,
                opened_at = COALESCE(opened_at, :filled_at)
            WHERE id = :position_id
              AND state IN ('pending_entry', 'open', 'trailing_active')
            RETURNING id
            """
        ),
        {
            "position_id": position_id,
            "quantity": total_quantity,
            "average_price": aggregate["average_price"],
            "t1_shares": staged.t1_shares,
            "t2_shares": staged.t2_shares,
            "t3_shares": staged.t3_shares,
            "runner_shares": staged.runner_shares,
            "filled_at": filled_at,
        },
    )
    if position_update.mappings().one_or_none() is None:
        raise ExecutionSafetyError(
            "Paper entry fill could not open the pending position."
        )
    await db.execute(
        text(
            """
            INSERT INTO position_events (
                position_id,
                event_ts,
                event_type,
                from_state,
                to_state,
                trigger_source,
                observed_price,
                details
            )
            VALUES (
                :position_id,
                :event_ts,
                'entry_filled',
                :from_state,
                'open',
                'execution_engine',
                :observed_price,
                CAST(:details AS jsonb)
            )
            """
        ),
        {
            "position_id": position_id,
            "event_ts": filled_at,
            "observed_price": fill_price,
            "from_state": previous_position["state"],
            "details": json.dumps(
                {
                    "execution_mode": "paper",
                    "broker_call_made": False,
                    "fill_quantity": quantity,
                }
            ),
        },
    )
    if intent["entry_leg_id"] is not None:
        await db.execute(
            text(
                """
                UPDATE entry_legs
                SET status = 'filled', filled_shares = :quantity,
                    filled_avg_price = :fill_price,
                    first_filled_at = COALESCE(first_filled_at, :filled_at),
                    updated_at = now()
                WHERE id = :entry_leg_id
                """
            ),
            {
                "entry_leg_id": intent["entry_leg_id"],
                "quantity": quantity,
                "fill_price": fill_price,
                "filled_at": filled_at,
            },
        )
    await _emit_system_event(
        db,
        severity="info",
        event_type="paper_entry_filled",
        correlation_id=trade_instruction_id or intent["proposal_id"] or position_id,
        position_id=position_id,
        order_intent_id=order_intent_id,
        payload={
            "fill_price": str(fill_price),
            "quantity": quantity,
        },
    )
    return True


async def create_exit_intent(
    db: AsyncSession,
    *,
    position_id: UUID,
    intent_type: ExitIntentType,
    side: Literal["buy", "sell"],
    quantity: int,
    product_type: str,
    observed_price: Decimal,
    reason: str,
    idempotency_suffix: str | None = None,
    exit_purpose: str | None = None,
    is_partial: bool = False,
) -> OrderIntentRef | None:
    """
    Persist a deterministic exit intent and move the position to exit_pending.

    Returns None when the position is already exiting or closed.
    """
    ensure_execution_mode_armed()
    await ensure_orders_allowed(db)

    execution_mode = settings.execution_mode
    suffix = idempotency_suffix or str(intent_type)
    idempotency_key = f"position:{position_id}:{suffix}:v1"
    intent_id = uuid4()

    position_result = await db.execute(
        text(
            """
            SELECT
                id,
                state,
                side,
                open_quantity,
                proposal_id
            FROM positions
            WHERE positions.id = :position_id
            FOR UPDATE OF positions
            """
        ),
        {"position_id": position_id},
    )
    position = position_result.mappings().one_or_none()
    if position is None:
        raise ExecutionSafetyError("Position was not found.")

    prior_result = await db.execute(
        text(
            """
            SELECT id, idempotency_key, execution_mode, status
            FROM order_intents
            WHERE idempotency_key = :idempotency_key
            """
        ),
        {"idempotency_key": idempotency_key},
    )
    prior = prior_result.mappings().one_or_none()
    if prior is not None and prior["status"] in {"cancelled", "rejected"}:
        retry_number = int(
            (
                await db.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM order_intents
                        WHERE position_id = :position_id
                          AND idempotency_key LIKE :prefix
                          AND status IN ('cancelled', 'rejected')
                        """
                    ),
                    {
                        "position_id": position_id,
                        "prefix": f"position:{position_id}:{suffix}%",
                    },
                )
            ).scalar_one()
        )
        idempotency_key = f"position:{position_id}:{suffix}:retry:{retry_number}:v1"
        prior = None
    if prior is not None:
        return OrderIntentRef(
            id=prior["id"],
            idempotency_key=prior["idempotency_key"],
            execution_mode=prior["execution_mode"],
        )
    if position["state"] in {"closed", "cancelled"} or (
        position["state"] == "exit_pending" and not is_partial
    ):
        existing = await db.execute(
            text(
                """
                SELECT id, idempotency_key, execution_mode
                FROM order_intents
                WHERE idempotency_key = :idempotency_key
                """
            ),
            {"idempotency_key": idempotency_key},
        )
        row = existing.mappings().one_or_none()
        if row is not None:
            return OrderIntentRef(
                id=row["id"],
                idempotency_key=row["idempotency_key"],
                execution_mode=row["execution_mode"],
            )
        return None
    if position["state"] not in {"open", "trailing_active"}:
        return None
    if int(position["open_quantity"]) <= 0:
        return None

    from_state = position["state"]
    to_state = from_state if is_partial else "exit_pending"
    if not is_partial:
        await db.execute(
            text(
                """
                UPDATE positions
                SET state = 'exit_pending'
                WHERE id = :position_id
                  AND state IN ('open', 'trailing_active')
                """
            ),
            {"position_id": position_id},
        )

    result = await db.execute(
        text(
            """
            INSERT INTO order_intents (
                id,
                idempotency_key,
                position_id,
                intent_type,
                side,
                quantity,
                product_type,
                order_type,
                status,
                execution_mode,
                requested_by_component,
                reason,
                exit_purpose,
                proposal_id
            )
            VALUES (
                :id,
                :idempotency_key,
                :position_id,
                :intent_type,
                :side,
                :quantity,
                :product_type,
                'market',
                'created',
                :execution_mode,
                'execution_engine',
                :reason,
                :exit_purpose,
                :proposal_id
            )
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id, idempotency_key, execution_mode
            """
        ),
        {
            "id": intent_id,
            "idempotency_key": idempotency_key,
            "position_id": position_id,
            "intent_type": intent_type,
            "side": side,
            "quantity": quantity,
            "product_type": product_type,
            "execution_mode": execution_mode,
            "reason": reason,
            "exit_purpose": exit_purpose,
            "proposal_id": position["proposal_id"],
        },
    )
    created = result.mappings().one_or_none()
    if created is None:
        existing_result = await db.execute(
            text(
                """
                SELECT id, idempotency_key, execution_mode
                FROM order_intents
                WHERE idempotency_key = :idempotency_key
                """
            ),
            {"idempotency_key": idempotency_key},
        )
        existing = existing_result.mappings().one()
        return OrderIntentRef(
            id=existing["id"],
            idempotency_key=existing["idempotency_key"],
            execution_mode=existing["execution_mode"],
        )

    await db.execute(
        text(
            """
            INSERT INTO position_events (
                position_id,
                event_type,
                from_state,
                to_state,
                trigger_source,
                observed_price,
                details
            )
            VALUES (
                :position_id,
                :event_type,
                :from_state,
                :to_state,
                'position_monitor',
                :observed_price,
                CAST(:details AS jsonb)
            )
            """
        ),
        {
            "position_id": position_id,
            "event_type": f"{intent_type}_triggered",
            "from_state": from_state,
            "to_state": to_state,
            "observed_price": observed_price,
            "details": json.dumps(
                {
                    "intent_type": intent_type,
                    "order_intent_id": str(created["id"]),
                    "execution_mode": execution_mode,
                    "exit_purpose": exit_purpose,
                    "partial": is_partial,
                }
            ),
        },
    )
    return OrderIntentRef(
        id=created["id"],
        idempotency_key=created["idempotency_key"],
        execution_mode=created["execution_mode"],
    )


async def complete_paper_exit(
    db: AsyncSession,
    *,
    order_intent_id: UUID,
    position_id: UUID,
    exit_price: Decimal,
) -> bool:
    """Fill a paper exit intent and close the position."""
    if settings.execution_mode != "paper":
        raise ExecutionSafetyError(
            "complete_paper_exit requires EXECUTION_MODE=paper."
        )

    await synchronize_stop_streak(db, "paper")

    intent_result = await db.execute(
        text(
            """
            SELECT id, status, quantity, intent_type, exit_purpose
            FROM order_intents
            WHERE id = :order_intent_id
              AND position_id = :position_id
              AND intent_type IN (
                    'stop_loss_exit',
                    'target_exit',
                    'trailing_exit',
                    'manual_exit',
                    'risk_reduction_exit',
                    'invalid_fill_exit'
              )
              AND execution_mode = 'paper'
            FOR UPDATE
            """
        ),
        {
            "order_intent_id": order_intent_id,
            "position_id": position_id,
        },
    )
    intent = intent_result.mappings().one_or_none()
    if intent is None:
        raise ExecutionSafetyError("Paper exit intent was not found.")
    if intent["status"] == "filled":
        return False
    if intent["status"] != "created":
        raise ExecutionSafetyError(
            f"Paper exit intent is in unexpected status '{intent['status']}'."
        )

    position_result = await db.execute(
        text(
            """
            SELECT
                state,
                side,
                open_quantity,
                average_entry_price,
                realized_pnl,
                current_stop_loss,
                trailing_rule_type,
                trailing_rule,
                high_water_mark,
                trailing_stop,
                t1_target,
                t2_target,
                t3_target,
                t1_shares,
                t2_shares,
                t3_shares,
                runner_shares,
                t1_filled_shares,
                t2_filled_shares,
                t3_filled_shares,
                runner_filled_shares,
                i.tick_size,
                i.fyers_symbol AS symbol
            FROM positions
            JOIN instruments i ON i.id = positions.instrument_id
            WHERE positions.id = :position_id
            FOR UPDATE OF positions
            """
        ),
        {"position_id": position_id},
    )
    position = position_result.mappings().one_or_none()
    if position is None:
        raise ExecutionSafetyError("Position was not found for paper exit.")
    if position["average_entry_price"] is None:
        raise ExecutionSafetyError(
            "Paper exit requires an average entry price on the position."
        )

    filled_at = datetime.now(timezone.utc)
    quantity = min(int(intent["quantity"]), int(position["open_quantity"]))
    if quantity <= 0:
        raise ExecutionSafetyError("Paper exit has no remaining position quantity.")
    pnl_delta = realized_pnl_on_exit(
        side=position["side"],
        average_entry_price=Decimal(position["average_entry_price"]),
        quantity=quantity,
        exit_price=exit_price,
    )
    paper_trade_id = f"paper:exit:{order_intent_id}"

    fill_insert = await db.execute(
        text(
            """
            INSERT INTO order_fills (
                id,
                order_intent_id,
                fyers_trade_id,
                filled_at,
                quantity,
                price,
                broker_payload
            )
            VALUES (
                :id,
                :order_intent_id,
                :fyers_trade_id,
                :filled_at,
                :quantity,
                :price,
                CAST(:broker_payload AS jsonb)
            )
            ON CONFLICT (fyers_trade_id) DO NOTHING
            RETURNING id
            """
        ),
        {
            "id": uuid4(),
            "order_intent_id": order_intent_id,
            "fyers_trade_id": paper_trade_id,
            "filled_at": filled_at,
            "quantity": quantity,
            "price": exit_price,
            "broker_payload": json.dumps(
                {
                    "source": "paper_exit_fill",
                    "intent_type": intent["intent_type"],
                    "price": str(exit_price),
                }
            ),
        },
    )
    fill_row = fill_insert.mappings().one_or_none()
    if fill_row is None:
        return False
    await enqueue_journal_fill_event(
        db,
        order_fill_id=fill_row["id"],
        position_id=position_id,
        fill_side="exit",
    )
    await db.execute(
        text(
            """
            UPDATE order_intents
            SET
                status = 'filled',
                broker_responded_at = :filled_at,
                reason = 'P5 paper mode: exit filled at observed LTP for monitor testing.'
            WHERE id = :order_intent_id
              AND status = 'created'
            """
        ),
        {"order_intent_id": order_intent_id, "filled_at": filled_at},
    )
    previous_state = position["state"]
    remaining_open = max(int(position["open_quantity"]) - quantity, 0)
    t1_delta = t2_delta = t3_delta = 0
    new_stop = Decimal(position["current_stop_loss"])
    new_hwm = (
        Decimal(position["high_water_mark"])
        if position["high_water_mark"] is not None
        else None
    )
    new_trailing_stop = (
        Decimal(position["trailing_stop"])
        if position["trailing_stop"] is not None
        else None
    )
    new_state = "closed" if remaining_open == 0 else previous_state
    if (
        intent["intent_type"] == "target_exit"
        and position["trailing_rule_type"] == "p10_staged_atr"
    ):
        rule = dict(position["trailing_rule"] or {})
        atr14 = Decimal(str(rule.get("atr14", "0")))
        staged = StagedPositionState(
            id=position_id,
            symbol=position["symbol"],
            side=position["side"],
            state=position["state"],
            open_quantity=int(position["open_quantity"]),
            weighted_entry_price=Decimal(position["average_entry_price"]),
            current_stop=new_stop,
            t1_target=Decimal(position["t1_target"]) if position["t1_target"] is not None else None,
            t2_target=Decimal(position["t2_target"]) if position["t2_target"] is not None else None,
            t3_target=Decimal(position["t3_target"]) if position["t3_target"] is not None else None,
            t1_shares=int(position["t1_shares"]),
            t2_shares=int(position["t2_shares"]),
            t3_shares=int(position["t3_shares"]),
            runner_shares=int(position["runner_shares"]),
            t1_filled_shares=int(position["t1_filled_shares"]),
            t2_filled_shares=int(position["t2_filled_shares"]),
            t3_filled_shares=int(position["t3_filled_shares"]),
            runner_filled_shares=int(position["runner_filled_shares"]),
            high_water_mark=new_hwm,
            trailing_stop=new_trailing_stop,
            atr14=atr14,
            tick_size=Decimal(position["tick_size"]),
        )
        allocation = allocate_cumulative_target_fill(
            staged,
            exit_purpose=str(intent["exit_purpose"]),
            fill_quantity=quantity,
        )
        t1_delta, t2_delta, t3_delta = allocation.t1, allocation.t2, allocation.t3
        t1_complete = staged.t1_filled_shares + t1_delta >= staged.t1_shares > 0
        t2_complete = staged.t2_filled_shares + t2_delta >= staged.t2_shares > 0
        if t1_complete:
            new_stop = max(new_stop, staged.weighted_entry_price)
        if t2_complete and remaining_open > 0:
            if atr14 <= 0:
                raise ExecutionSafetyError("P10 T2 fill cannot activate a non-positive ATR trail.")
            new_state = "trailing_active"
            new_hwm = max(new_hwm or exit_price, exit_price)
            candidate = floor_to_tick(
                new_hwm - (Decimal("2") * atr14),
                staged.tick_size,
            )
            new_trailing_stop = max(new_stop, new_trailing_stop or Decimal("0"), candidate)
    await db.execute(
        text(
            """
            UPDATE positions
            SET
                state = :new_state,
                open_quantity = :open_quantity,
                realized_pnl = realized_pnl + :pnl_delta,
                current_stop_loss = :current_stop_loss,
                high_water_mark = :high_water_mark,
                trailing_stop = :trailing_stop,
                t1_filled_shares = t1_filled_shares + :t1_delta,
                t2_filled_shares = t2_filled_shares + :t2_delta,
                t3_filled_shares = t3_filled_shares + :t3_delta,
                closed_at = CASE
                    WHEN :new_state = 'closed' THEN COALESCE(closed_at, :filled_at)
                    ELSE closed_at
                END
            WHERE id = :position_id
              AND state IN ('open', 'trailing_active', 'exit_pending')
            """
        ),
        {
            "position_id": position_id,
            "new_state": new_state,
            "open_quantity": remaining_open,
            "pnl_delta": pnl_delta,
            "filled_at": filled_at,
            "current_stop_loss": new_stop,
            "high_water_mark": new_hwm,
            "trailing_stop": new_trailing_stop,
            "t1_delta": t1_delta,
            "t2_delta": t2_delta,
            "t3_delta": t3_delta,
        },
    )
    await db.execute(
        text(
            """
            INSERT INTO position_events (
                position_id,
                event_ts,
                event_type,
                from_state,
                to_state,
                trigger_source,
                observed_price,
                details
            )
            VALUES (
                :position_id,
                :event_ts,
                :event_type,
                :from_state,
                :to_state,
                'execution_engine',
                :observed_price,
                CAST(:details AS jsonb)
            )
            """
        ),
        {
            "position_id": position_id,
            "event_ts": filled_at,
            "event_type": f"{intent['intent_type']}_filled",
            "from_state": previous_state,
            "to_state": new_state,
            "observed_price": exit_price,
            "details": json.dumps(
                {
                    "execution_mode": "paper",
                    "realized_pnl_delta": str(pnl_delta),
                    "fill_quantity": quantity,
                    "remaining_open_quantity": remaining_open,
                    "exit_purpose": intent["exit_purpose"],
                    "target_fill_allocation": {
                        "t1": t1_delta,
                        "t2": t2_delta,
                        "t3": t3_delta,
                    },
                }
            ),
        },
    )
    await _emit_system_event(
        db,
        severity="info",
        event_type="paper_exit_filled",
        correlation_id=position_id,
        position_id=position_id,
        order_intent_id=order_intent_id,
        payload={
            "exit_price": str(exit_price),
            "intent_type": intent["intent_type"],
            "realized_pnl_delta": str(pnl_delta),
        },
    )
    if new_state == "closed":
        await record_closed_position(db, "paper", position_id, filled_at)
    return True


async def submit_live_exit_intent(
    db: AsyncSession,
    redis,
    *,
    order_intent_id: UUID,
    broker_client: FyersAsyncOrderClient | None = None,
    rate_limiter: RedisOrderRateLimiter | None = None,
    fill_price: Decimal | None = None,
) -> SubmissionResult:
    """Claim and submit a durable exit intent exactly once."""
    ensure_execution_mode_armed()

    snapshot = await _load_live_intent_for_submission(db, order_intent_id)
    if snapshot["intent_type"] == "entry":
        raise ExecutionSafetyError("Use submit_live_entry_intent for entry intents.")

    if snapshot["status"] == "submission_unknown":
        raise ExecutionSafetyError(
            "The prior broker submission outcome is unknown. Automatic retry is "
            "blocked until reconciliation resolves it."
        )
    if snapshot["status"] == "submission_pending":
        return SubmissionResult(
            broker_call_made=False,
            outcome="already_in_progress",
            message="Another request already claimed this intent for submission.",
        )
    if snapshot["status"] != "created":
        return SubmissionResult(
            broker_call_made=False,
            outcome="already_submitted",
            message=f"Intent is already in '{snapshot['status']}' state.",
        )

    try:
        await ensure_orders_allowed(db)
        access_token = None
        if settings.execution_mode == "live":
            try:
                access_token = await get_valid_access_token(redis)
            except AuthUnavailableError as exc:
                raise ExecutionBlockedError(str(exc)) from exc

        await ensure_order_gateway_ready(redis)
        limiter = rate_limiter or RedisOrderRateLimiter(
            redis,
            rate=settings.order_ops_limit,
        )
        await limiter.acquire()
        await ensure_orders_allowed(db)
        await ensure_order_gateway_ready(redis)
    except Exception as exc:
        # Pre-claim failure (e.g. kill switch engaged, gateway not ready, live auth unavailable).
        # Reject intent and restore position so it does not remain stranded in exit_pending.
        await db.execute(
            text(
                """
                UPDATE order_intents
                SET
                    status = 'rejected',
                    broker_responded_at = now(),
                    reason = :message
                WHERE id = :order_intent_id
                  AND status = 'created'
                """
            ),
            {"order_intent_id": order_intent_id, "message": str(exc)},
        )
        if snapshot.get("trade_instruction_id"):
            await db.execute(
                text(
                    """
                    UPDATE trade_instructions
                    SET status = 'rejected'
                    WHERE id = :trade_instruction_id
                    """
                ),
                {"trade_instruction_id": snapshot["trade_instruction_id"]},
            )
        await restore_rejected_exit_position(
            db,
            position_id=snapshot.get("position_id"),
            order_intent_id=order_intent_id,
            trade_instruction_id=snapshot.get("trade_instruction_id"),
            event_type="exit_rejected",
            reason=str(exc),
            trigger_source="execution_engine",
            details={"message": str(exc), "stage": "pre_claim_blocked"},
        )
        await db.commit()
        if isinstance(exc, (ExecutionBlockedError, ExecutionSafetyError)):
            raise
        return SubmissionResult(
            broker_call_made=False,
            outcome="rejected",
            message=str(exc),
        )

    claim = await db.execute(
        text(
            """
            UPDATE order_intents
            SET
                status = 'submission_pending',
                broker_requested_at = now(),
                reason = 'Claimed for one async exit submission; automatic concurrent retries are blocked.'
            WHERE id = :order_intent_id
              AND execution_mode = :execution_mode
              AND status = 'created'
            RETURNING id
            """
        ),
        {
            "order_intent_id": order_intent_id,
            "execution_mode": settings.execution_mode,
        },
    )
    if claim.mappings().one_or_none() is None:
        await db.rollback()
        return SubmissionResult(
            broker_call_made=False,
            outcome="already_in_progress",
            message="Another request claimed this intent for submission.",
        )
    await db.commit()

    if settings.execution_mode == "paper":
        return await _complete_paper_submission(
            db, redis, snapshot=snapshot, fill_price=fill_price
        )

    payload = _build_fyers_order_payload(snapshot)
    client = broker_client or FyersAsyncOrderClient(app_id=settings.fyers_app_id)
    try:
        acceptance = await client.place_order(
            access_token=access_token or "",
            payload=payload,
        )
    except BrokerOrderRejectedError as exc:
        await _record_definite_rejection(
            db,
            snapshot=snapshot,
            payload=exc.payload,
            message=str(exc),
        )
        await db.commit()
        return SubmissionResult(
            broker_call_made=True,
            outcome="rejected",
            message=str(exc),
        )
    except BrokerSubmissionUnknownError as exc:
        await _record_unknown_submission(
            db,
            snapshot=snapshot,
            message=str(exc),
        )
        await db.commit()
        return SubmissionResult(
            broker_call_made=True,
            outcome="submission_unknown",
            message=str(exc),
        )
    except Exception as exc:
        message = (
            "Unexpected broker adapter failure; submission outcome is unknown and "
            "was not retried."
        )
        await _record_unknown_submission(
            db,
            snapshot=snapshot,
            message=message,
        )
        await db.commit()
        raise BrokerSubmissionUnknownError(message) from exc

    result = await db.execute(
        text(
            """
            UPDATE order_intents
            SET
                status = CASE
                    WHEN status = 'submission_pending' THEN 'submitted'
                    ELSE status
                END,
                fyers_async_id = COALESCE(fyers_async_id, :fyers_async_id),
                broker_responded_at = now(),
                reason = CASE
                    WHEN status = 'submission_pending'
                    THEN 'Fyers accepted the async exit request; awaiting order WebSocket correlation.'
                    ELSE reason
                END
            WHERE id = :order_intent_id
              AND status NOT IN ('submission_unknown', 'rejected', 'cancelled')
              AND (
                    fyers_async_id IS NULL
                 OR fyers_async_id = :fyers_async_id
              )
            RETURNING id
            """
        ),
        {
            "order_intent_id": order_intent_id,
            "fyers_async_id": acceptance.fyers_async_id,
        },
    )
    if result.mappings().one_or_none() is None:
        await db.rollback()
        raise ExecutionSafetyError(
            "The exit intent changed state while recording Fyers acceptance."
        )
    await _insert_submission_event(
        db,
        snapshot=snapshot,
        event_type="async_submission_accepted",
        fyers_async_id=acceptance.fyers_async_id,
        broker_payload=acceptance.payload,
    )
    await _emit_system_event(
        db,
        severity="info",
        event_type="live_exit_submitted",
        correlation_id=snapshot["position_id"],
        position_id=snapshot["position_id"],
        order_intent_id=order_intent_id,
        payload={"fyers_async_id": acceptance.fyers_async_id},
    )
    await db.commit()
    return SubmissionResult(
        broker_call_made=True,
        outcome="submitted",
        message="Fyers accepted the async exit request; awaiting order updates.",
    )


async def create_paper_entry_intent(
    db: AsyncSession,
    **kwargs,
) -> PaperOrderIntent:
    if settings.execution_mode != "paper":
        raise ExecutionSafetyError(
            "create_paper_entry_intent requires EXECUTION_MODE=paper."
        )
    return await create_entry_intent(db, **kwargs)


async def _cancel_entry_before_submission(
    db: AsyncSession,
    *,
    snapshot: dict[str, Any],
    reason: str,
) -> bool:
    """Release a P10 reservation when final eligibility fails pre-broker."""
    cancel_result = await db.execute(
        text(
            """
            UPDATE order_intents
            SET status = 'cancelled', reason = :reason
            WHERE id = :intent_id AND status = 'created'
            RETURNING id
            """
        ),
        {"intent_id": snapshot["id"], "reason": reason},
    )
    cancelled = cancel_result.mappings().one_or_none()
    if cancelled is None:
        return False
    if snapshot.get("entry_leg_id") is None:
        return True
    await db.execute(
        text(
            """
            UPDATE positions
            SET quantity = CASE
                    WHEN :leg_index = 1 THEN quantity
                    ELSE GREATEST(open_quantity, quantity - :quantity)
                END,
                state = CASE
                    WHEN :leg_index = 1 AND open_quantity = 0
                    THEN 'cancelled'
                    ELSE state
                END
            WHERE id = :position_id
            """
        ),
        {
            "position_id": snapshot["position_id"],
            "leg_index": snapshot["entry_leg_index"],
            "quantity": snapshot["quantity"],
        },
    )
    await db.execute(
        text(
            """
            UPDATE entry_legs el
            SET status = CASE
                    WHEN tp.entry_trigger_policy_version =
                         'breakout_bar_signal_v2'
                    THEN 'waiting_for_reset'
                    ELSE 'armed'
                END,
                signal_bar_timestamp = NULL,
                order_intent_id = NULL,
                position_id = CASE
                    WHEN el.leg_index = 1 THEN NULL
                    ELSE el.position_id
                END
            FROM trade_proposals tp
            WHERE el.proposal_id = tp.id
              AND el.id = :leg_id
              AND el.order_intent_id = :intent_id
              AND el.status = 'intent_created'
            """
        ),
        {
            "leg_id": snapshot["entry_leg_id"],
            "intent_id": snapshot["id"],
        },
    )
    return True


async def submit_live_entry_intent(
    db: AsyncSession,
    redis,
    *,
    order_intent_id: UUID,
    broker_client: FyersAsyncOrderClient | None = None,
    rate_limiter: RedisOrderRateLimiter | None = None,
    fill_price: Decimal | None = None,
    pre_submit_check: Callable[[], Awaitable[Decimal | None]] | None = None,
) -> SubmissionResult:
    """Claim and submit a durable entry intent exactly once automatically."""
    ensure_execution_mode_armed()

    snapshot = await _load_live_intent_for_submission(db, order_intent_id)
    if snapshot["intent_type"] != "entry":
        raise ExecutionSafetyError("Use submit_live_exit_intent for exit intents.")
    if snapshot["status"] == "submission_unknown":
        raise ExecutionSafetyError(
            "The prior broker submission outcome is unknown. Automatic retry is "
            "blocked until reconciliation resolves it."
        )
    if snapshot["status"] == "submission_pending":
        return SubmissionResult(
            broker_call_made=False,
            outcome="already_in_progress",
            message="Another request already claimed this intent for submission.",
        )
    if snapshot["status"] != "created":
        return SubmissionResult(
            broker_call_made=False,
            outcome="already_submitted",
            message=f"Intent is already in '{snapshot['status']}' state.",
        )

    await ensure_orders_allowed(db)
    access_token = None
    if settings.execution_mode == "live":
        try:
            access_token = await get_valid_access_token(redis)
        except AuthUnavailableError as exc:
            raise ExecutionBlockedError(str(exc)) from exc

    await ensure_order_gateway_ready(redis)
    limiter = rate_limiter or RedisOrderRateLimiter(
        redis,
        rate=settings.order_ops_limit,
    )
    await limiter.acquire()
    # The operator may engage the switch while a burst waits in the queue.
    await ensure_orders_allowed(db)
    await ensure_order_gateway_ready(redis)
    if pre_submit_check is not None:
        # Entry eligibility must be checked after auth/gateway/rate-limit
        # waits and immediately before the durable no-double-place claim.
        try:
            checked_fill_price = await pre_submit_check()
        except Exception as exc:
            cancelled = await _cancel_entry_before_submission(
                db,
                snapshot=snapshot,
                reason=f"Pre-submission eligibility blocked: {exc}",
            )
            if not cancelled:
                await db.rollback()
                return SubmissionResult(
                    broker_call_made=False,
                    outcome="already_in_progress",
                    message="Another request claimed this intent for submission.",
                )
            await db.commit()
            raise
        if checked_fill_price is not None:
            fill_price = checked_fill_price

    claim = await db.execute(
        text(
            """
            UPDATE order_intents
            SET
                status = 'submission_pending',
                broker_requested_at = now(),
                reason = 'Claimed for one async submission; automatic concurrent retries are blocked.'
            WHERE id = :order_intent_id
              AND execution_mode = :execution_mode
              AND status = 'created'
            RETURNING id
            """
        ),
        {
            "order_intent_id": order_intent_id,
            "execution_mode": settings.execution_mode,
        },
    )
    if claim.mappings().one_or_none() is None:
        await db.rollback()
        return SubmissionResult(
            broker_call_made=False,
            outcome="already_in_progress",
            message="Another request claimed this intent for submission.",
        )
    # This commit is deliberate: the no-double-place claim must be durable
    # before the HTTP request can leave this process.
    await db.commit()

    if settings.execution_mode == "paper":
        return await _complete_paper_submission(
            db, redis, snapshot=snapshot, fill_price=fill_price
        )

    payload = _build_fyers_order_payload(snapshot)
    client = broker_client or FyersAsyncOrderClient(app_id=settings.fyers_app_id)
    try:
        acceptance = await client.place_order(
            access_token=access_token or "",
            payload=payload,
        )
    except BrokerOrderRejectedError as exc:
        await _record_definite_rejection(
            db,
            snapshot=snapshot,
            payload=exc.payload,
            message=str(exc),
        )
        await db.commit()
        return SubmissionResult(
            broker_call_made=True,
            outcome="rejected",
            message=str(exc),
        )
    except BrokerSubmissionUnknownError as exc:
        await _record_unknown_submission(
            db,
            snapshot=snapshot,
            message=str(exc),
        )
        await db.commit()
        return SubmissionResult(
            broker_call_made=True,
            outcome="submission_unknown",
            message=str(exc),
        )
    except Exception as exc:
        message = (
            "Unexpected broker adapter failure; submission outcome is unknown and "
            "was not retried."
        )
        await _record_unknown_submission(
            db,
            snapshot=snapshot,
            message=message,
        )
        await db.commit()
        raise BrokerSubmissionUnknownError(message) from exc

    result = await db.execute(
        text(
            """
            UPDATE order_intents
            SET
                status = CASE
                    WHEN status = 'submission_pending' THEN 'submitted'
                    ELSE status
                END,
                fyers_async_id = COALESCE(fyers_async_id, :fyers_async_id),
                broker_responded_at = now(),
                reason = CASE
                    WHEN status = 'submission_pending'
                    THEN 'Fyers accepted the async request; awaiting order WebSocket correlation.'
                    ELSE reason
                END
            WHERE id = :order_intent_id
              AND status NOT IN ('submission_unknown', 'rejected', 'cancelled')
              AND (
                    fyers_async_id IS NULL
                 OR fyers_async_id = :fyers_async_id
              )
            RETURNING id
            """
        ),
        {
            "order_intent_id": order_intent_id,
            "fyers_async_id": acceptance.fyers_async_id,
        },
    )
    if result.mappings().one_or_none() is None:
        await db.rollback()
        raise ExecutionSafetyError(
            "The intent changed state while recording Fyers acceptance."
        )
    await db.execute(
        text(
            """
            UPDATE trade_instructions
            SET status = 'submitted', submitted_at = COALESCE(submitted_at, now())
            WHERE id = :trade_instruction_id
              AND manual_confirmed_at IS NOT NULL
            """
        ),
        {"trade_instruction_id": snapshot["trade_instruction_id"]},
    )
    await _insert_submission_event(
        db,
        snapshot=snapshot,
        event_type="async_submission_accepted",
        fyers_async_id=acceptance.fyers_async_id,
        broker_payload=acceptance.payload,
    )
    await _emit_system_event(
        db,
        severity="info",
        event_type="live_entry_submitted",
        correlation_id=snapshot["trade_instruction_id"] or snapshot["proposal_id"],
        position_id=snapshot["position_id"],
        order_intent_id=order_intent_id,
        payload={"fyers_async_id": acceptance.fyers_async_id},
    )
    await db.commit()
    return SubmissionResult(
        broker_call_made=True,
        outcome="submitted",
        message="Fyers accepted the async request; awaiting order updates.",
    )


async def _complete_paper_submission(
    db: AsyncSession,
    redis,
    *,
    snapshot: dict[str, Any],
    fill_price: Decimal | None,
) -> SubmissionResult:
    """Accept a claimed paper intent, book the fill, and run gateway processors."""
    from app.services.order_gateway import process_order_message, process_trade_message
    from app.services.paper_broker import (
        PaperBrokerError,
        place_paper_order,
        publish_paper_fill_events,
    )

    price = fill_price
    if price is None:
        raw = await redis.get(f"ltp:{snapshot['symbol']}")
        if raw is None:
            await _record_definite_rejection(
                db,
                snapshot=snapshot,
                payload={"s": "error", "message": "missing LTP"},
                message="Paper submission requires a fill price or fresh LTP.",
            )
            await db.commit()
            return SubmissionResult(
                broker_call_made=True,
                outcome="rejected",
                message="Paper submission requires a fill price or fresh LTP.",
            )
        try:
            payload = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            if not isinstance(payload, dict):
                raise ValueError("Cached LTP payload is invalid")
            price = Decimal(str(payload["ltp"]))
            if price <= Decimal("0"):
                raise ValueError("Cached LTP price is non-positive")
            received_at_str = payload.get("received_at")
            if not received_at_str:
                raise ValueError("Cached LTP is missing received_at timestamp")
            received_at = datetime.fromisoformat(received_at_str)
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - received_at).total_seconds()
            if age_seconds > 15.0:
                raise ValueError(f"Cached LTP is stale ({age_seconds:.1f}s > 15s)")
            if age_seconds < -5.0:
                raise ValueError("Cached LTP has future timestamp")
        except Exception as exc:
            await _record_definite_rejection(
                db,
                snapshot=snapshot,
                payload={"s": "error", "message": f"invalid or stale LTP: {exc}"},
                message=f"Paper submission requires a valid and fresh LTP: {exc}",
            )
            await db.commit()
            return SubmissionResult(
                broker_call_made=True,
                outcome="rejected",
                message=f"Paper submission requires a valid and fresh LTP: {exc}",
            )
    try:
        paper_result = await place_paper_order(
            db, snapshot=snapshot, fill_price=price
        )
    except PaperBrokerError as exc:
        await _record_definite_rejection(
            db,
            snapshot=snapshot,
            payload={"s": "error", "message": str(exc)},
            message=str(exc),
        )
        await db.commit()
        return SubmissionResult(
            broker_call_made=True,
            outcome="rejected",
            message=str(exc),
        )

    recorded = await db.execute(
        text(
            """
            UPDATE order_intents
            SET
                status = CASE
                    WHEN status = 'submission_pending' THEN 'submitted'
                    ELSE status
                END,
                fyers_async_id = COALESCE(fyers_async_id, :fyers_async_id),
                fyers_order_id = COALESCE(fyers_order_id, :fyers_order_id),
                broker_responded_at = now(),
                reason = CASE
                    WHEN status = 'submission_pending'
                    THEN 'Paper broker accepted; applying synthetic fill events.'
                    ELSE reason
                END
            WHERE id = :order_intent_id
              AND status NOT IN ('submission_unknown', 'rejected', 'cancelled')
            RETURNING id
            """
        ),
        {
            "order_intent_id": snapshot["id"],
            "fyers_async_id": paper_result.fyers_async_id,
            "fyers_order_id": paper_result.fyers_order_id,
        },
    )
    if recorded.mappings().one_or_none() is None:
        await db.rollback()
        raise ExecutionSafetyError(
            "The intent changed state while recording paper acceptance."
        )
    await process_order_message(db, paper_result.order_message)
    await process_trade_message(db, paper_result.trade_message)
    await db.commit()
    try:
        await publish_paper_fill_events(redis, paper_result)
    except Exception:
        pass
    return SubmissionResult(
        broker_call_made=True,
        outcome="submitted",
        message="Paper broker accepted and applied the fill.",
    )


async def _load_live_intent_for_submission(
    db: AsyncSession,
    order_intent_id: UUID,
) -> dict[str, Any]:
    result = await db.execute(
        text(
            """
            SELECT
                oi.id,
                oi.idempotency_key,
                oi.trade_instruction_id,
                oi.proposal_id,
                oi.entry_leg_id,
                oi.position_id,
                oi.intent_type,
                oi.side,
                oi.quantity,
                oi.product_type,
                oi.order_type,
                oi.limit_price,
                oi.trigger_price,
                oi.status,
                oi.execution_mode,
                i.fyers_symbol AS symbol,
                ti.manual_confirmed_at,
                tp.status AS proposal_status,
                tp.proposal_hash,
                tp.live_eligible,
                tp.entry_session_date,
                pd.expected_proposal_hash,
                    el.status AS entry_leg_status,
                    el.leg_index AS entry_leg_index,
                    el.eligible_session_start,
                    el.eligible_session_end,
                p.state AS position_state,
                p.open_quantity,
                p.side AS position_side
            FROM order_intents oi
            JOIN positions p ON p.id = oi.position_id
            JOIN instruments i ON i.id = p.instrument_id
            LEFT JOIN trade_instructions ti ON ti.id = oi.trade_instruction_id
            LEFT JOIN trade_proposals tp ON tp.id = oi.proposal_id
            LEFT JOIN proposal_decisions pd ON pd.proposal_id = tp.id
                                             AND pd.decision = 'approved'
            LEFT JOIN entry_legs el ON el.id = oi.entry_leg_id
            WHERE oi.id = :order_intent_id
            """
        ),
        {"order_intent_id": order_intent_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise ExecutionSafetyError("Order intent was not found.")
    snapshot = dict(row)
    if snapshot["execution_mode"] != settings.execution_mode:
        raise ExecutionSafetyError(
            "Intent execution_mode does not match the process EXECUTION_MODE."
        )
    if snapshot["product_type"] != "CNC":
        raise ExecutionSafetyError("Intents require product_type=CNC.")

    intent_type = snapshot["intent_type"]
    if intent_type == "entry":
        manual_approved = snapshot["manual_confirmed_at"] is not None
        proposal_approved = (
            snapshot.get("proposal_id") is not None
            and snapshot.get("proposal_status") == "approved"
            and snapshot.get("live_eligible") is True
            and snapshot.get("expected_proposal_hash") == snapshot.get("proposal_hash")
            and snapshot.get("entry_leg_status")
            in {"intent_created", "submitted", "partially_filled"}
        )
        if not manual_approved and not proposal_approved:
            raise ExecutionSafetyError(
                "Live entry requires an exact approved proposal or confirmed manual instruction."
            )
        if proposal_approved:
            today_ist = datetime.now(timezone.utc).astimezone(
                ZoneInfo("Asia/Kolkata")
            ).date()
            if int(snapshot.get("entry_leg_index") or 0) == 1:
                if snapshot["entry_session_date"] != today_ist:
                    raise ExecutionSafetyError("Approved initial proposal entry is outside D1.")
            elif not (
                snapshot.get("eligible_session_start")
                and snapshot.get("eligible_session_end")
                and snapshot["eligible_session_start"] <= today_ist
                <= snapshot["eligible_session_end"]
            ):
                raise ExecutionSafetyError("Approved add leg is outside its eligibility window.")
        if snapshot["side"] != "buy":
            raise ExecutionSafetyError(
                "P4 live CNC entry supports buy orders only. CNC short entry is "
                "not supported."
            )
    elif intent_type in {
        "stop_loss_exit",
        "target_exit",
        "trailing_exit",
        "manual_exit",
        "risk_reduction_exit",
        "invalid_fill_exit",
    }:
        if snapshot["order_type"] != "market":
            raise ExecutionSafetyError("P5 live exits require market orders.")
        if snapshot["position_state"] not in {
            "open",
            "trailing_active",
            "exit_pending",
        }:
            raise ExecutionSafetyError(
                "Live exit submission requires an active or exiting position."
            )
        expected_side = "sell" if snapshot["position_side"] == "long" else "buy"
        if snapshot["side"] != expected_side:
            raise ExecutionSafetyError(
                "Exit intent side does not match the open position."
            )
    else:
        raise ExecutionSafetyError(
            f"Unsupported live intent type '{intent_type}'."
        )
    return snapshot


def _build_fyers_order_payload(intent: dict[str, Any]) -> dict[str, Any]:
    order_types = {"limit": 1, "market": 2, "stop": 3, "stop_limit": 4}
    return {
        "symbol": intent["symbol"],
        "qty": int(intent["quantity"]),
        "type": order_types[intent["order_type"]],
        "side": 1 if intent["side"] == "buy" else -1,
        "productType": intent["product_type"],
        "limitPrice": (
            float(intent["limit_price"]) if intent["limit_price"] is not None else 0
        ),
        "stopPrice": (
            float(intent["trigger_price"])
            if intent["trigger_price"] is not None
            else 0
        ),
        # P10 market/MPP orders may not leave a live remainder that races a
        # correction or later signal. IOC preserves actual partial fills and
        # lets the order gateway terminalize the unfilled balance.
        "validity": (
            "IOC"
            if intent.get("proposal_id") is not None
            and intent["intent_type"] == "entry"
            and intent["order_type"] == "market"
            else "DAY"
        ),
        "disclosedQty": 0,
        "offlineOrder": False,
        "isSliceOrder": False,
        "orderTag": _order_tag(intent["id"]),
    }


def _order_tag(intent_id: UUID) -> str:
    encoded = base64.urlsafe_b64encode(intent_id.bytes).decode().rstrip("=")
    return f"stv-{encoded}"


def _extract_async_id(payload: dict[str, Any]) -> str | None:
    candidates: list[Any] = [
        payload.get("id_fyers"),
        payload.get("idFyers"),
    ]
    nested = payload.get("data")
    if isinstance(nested, dict):
        candidates.extend((nested.get("id_fyers"), nested.get("idFyers")))
    for value in candidates:
        if value is not None and str(value).strip():
            return str(value)
    return None


async def restore_rejected_exit_position(
    db: AsyncSession,
    *,
    position_id: UUID | str | None,
    order_intent_id: UUID | str | None = None,
    trade_instruction_id: UUID | str | None = None,
    event_type: str = "exit_rejected",
    reason: str = "Exit intent rejected or cancelled",
    trigger_source: str = "execution_engine",
    details: dict[str, Any] | None = None,
) -> str | None:
    """
    Restore an exit_pending position back to active monitoring (open / trailing_active)
    when an exit intent is definitively rejected or cancelled (TRD-002 / OG-002).
    Uses the exact from_state from the prior position_events transition to exit_pending.
    """
    if not position_id:
        return None

    pos_result = await db.execute(
        text(
            """
            SELECT
                id,
                state,
                open_quantity,
                trailing_stop,
                t2_filled_shares,
                t2_shares
            FROM positions
            WHERE id = :position_id
            FOR UPDATE OF positions
            """
        ),
        {"position_id": position_id},
    )
    position = pos_result.mappings().one_or_none()
    if (
        position is None
        or position["state"] != "exit_pending"
        or int(position["open_quantity"] or 0) <= 0
    ):
        return None

    # Retrieve the exact from_state from the position_events entry that moved it to exit_pending
    prior_event_result = await db.execute(
        text(
            """
            SELECT from_state
            FROM position_events
            WHERE position_id = :position_id
              AND to_state = 'exit_pending'
            ORDER BY id DESC
            LIMIT 1
            """
        ),
        {"position_id": position_id},
    )
    prior_event = prior_event_result.mappings().one_or_none()
    if prior_event and prior_event["from_state"] in {"open", "trailing_active"}:
        restored_state = prior_event["from_state"]
    else:
        # Fallback: check if trailing stop was active
        is_trailing = (
            position["trailing_stop"] is not None
            and int(position["t2_filled_shares"] or 0) >= int(position["t2_shares"] or 0) > 0
        )
        restored_state = "trailing_active" if is_trailing else "open"

    await db.execute(
        text(
            """
            UPDATE positions
            SET state = :restored_state
            WHERE id = :position_id
              AND state = 'exit_pending'
              AND open_quantity > 0
            """
        ),
        {"position_id": position_id, "restored_state": restored_state},
    )
    payload_details = details or {}
    await db.execute(
        text(
            """
            INSERT INTO position_events (
                position_id,
                event_type,
                from_state,
                to_state,
                trigger_source,
                details
            )
            VALUES (
                :position_id,
                :event_type,
                'exit_pending',
                :restored_state,
                :trigger_source,
                CAST(:details AS jsonb)
            )
            """
        ),
        {
            "position_id": position_id,
            "event_type": event_type,
            "restored_state": restored_state,
            "trigger_source": trigger_source,
            "details": json.dumps(payload_details),
        },
    )
    await _emit_system_event(
        db,
        severity="critical",
        event_type="exit_intent_rejected_position_rearmed",
        correlation_id=trade_instruction_id,
        position_id=position_id,
        order_intent_id=order_intent_id,
        payload={
            "reason": reason,
            "restored_state": restored_state,
            "residual_open_quantity": int(position["open_quantity"]),
            "broker_payload": payload_details,
        },
    )
    return restored_state


async def _record_definite_rejection(
    db: AsyncSession,
    *,
    snapshot: dict[str, Any],
    payload: dict[str, Any],
    message: str,
) -> None:
    await db.execute(
        text(
            """
            UPDATE order_intents
            SET
                status = 'rejected',
                broker_responded_at = now(),
                reason = :message
            WHERE id = :order_intent_id
              AND status = 'submission_pending'
            """
        ),
        {"order_intent_id": snapshot["id"], "message": message},
    )
    if snapshot.get("trade_instruction_id"):
        await db.execute(
            text(
                """
                UPDATE trade_instructions
                SET status = 'rejected'
                WHERE id = :trade_instruction_id
                """
            ),
            {"trade_instruction_id": snapshot["trade_instruction_id"]},
        )
    if snapshot.get("intent_type") == "entry":
        await _cancel_unfilled_position(
            db,
            snapshot=snapshot,
            event_type="entry_rejected",
            details={"message": message, "broker_payload": payload},
        )
    else:
        # Exit intent rejected: restore position back to active monitoring (TRD-002)
        await restore_rejected_exit_position(
            db,
            position_id=snapshot.get("position_id"),
            order_intent_id=snapshot.get("id"),
            trade_instruction_id=snapshot.get("trade_instruction_id"),
            event_type="exit_rejected",
            reason=message,
            trigger_source="execution_engine",
            details={"message": message, "broker_payload": payload},
        )
    await _insert_submission_event(
        db,
        snapshot=snapshot,
        event_type="async_submission_rejected",
        broker_payload=payload,
    )


async def _record_unknown_submission(
    db: AsyncSession,
    *,
    snapshot: dict[str, Any],
    message: str,
) -> None:
    await db.execute(
        text(
            """
            UPDATE order_intents
            SET
                status = 'submission_unknown',
                broker_responded_at = now(),
                reason = :message
            WHERE id = :order_intent_id
              AND status = 'submission_pending'
            """
        ),
        {"order_intent_id": snapshot["id"], "message": message},
    )
    await _insert_submission_event(
        db,
        snapshot=snapshot,
        event_type="async_submission_unknown",
        broker_payload={"message": message},
    )
    await _emit_system_event(
        db,
        severity="critical",
        event_type="live_entry_submission_unknown",
        correlation_id=snapshot["trade_instruction_id"],
        position_id=snapshot["position_id"],
        order_intent_id=snapshot["id"],
        payload={"message": message, "automatic_retry": False},
    )


async def _cancel_unfilled_position(
    db: AsyncSession,
    *,
    snapshot: dict[str, Any],
    event_type: str,
    details: dict[str, Any],
) -> None:
    result = await db.execute(
        text(
            """
            UPDATE positions
            SET state = 'cancelled'
            WHERE id = :position_id
              AND state = 'pending_entry'
              AND open_quantity = 0
            RETURNING id
            """
        ),
        {"position_id": snapshot["position_id"]},
    )
    if result.mappings().one_or_none() is None:
        return
    await db.execute(
        text(
            """
            INSERT INTO position_events (
                position_id,
                event_type,
                from_state,
                to_state,
                trigger_source,
                details
            )
            VALUES (
                :position_id,
                :event_type,
                'pending_entry',
                'cancelled',
                'execution_engine',
                CAST(:details AS jsonb)
            )
            """
        ),
        {
            "position_id": snapshot["position_id"],
            "event_type": event_type,
            "details": json.dumps(details),
        },
    )


async def _insert_submission_event(
    db: AsyncSession,
    *,
    snapshot: dict[str, Any],
    event_type: str,
    broker_payload: dict[str, Any],
    fyers_async_id: str | None = None,
) -> None:
    event_key = f"execution:{snapshot['id']}:{event_type}"
    await db.execute(
        text(
            """
            INSERT INTO order_events (
                order_intent_id,
                event_type,
                broker_event_key,
                fyers_async_id,
                fyers_status,
                broker_payload
            )
            VALUES (
                :order_intent_id,
                :event_type,
                :broker_event_key,
                :fyers_async_id,
                :fyers_status,
                CAST(:broker_payload AS jsonb)
            )
            ON CONFLICT (broker_event_key) DO NOTHING
            """
        ),
        {
            "order_intent_id": snapshot["id"],
            "event_type": event_type,
            "broker_event_key": event_key,
            "fyers_async_id": fyers_async_id,
            "fyers_status": broker_payload.get("s"),
            "broker_payload": json.dumps(broker_payload),
        },
    )


async def _emit_system_event(
    db: AsyncSession,
    *,
    severity: str,
    event_type: str,
    correlation_id: UUID,
    position_id: UUID,
    order_intent_id: UUID,
    payload: dict[str, Any],
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO system_events (
                component,
                severity,
                event_type,
                correlation_id,
                position_id,
                order_intent_id,
                payload
            )
            VALUES (
                'execution_engine',
                :severity,
                :event_type,
                :correlation_id,
                :position_id,
                :order_intent_id,
                CAST(:payload AS jsonb)
            )
            """
        ),
        {
            "severity": severity,
            "event_type": event_type,
            "correlation_id": correlation_id,
            "position_id": position_id,
            "order_intent_id": order_intent_id,
            "payload": json.dumps(payload),
        },
    )
