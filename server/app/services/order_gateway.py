"""Replay-safe persistence for Fyers order-WebSocket order and trade updates."""

import hashlib
import base64
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading import realized_pnl_on_exit


class OrderGatewayError(RuntimeError):
    pass


_STATUS_RANK = {
    "created": 0,
    "submission_pending": 1,
    "submission_unknown": 1,
    "submitted": 2,
    "acknowledged": 3,
    "partially_filled": 4,
    "filled": 5,
    "rejected": 5,
    "cancel_requested": 3,
    "cancelled": 5,
}

_NUMERIC_ORDER_STATUSES = {
    1: "cancelled",
    2: "filled",
    4: "submitted",
    5: "rejected",
    6: "acknowledged",
    7: "cancelled",
}


async def process_order_message(
    db: AsyncSession,
    message: dict[str, Any],
) -> bool:
    """Persist one OnOrders message. Returns False for duplicate/unmatched data."""
    payload = _message_payload(message, "orders")
    async_id = _string(payload.get("id_fyers") or payload.get("idFyers"))
    fyers_order_id = _string(payload.get("id") or payload.get("orderNumber"))
    exchange_order_id = _string(
        payload.get("exchOrdId") or payload.get("exchangeOrderNo")
    )
    tagged_intent_id = _intent_id_from_tag(payload.get("orderTag"))

    intent = await _find_intent(
        db,
        async_id=async_id,
        fyers_order_id=fyers_order_id,
        exchange_order_id=exchange_order_id,
        tagged_intent_id=tagged_intent_id,
    )
    if intent is None:
        await _emit_unmatched_event(db, "unmatched_order_update", payload)
        return False

    _validate_correlation_ids(
        intent,
        async_id=async_id,
        fyers_order_id=fyers_order_id,
        exchange_order_id=exchange_order_id,
    )

    event_key = _broker_event_key("order", payload)
    raw_status = payload.get("status")
    filled_quantity = _integer(payload.get("filledQty"))
    average_price = _decimal(payload.get("tradedPrice"))
    inserted = await db.execute(
        text(
            """
            INSERT INTO order_events (
                order_intent_id,
                event_ts,
                event_type,
                broker_event_key,
                fyers_async_id,
                fyers_order_id,
                exchange_order_id,
                fyers_status,
                filled_quantity,
                average_price,
                broker_payload
            )
            VALUES (
                :order_intent_id,
                :event_ts,
                'order_update',
                :broker_event_key,
                :fyers_async_id,
                :fyers_order_id,
                :exchange_order_id,
                :fyers_status,
                :filled_quantity,
                :average_price,
                CAST(:broker_payload AS jsonb)
            )
            ON CONFLICT (broker_event_key) DO NOTHING
            RETURNING id
            """
        ),
        {
            "order_intent_id": intent["id"],
            "event_ts": _event_time(payload.get("orderDateTime")),
            "broker_event_key": event_key,
            "fyers_async_id": async_id,
            "fyers_order_id": fyers_order_id,
            "exchange_order_id": exchange_order_id,
            "fyers_status": str(raw_status) if raw_status is not None else None,
            "filled_quantity": filled_quantity,
            "average_price": average_price,
            "broker_payload": _json(payload),
        },
    )
    if inserted.mappings().one_or_none() is None:
        return False

    new_status = _map_order_status(
        raw_status=raw_status,
        filled_quantity=filled_quantity,
        requested_quantity=int(intent["quantity"]),
    )
    effective_status = _non_regressing_status(intent["status"], new_status)
    await db.execute(
        text(
            """
            UPDATE order_intents
            SET
                status = :status,
                fyers_async_id = COALESCE(fyers_async_id, :fyers_async_id),
                fyers_order_id = COALESCE(fyers_order_id, :fyers_order_id),
                exchange_order_id = COALESCE(
                    exchange_order_id,
                    :exchange_order_id
                ),
                broker_responded_at = COALESCE(broker_responded_at, now()),
                reason = COALESCE(:reason, reason)
            WHERE id = :order_intent_id
            """
        ),
        {
            "order_intent_id": intent["id"],
            "status": effective_status,
            "fyers_async_id": async_id,
            "fyers_order_id": fyers_order_id,
            "exchange_order_id": exchange_order_id,
            "reason": _string(payload.get("message")),
        },
    )
    await _mark_instruction_submitted(db, intent["trade_instruction_id"])

    if effective_status in {"rejected", "cancelled"}:
        await _close_unfilled_entry(
            db,
            intent=intent,
            terminal_status=effective_status,
            details=payload,
        )
    return True


