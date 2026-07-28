"""Idempotent paper/live order intent creation and Fyers submission."""

import asyncio
import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.trading import ExitIntentType, realized_pnl_on_exit
from app.services.auth_service import AuthUnavailableError, get_valid_access_token

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
            "Live order gateway heartbeat is unavailable; execution fails closed."
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
            "Live order gateway heartbeat is invalid; execution fails closed."
        ) from exc
    if status.get("status") != "running" or age_seconds > 30:
        raise ExecutionBlockedError(
            "Live order gateway is not healthy; execution fails closed."
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
    trade_instruction_id: UUID,
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

    intent_result = await db.execute(
        text(
            """
            SELECT id, status, quantity
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

    filled_at = datetime.now(timezone.utc)
    paper_trade_id = f"paper:entry:{order_intent_id}"
    await db.execute(
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
        },
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
    position_update = await db.execute(
        text(
            """
            UPDATE positions
            SET
                state = 'open',
                open_quantity = :quantity,
                average_entry_price = :fill_price,
                opened_at = COALESCE(opened_at, :filled_at)
            WHERE id = :position_id
              AND state = 'pending_entry'
            RETURNING id
            """
        ),
        {
            "position_id": position_id,
            "quantity": quantity,
            "fill_price": fill_price,
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
                'pending_entry',
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
            "details": json.dumps(
                {
                    "execution_mode": "paper",
                    "broker_call_made": False,
                    "fill_quantity": quantity,
                }
            ),
        },
    )
    await _emit_system_event(
        db,
        severity="info",
        event_type="paper_entry_filled",
        correlation_id=trade_instruction_id,
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
) -> OrderIntentRef | None:
    """
    Persist a deterministic exit intent and move the position to exit_pending.

    Returns None when the position is already exiting or closed.
    """
    ensure_execution_mode_armed()
    await ensure_orders_allowed(db)

    execution_mode = settings.execution_mode
    idempotency_key = f"position:{position_id}:{intent_type}:v1"
    intent_id = uuid4()

    position_result = await db.execute(
        text(
            """
            SELECT
                id,
                state,
                side,
                open_quantity
            FROM positions
            WHERE id = :position_id
            FOR UPDATE
            """
        ),
        {"position_id": position_id},
    )
    position = position_result.mappings().one_or_none()
    if position is None:
        raise ExecutionSafetyError("Position was not found.")
    if position["state"] in {"closed", "cancelled", "exit_pending"}:
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
                reason
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
                :reason
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
                'exit_pending',
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
            "observed_price": observed_price,
            "details": json.dumps(
                {
                    "intent_type": intent_type,
                    "order_intent_id": str(created["id"]),
                    "execution_mode": execution_mode,
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

    intent_result = await db.execute(
        text(
            """
            SELECT id, status, quantity, intent_type
            FROM order_intents
            WHERE id = :order_intent_id
              AND position_id = :position_id
              AND intent_type IN (
                    'stop_loss_exit',
                    'target_exit',
                    'trailing_exit',
                    'manual_exit'
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
                realized_pnl
            FROM positions
            WHERE id = :position_id
            FOR UPDATE
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
    quantity = int(intent["quantity"])
    pnl_delta = realized_pnl_on_exit(
        side=position["side"],
        average_entry_price=Decimal(position["average_entry_price"]),
        quantity=quantity,
        exit_price=exit_price,
    )
    paper_trade_id = f"paper:exit:{order_intent_id}"

    await db.execute(
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
    await db.execute(
        text(
            """
            UPDATE positions
            SET
                state = 'closed',
                open_quantity = 0,
                realized_pnl = realized_pnl + :pnl_delta,
                closed_at = COALESCE(closed_at, :filled_at)
            WHERE id = :position_id
              AND state IN ('open', 'trailing_active', 'exit_pending')
            """
        ),
        {
            "position_id": position_id,
            "pnl_delta": pnl_delta,
            "filled_at": filled_at,
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
                'closed',
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
            "observed_price": exit_price,
            "details": json.dumps(
                {
                    "execution_mode": "paper",
                    "realized_pnl_delta": str(pnl_delta),
                    "fill_quantity": quantity,
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
    return True


async def submit_live_exit_intent(
    db: AsyncSession,
    redis,
    *,
    order_intent_id: UUID,
    broker_client: FyersAsyncOrderClient | None = None,
    rate_limiter: RedisOrderRateLimiter | None = None,
) -> SubmissionResult:
    """Claim and submit a durable live exit intent exactly once."""
    ensure_execution_mode_armed()
    if settings.execution_mode != "live":
        return SubmissionResult(
            broker_call_made=False,
            outcome="paper_logged",
            message="Paper intent logged; no broker request was made.",
        )

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

    await ensure_orders_allowed(db)
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

    claim = await db.execute(
        text(
            """
            UPDATE order_intents
            SET
                status = 'submission_pending',
                broker_requested_at = now(),
                reason = 'Claimed for one async Fyers exit submission; automatic concurrent retries are blocked.'
            WHERE id = :order_intent_id
              AND execution_mode = 'live'
              AND status = 'created'
            RETURNING id
            """
        ),
        {"order_intent_id": order_intent_id},
    )
    if claim.mappings().one_or_none() is None:
        await db.rollback()
        return SubmissionResult(
            broker_call_made=False,
            outcome="already_in_progress",
            message="Another request claimed this intent for submission.",
        )
    await db.commit()

    payload = _build_fyers_order_payload(snapshot)
    client = broker_client or FyersAsyncOrderClient(app_id=settings.fyers_app_id)
    try:
        acceptance = await client.place_order(
            access_token=access_token,
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


async def submit_live_entry_intent(
    db: AsyncSession,
    redis,
    *,
    order_intent_id: UUID,
    broker_client: FyersAsyncOrderClient | None = None,
    rate_limiter: RedisOrderRateLimiter | None = None,
) -> SubmissionResult:
    """Claim and submit a durable live intent exactly once automatically."""
    ensure_execution_mode_armed()
    if settings.execution_mode != "live":
        return SubmissionResult(
            broker_call_made=False,
            outcome="paper_logged",
            message="Paper intent logged; no broker request was made.",
        )

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

    claim = await db.execute(
        text(
            """
            UPDATE order_intents
            SET
                status = 'submission_pending',
                broker_requested_at = now(),
                reason = 'Claimed for one async Fyers submission; automatic concurrent retries are blocked.'
            WHERE id = :order_intent_id
              AND execution_mode = 'live'
              AND status = 'created'
            RETURNING id
            """
        ),
        {"order_intent_id": order_intent_id},
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

    payload = _build_fyers_order_payload(snapshot)
    client = broker_client or FyersAsyncOrderClient(app_id=settings.fyers_app_id)
    try:
        acceptance = await client.place_order(
            access_token=access_token,
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
        correlation_id=snapshot["trade_instruction_id"],
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
                p.state AS position_state,
                p.open_quantity,
                p.side AS position_side
            FROM order_intents oi
            JOIN positions p ON p.id = oi.position_id
            JOIN instruments i ON i.id = p.instrument_id
            LEFT JOIN trade_instructions ti ON ti.id = oi.trade_instruction_id
            WHERE oi.id = :order_intent_id
            """
        ),
        {"order_intent_id": order_intent_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise ExecutionSafetyError("Order intent was not found.")
    snapshot = dict(row)
    if snapshot["execution_mode"] != "live":
        raise ExecutionSafetyError("Only a live intent can be submitted here.")
    if snapshot["product_type"] != "CNC":
        raise ExecutionSafetyError("Live intents require product_type=CNC.")

    intent_type = snapshot["intent_type"]
    if intent_type == "entry":
        if snapshot["manual_confirmed_at"] is None:
            raise ExecutionSafetyError(
                "Live entry requires an explicitly confirmed trade instruction."
            )
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
        "validity": "DAY",
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
    await _cancel_unfilled_position(
        db,
        snapshot=snapshot,
        event_type="entry_rejected",
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