async def process_trade_message(
    db: AsyncSession,
    message: dict[str, Any],
) -> bool:
    """Persist one OnTrades fill and apply its entry-position aggregate."""
    payload = _message_payload(message, "trades")
    async_id = _string(payload.get("id_fyers") or payload.get("idFyers"))
    fyers_order_id = _string(payload.get("orderNumber") or payload.get("id"))
    exchange_order_id = _string(
        payload.get("exchangeOrderNo") or payload.get("exchOrdId")
    )
    tagged_intent_id = _intent_id_from_tag(payload.get("orderTag"))
    intent = await _find_intent(
        db,
        async_id=async_id,
        fyers_order_id=fyers_order_id,
        exchange_order_id=exchange_order_id,
        tagged_intent_id=tagged_intent_id,
    )
    if intent is None:
        await _emit_unmatched_event(db, "unmatched_trade_update", payload)
        return False

    _validate_correlation_ids(
        intent,
        async_id=async_id,
        fyers_order_id=fyers_order_id,
        exchange_order_id=exchange_order_id,
    )
    quantity = _integer(payload.get("tradedQty"))
    price = _decimal(payload.get("tradePrice"))
    if quantity is None or quantity <= 0 or price is None or price < 0:
        raise OrderGatewayError("Fyers trade update has invalid quantity or price.")

    trade_id = _string(payload.get("tradeNumber"))
    if trade_id is None:
        trade_id = f"synthetic:{_broker_event_key('fill', payload)}"
    filled_at = _event_time(payload.get("orderDateTime"))
    fill_id = await db.execute(
        text(
            """
            INSERT INTO order_fills (
                order_intent_id,
                fyers_trade_id,
                filled_at,
                quantity,
                price,
                broker_payload
            )
            VALUES (
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
            "order_intent_id": intent["id"],
            "fyers_trade_id": trade_id,
            "filled_at": filled_at,
            "quantity": quantity,
            "price": price,
            "broker_payload": _json(payload),
        },
    )
    if fill_id.mappings().one_or_none() is None:
        return False

    await db.execute(
        text(
            """
            INSERT INTO order_events (
                order_intent_id,
                event_ts,
                event_type,
                broker_event_key,
                fyers_async_id,
                fyers_order_id,
                exchange_order_id,
                fyers_status,
                filled_quantity,
                average_price,
                broker_payload
            )
            VALUES (
                :order_intent_id,
                :event_ts,
                'trade_fill',
                :broker_event_key,
                :fyers_async_id,
                :fyers_order_id,
                :exchange_order_id,
                'fill',
                :filled_quantity,
                :average_price,
                CAST(:broker_payload AS jsonb)
            )
            ON CONFLICT (broker_event_key) DO NOTHING
            """
        ),
        {
            "order_intent_id": intent["id"],
            "event_ts": filled_at,
            "broker_event_key": _broker_event_key("trade", payload),
            "fyers_async_id": async_id,
            "fyers_order_id": fyers_order_id,
            "exchange_order_id": exchange_order_id,
            "filled_quantity": quantity,
            "average_price": price,
            "broker_payload": _json(payload),
        },
    )

    aggregate_result = await db.execute(
        text(
            """
            SELECT
                COALESCE(SUM(quantity), 0)::integer AS filled_quantity,
                CASE
                    WHEN COALESCE(SUM(quantity), 0) = 0 THEN NULL
                    ELSE SUM(quantity * price) / SUM(quantity)
                END AS average_price
            FROM order_fills
            WHERE order_intent_id = :order_intent_id
            """
        ),
        {"order_intent_id": intent["id"]},
    )
    aggregate = aggregate_result.mappings().one()
    total_filled = int(aggregate["filled_quantity"])
    requested_quantity = int(intent["quantity"])
    if total_filled > requested_quantity:
        await _emit_overfill_event(
            db,
            intent=intent,
            total_filled=total_filled,
        )
    open_quantity = min(total_filled, requested_quantity)
    intent_status = (
        "filled" if total_filled >= requested_quantity else "partially_filled"
    )
    await db.execute(
        text(
            """
            UPDATE order_intents
            SET
                status = :status,
                fyers_async_id = COALESCE(fyers_async_id, :fyers_async_id),
                fyers_order_id = COALESCE(fyers_order_id, :fyers_order_id),
                exchange_order_id = COALESCE(
                    exchange_order_id,
                    :exchange_order_id
                ),
                broker_responded_at = COALESCE(broker_responded_at, now())
            WHERE id = :order_intent_id
            """
        ),
        {
            "order_intent_id": intent["id"],
            "status": intent_status,
            "fyers_async_id": async_id,
            "fyers_order_id": fyers_order_id,
            "exchange_order_id": exchange_order_id,
        },
    )

    if intent["intent_type"] == "entry":
        previous_state = intent["position_state"]
        await db.execute(
            text(
                """
                UPDATE positions
                SET
                    state = 'open',
                    open_quantity = :open_quantity,
                    average_entry_price = :average_price,
                    opened_at = COALESCE(opened_at, :filled_at)
                WHERE id = :position_id
                  AND state IN ('pending_entry', 'open')
                """
            ),
            {
                "position_id": intent["position_id"],
                "open_quantity": open_quantity,
                "average_price": aggregate["average_price"],
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
                    'open',
                    'order_gateway',
                    :observed_price,
                    CAST(:details AS jsonb)
                )
                """
            ),
            {
                "position_id": intent["position_id"],
                "event_ts": filled_at,
                "event_type": (
                    "entry_filled"
                    if intent_status == "filled"
                    else "entry_partially_filled"
                ),
                "from_state": previous_state,
                "observed_price": price,
                "details": _json(
                    {
                        "fyers_trade_id": trade_id,
                        "fill_quantity": quantity,
                        "total_filled": total_filled,
                        "average_entry_price": str(aggregate["average_price"]),
                    }
                ),
            },
        )
    elif intent["intent_type"] in {
        "stop_loss_exit",
        "target_exit",
        "trailing_exit",
        "manual_exit",
    }:
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
            {"position_id": intent["position_id"]},
        )
        position = position_result.mappings().one_or_none()
        if position is None:
            return True
        previous_state = position["state"]
        exit_qty = min(total_filled, int(position["open_quantity"]))
        if exit_qty <= 0 or position["average_entry_price"] is None:
            return True
        pnl_delta = realized_pnl_on_exit(
            side=position["side"],
            average_entry_price=Decimal(position["average_entry_price"]),
            quantity=exit_qty,
            exit_price=Decimal(str(aggregate["average_price"])),
        )
        remaining_open = max(int(position["open_quantity"]) - exit_qty, 0)
        new_state = "closed" if remaining_open == 0 else previous_state
        await db.execute(
            text(
                """
                UPDATE positions
                SET
                    state = :new_state,
                    open_quantity = :open_quantity,
                    realized_pnl = realized_pnl + :pnl_delta,
                    closed_at = CASE
                        WHEN :new_state = 'closed' THEN COALESCE(closed_at, :filled_at)
                        ELSE closed_at
                    END
                WHERE id = :position_id
                  AND state IN ('open', 'trailing_active', 'exit_pending')
                """
            ),
            {
                "position_id": intent["position_id"],
                "new_state": new_state,
                "open_quantity": remaining_open,
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
                    :to_state,
                    'order_gateway',
                    :observed_price,
                    CAST(:details AS jsonb)
                )
                """
            ),
            {
                "position_id": intent["position_id"],
                "event_ts": filled_at,
                "event_type": (
                    f"{intent['intent_type']}_filled"
                    if intent_status == "filled"
                    else f"{intent['intent_type']}_partially_filled"
                ),
                "from_state": previous_state,
                "to_state": new_state,
                "observed_price": price,
                "details": _json(
                    {
                        "fyers_trade_id": trade_id,
                        "fill_quantity": quantity,
                        "total_filled": total_filled,
                        "realized_pnl_delta": str(pnl_delta),
                    }
                ),
            },
        )
    if intent.get("trade_instruction_id") is not None:
        await _mark_instruction_submitted(db, intent["trade_instruction_id"])
    return True


async def _find_intent(
    db: AsyncSession,
    *,
    async_id: str | None,
    fyers_order_id: str | None,
    exchange_order_id: str | None,
    tagged_intent_id: UUID | None,
) -> dict[str, Any] | None:
    result = await db.execute(
        text(
            """
            SELECT
                oi.*,
                p.state AS position_state,
                p.open_quantity
            FROM order_intents oi
            JOIN positions p ON p.id = oi.position_id
            WHERE oi.execution_mode = 'live'
              AND (
                    (:async_id IS NOT NULL AND oi.fyers_async_id = :async_id)
                 OR (:fyers_order_id IS NOT NULL
                     AND oi.fyers_order_id = :fyers_order_id)
                 OR (:exchange_order_id IS NOT NULL
                     AND oi.exchange_order_id = :exchange_order_id)
                 OR (:tagged_intent_id IS NOT NULL
                     AND oi.id = :tagged_intent_id)
              )
            FOR UPDATE OF oi, p
            """
        ),
        {
            "async_id": async_id,
            "fyers_order_id": fyers_order_id,
            "exchange_order_id": exchange_order_id,
            "tagged_intent_id": tagged_intent_id,
        },
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


def _validate_correlation_ids(
    intent: dict[str, Any],
    *,
    async_id: str | None,
    fyers_order_id: str | None,
    exchange_order_id: str | None,
) -> None:
    pairs = (
        ("id_fyers", intent.get("fyers_async_id"), async_id),
        ("Fyers order ID", intent.get("fyers_order_id"), fyers_order_id),
        ("exchange order ID", intent.get("exchange_order_id"), exchange_order_id),
    )
    for label, existing, incoming in pairs:
        if existing is not None and incoming is not None and str(existing) != incoming:
            raise OrderGatewayError(
                f"Conflicting {label} for order intent {intent['id']}."
            )


def _map_order_status(
    *,
    raw_status: Any,
    filled_quantity: int | None,
    requested_quantity: int,
) -> str:
    if filled_quantity is not None and 0 < filled_quantity < requested_quantity:
        return "partially_filled"
    if filled_quantity is not None and filled_quantity >= requested_quantity:
        return "filled"
    if isinstance(raw_status, str):
        normalized = raw_status.strip().lower().replace(" ", "_")
        string_statuses = {
            "cancelled": "cancelled",
            "canceled": "cancelled",
            "filled": "filled",
            "traded": "filled",
            "rejected": "rejected",
            "pending": "acknowledged",
            "acknowledged": "acknowledged",
            "transit": "submitted",
            "expired": "cancelled",
        }
        if normalized in string_statuses:
            return string_statuses[normalized]
        try:
            raw_status = int(normalized)
        except ValueError:
            return "acknowledged"
    if isinstance(raw_status, (int, float)):
        return _NUMERIC_ORDER_STATUSES.get(int(raw_status), "acknowledged")
    return "acknowledged"


def _non_regressing_status(current: str, incoming: str) -> str:
    if current in {"filled", "rejected", "cancelled"}:
        return current
    if _STATUS_RANK.get(incoming, 0) < _STATUS_RANK.get(current, 0):
        return current
    return incoming


async def _mark_instruction_submitted(
    db: AsyncSession,
    instruction_id: UUID,
) -> None:
    await db.execute(
        text(
            """
            UPDATE trade_instructions
            SET
                status = CASE
                    WHEN status IN ('confirmed', 'submitted') THEN 'submitted'
                    ELSE status
                END,
                submitted_at = COALESCE(submitted_at, now())
            WHERE id = :instruction_id
            """
        ),
        {"instruction_id": instruction_id},
    )


async def _close_unfilled_entry(
    db: AsyncSession,
    *,
    intent: dict[str, Any],
    terminal_status: str,
    details: dict[str, Any],
) -> None:
    if intent["intent_type"] != "entry" or int(intent["open_quantity"]) > 0:
        return
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
        {"position_id": intent["position_id"]},
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
                'order_gateway',
                CAST(:details AS jsonb)
            )
            """
        ),
        {
            "position_id": intent["position_id"],
            "event_type": f"entry_{terminal_status}",
            "details": _json(details),
        },
    )
    instruction_status = "rejected" if terminal_status == "rejected" else "cancelled"
    await db.execute(
        text(
            """
            UPDATE trade_instructions
            SET status = :status
            WHERE id = :instruction_id
            """
        ),
        {
            "instruction_id": intent["trade_instruction_id"],
            "status": instruction_status,
        },
    )


async def _emit_unmatched_event(
    db: AsyncSession,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO system_events (
                component,
                severity,
                event_type,
                payload
            )
            VALUES (
                'order_gateway',
                'warning',
                :event_type,
                CAST(:payload AS jsonb)
            )
            """
        ),
        {"event_type": event_type, "payload": _json(payload)},
    )


async def _emit_overfill_event(
    db: AsyncSession,
    *,
    intent: dict[str, Any],
    total_filled: int,
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
                'order_gateway',
                'critical',
                'broker_overfill_detected',
                :correlation_id,
                :position_id,
                :order_intent_id,
                CAST(:payload AS jsonb)
            )
            """
        ),
        {
            "correlation_id": intent["trade_instruction_id"],
            "position_id": intent["position_id"],
            "order_intent_id": intent["id"],
            "payload": _json(
                {
                    "requested_quantity": int(intent["quantity"]),
                    "broker_filled_quantity": total_filled,
                }
            ),
        },
    )


def _message_payload(message: dict[str, Any], key: str) -> dict[str, Any]:
    nested = message.get(key)
    if isinstance(nested, dict):
        return dict(nested)
    return dict(message)


def _broker_event_key(kind: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{kind}:{canonical}".encode()).hexdigest()


def _intent_id_from_tag(value: Any) -> UUID | None:
    tag = _string(value)
    if tag is None:
        return None
    if tag.startswith("stv-"):
        encoded = tag.removeprefix("stv-")
        try:
            raw = base64.urlsafe_b64decode(encoded + ("=" * (-len(encoded) % 4)))
            return UUID(bytes=raw)
        except (ValueError, TypeError):
            return None
    # Accept the original development tag format for already-created fixtures.
    if not tag.startswith("stvcp-"):
        return None
    try:
        return UUID(tag.removeprefix("stvcp-"))
    except ValueError:
        return None


def _event_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str) and value.strip():
        candidate = value.strip()
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            return (
                parsed
                if parsed.tzinfo is not None
                else parsed.replace(tzinfo=timezone.utc)
            )
        except ValueError:
            pass
        for format_string in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(candidate, format_string).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue
    return datetime.now(timezone.utc)


def _string(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _integer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise OrderGatewayError(f"Invalid integer value from Fyers: {value!r}") from exc


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise OrderGatewayError(f"Invalid decimal value from Fyers: {value!r}") from exc


def _json(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))
