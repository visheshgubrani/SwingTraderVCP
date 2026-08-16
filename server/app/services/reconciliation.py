"""Periodic reconciliation of DB money-path state against Fyers broker books."""

import base64
import json
import logging
from collections import defaultdict
from decimal import Decimal
from typing import Any
from uuid import UUID

from arq.connections import ArqRedis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.services.auth_service import AuthUnavailableError, get_valid_access_token
from app.services.fyers_broker_reads import BrokerBooks, FyersBrokerReadClient, FyersBrokerReadError
from app.services.order_gateway import process_order_message, process_trade_message
from app.services.paper_broker import PaperBrokerError, fetch_preflight as fetch_paper_preflight

logger = logging.getLogger(__name__)

_TERMINAL_INTENT_STATUSES = frozenset({"filled", "rejected", "cancelled"})
_OPEN_POSITION_STATES = frozenset(
    {"pending_entry", "open", "trailing_active", "exit_pending"}
)


async def run_reconciliation(
    ctx: dict[str, Any],
    triggered_by: str = "scheduler",
) -> dict[str, Any]:
    """arq job: compare local live state to Fyers and heal or flag discrepancies."""
    redis: ArqRedis = ctx["redis"]
    job_id = str(ctx.get("job_id", triggered_by))

    async with async_session() as db:
        job_run_id, recon_run_id = await _start_runs(
            db,
            job_key=f"reconciliation_{job_id}",
            triggered_by=triggered_by,
        )
        await db.commit()

        try:
            if settings.execution_mode == "paper":
                snapshot = await fetch_paper_preflight(db)
                books = snapshot.books
            else:
                access_token = await get_valid_access_token(redis)
                client = FyersBrokerReadClient(app_id=settings.fyers_app_id)
                books = await client.fetch_all(access_token=access_token)
        except (AuthUnavailableError, FyersBrokerReadError, PaperBrokerError) as exc:
            await _fail_runs(
                db,
                job_run_id=job_run_id,
                recon_run_id=recon_run_id,
                error=str(exc),
            )
            await _emit_system_event(
                db,
                severity="critical",
                event_type="reconciliation_fetch_failed",
                payload={"error": str(exc), "triggered_by": triggered_by},
            )
            await db.commit()
            logger.error("Reconciliation broker fetch failed: %s", exc)
            return {"status": "failed", "error": str(exc), "run_id": str(recon_run_id)}

        summary = await _reconcile_books(db, recon_run_id=recon_run_id, books=books)
        critical_open = int(summary.get("critical_open", 0))
        discrepancies = int(summary.get("total_items", 0))

        await _finish_runs(
            db,
            job_run_id=job_run_id,
            recon_run_id=recon_run_id,
            discrepancies=discrepancies,
            summary=summary,
        )
        if critical_open > 0:
            await _emit_system_event(
                db,
                severity="critical",
                event_type="reconciliation_critical_items",
                payload={
                    "reconciliation_run_id": str(recon_run_id),
                    "critical_open": critical_open,
                    "summary": summary,
                },
            )
        await db.commit()
        logger.info(
            "Reconciliation succeeded (run=%s, items=%s, healed=%s)",
            recon_run_id,
            discrepancies,
            summary.get("healed", 0),
        )
        return {
            "status": "succeeded",
            "run_id": str(recon_run_id),
            "discrepancies_found": discrepancies,
            "summary": summary,
        }


async def _reconcile_books(
    db: AsyncSession,
    *,
    recon_run_id: UUID,
    books: BrokerBooks,
) -> dict[str, Any]:
    local_intents = await _load_live_intents(db)
    known_trade_ids = await _load_known_trade_ids(db)
    local_positions = await _load_open_positions(db)

    summary: dict[str, Any] = defaultdict(int)
    matched_order_keys: set[str] = set()
    matched_trade_keys: set[str] = set()

    intent_by_id = {str(row["id"]): row for row in local_intents}
    order_indexes = _build_order_indexes(books.orders, intent_by_id)

    for intent in local_intents:
        broker_order = _match_broker_order(intent, order_indexes)
        if broker_order is None:
            if intent["status"] == "submission_unknown":
                await _insert_item(
                    db,
                    recon_run_id=recon_run_id,
                    domain="orders",
                    issue_type="submission_unknown_unresolved",
                    severity="critical",
                    local_record_id=str(intent["id"]),
                    broker_record_id=None,
                    local_snapshot=_snapshot_intent(intent),
                    broker_snapshot={},
                )
                summary["submission_unknown_unresolved"] += 1
                summary["critical_open"] += 1
            continue

        matched_order_keys.add(_broker_order_key(broker_order))
        healed = await _heal_order(db, intent=intent, broker_order=broker_order)
        if healed:
            await _insert_item(
                db,
                recon_run_id=recon_run_id,
                domain="orders",
                issue_type="status_mismatch_healed",
                severity="info",
                local_record_id=str(intent["id"]),
                broker_record_id=_string(broker_order.get("id")),
                local_snapshot=_snapshot_intent(intent),
                broker_snapshot=broker_order,
                resolution_status="resolved",
            )
            summary["status_mismatch_healed"] += 1
            summary["healed"] += 1

    for order in books.orders:
        if str(order.get("productType", "")).upper() != "CNC":
            continue
        key = _broker_order_key(order)
        if key in matched_order_keys:
            continue
        if _match_local_intent(order, intent_by_id, order_indexes) is not None:
            continue
        await _insert_item(
            db,
            recon_run_id=recon_run_id,
            domain="orders",
            issue_type="external_unmatched_order",
            severity="warning",
            local_record_id=None,
            broker_record_id=_string(order.get("id")),
            local_snapshot={},
            broker_snapshot=order,
        )
        summary["external_unmatched_order"] += 1
        summary["warning_open"] += 1

    trade_indexes = _build_trade_indexes(books.trades, intent_by_id)
    for trade in books.trades:
        if str(trade.get("productType", "")).upper() not in {"", "CNC"}:
            continue
        trade_id = _string(trade.get("tradeNumber"))
        if trade_id and trade_id in known_trade_ids:
            continue
        intent = _match_broker_trade(trade, trade_indexes, intent_by_id)
        if intent is None:
            await _insert_item(
                db,
                recon_run_id=recon_run_id,
                domain="fills",
                issue_type="external_unmatched_trade",
                severity="warning",
                local_record_id=None,
                broker_record_id=trade_id,
                local_snapshot={},
                broker_snapshot=trade,
            )
            summary["external_unmatched_trade"] += 1
            summary["warning_open"] += 1
            continue

        matched_trade_keys.add(trade_id or _broker_trade_key(trade))
        healed = await _heal_trade(db, trade=trade)
        if healed:
            await _insert_item(
                db,
                recon_run_id=recon_run_id,
                domain="fills",
                issue_type="missing_fill_healed",
                severity="info",
                local_record_id=str(intent["id"]),
                broker_record_id=trade_id,
                local_snapshot=_snapshot_intent(intent),
                broker_snapshot=trade,
                resolution_status="resolved",
            )
            summary["missing_fill_healed"] += 1
            summary["healed"] += 1
            if trade_id:
                known_trade_ids.add(trade_id)

    broker_qty = _aggregate_broker_quantities(books.positions, books.holdings)
    local_qty = {
        row["fyers_symbol"]: int(row["open_quantity"])
        for row in local_positions
        if int(row["open_quantity"]) > 0
    }

    all_symbols = set(local_qty) | set(broker_qty)
    for symbol in sorted(all_symbols):
        db_qty = local_qty.get(symbol, 0)
        fy_qty = broker_qty.get(symbol, 0)
        if db_qty == fy_qty:
            continue
        if db_qty > 0 and fy_qty == 0:
            issue_type = "local_only_position"
            severity = "warning"
        elif db_qty == 0 and fy_qty > 0:
            issue_type = "broker_only_position"
            severity = "warning"
        else:
            issue_type = "qty_mismatch"
            severity = "critical"
        await _insert_item(
            db,
            recon_run_id=recon_run_id,
            domain="positions",
            issue_type=issue_type,
            severity=severity,
            local_record_id=_local_position_id(local_positions, symbol),
            broker_record_id=symbol,
            local_snapshot={"symbol": symbol, "open_quantity": db_qty},
            broker_snapshot={"symbol": symbol, "broker_quantity": fy_qty},
        )
        summary[issue_type] += 1
        if severity == "critical":
            summary["critical_open"] += 1
        else:
            summary["warning_open"] += 1

    summary["total_items"] = sum(
        summary[key]
        for key in summary
        if key not in {"healed", "critical_open", "warning_open", "total_items"}
    )
    return dict(summary)


async def _heal_order(
    db: AsyncSession,
    *,
    intent: dict[str, Any],
    broker_order: dict[str, Any],
) -> bool:
    if intent["status"] in _TERMINAL_INTENT_STATUSES:
        return False
    message = {"s": "ok", "orders": broker_order}
    return await process_order_message(db, message)


async def _heal_trade(db: AsyncSession, *, trade: dict[str, Any]) -> bool:
    message = {"s": "ok", "trades": trade}
    return await process_trade_message(db, message)


async def _start_runs(
    db: AsyncSession,
    *,
    job_key: str,
    triggered_by: str,
) -> tuple[UUID, UUID]:
    job_result = await db.execute(
        text(
            """
            INSERT INTO job_runs (
                job_type,
                job_key,
                triggered_by,
                status,
                started_at
            )
            VALUES (
                'reconciliation',
                :job_key,
                :triggered_by,
                'running',
                now()
            )
            RETURNING id
            """
        ),
        {"job_key": job_key, "triggered_by": triggered_by},
    )
    job_run_id = job_result.scalar_one()

    recon_result = await db.execute(
        text(
            """
            INSERT INTO reconciliation_runs (status, started_at)
            VALUES ('running', now())
            RETURNING id
            """
        )
    )
    recon_run_id = recon_result.scalar_one()
    return job_run_id, recon_run_id


async def _finish_runs(
    db: AsyncSession,
    *,
    job_run_id: UUID,
    recon_run_id: UUID,
    discrepancies: int,
    summary: dict[str, Any],
) -> None:
    await db.execute(
        text(
            """
            UPDATE job_runs
            SET
                status = 'succeeded',
                completed_at = now(),
                result_payload = CAST(:summary AS jsonb)
            WHERE id = :run_id
            """
        ),
        {"run_id": job_run_id, "summary": _json(summary)},
    )
    await db.execute(
        text(
            """
            UPDATE reconciliation_runs
            SET
                status = 'succeeded',
                completed_at = now(),
                discrepancies_found = :discrepancies,
                summary = CAST(:summary AS jsonb)
            WHERE id = :run_id
            """
        ),
        {
            "run_id": recon_run_id,
            "discrepancies": discrepancies,
            "summary": _json(summary),
        },
    )


async def _fail_runs(
    db: AsyncSession,
    *,
    job_run_id: UUID,
    recon_run_id: UUID,
    error: str,
) -> None:
    await db.execute(
        text(
            """
            UPDATE job_runs
            SET
                status = 'failed',
                completed_at = now(),
                error_message = :error
            WHERE id = :run_id
            """
        ),
        {"run_id": job_run_id, "error": error},
    )
    await db.execute(
        text(
            """
            UPDATE reconciliation_runs
            SET
                status = 'failed',
                completed_at = now(),
                error_message = :error
            WHERE id = :run_id
            """
        ),
        {"run_id": recon_run_id, "error": error},
    )


async def _insert_item(
    db: AsyncSession,
    *,
    recon_run_id: UUID,
    domain: str,
    issue_type: str,
    severity: str,
    local_record_id: str | None,
    broker_record_id: str | None,
    local_snapshot: dict[str, Any],
    broker_snapshot: dict[str, Any],
    resolution_status: str = "open",
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO reconciliation_items (
                reconciliation_run_id,
                domain,
                local_record_id,
                broker_record_id,
                issue_type,
                severity,
                local_snapshot,
                broker_snapshot,
                resolution_status,
                resolved_at
            )
            VALUES (
                :recon_run_id,
                :domain,
                :local_record_id,
                :broker_record_id,
                :issue_type,
                :severity,
                CAST(:local_snapshot AS jsonb),
                CAST(:broker_snapshot AS jsonb),
                :resolution_status,
                CASE WHEN :resolution_status = 'resolved' THEN now() ELSE NULL END
            )
            """
        ),
        {
            "recon_run_id": recon_run_id,
            "domain": domain,
            "local_record_id": local_record_id,
            "broker_record_id": broker_record_id,
            "issue_type": issue_type,
            "severity": severity,
            "local_snapshot": _json(local_snapshot),
            "broker_snapshot": _json(broker_snapshot),
            "resolution_status": resolution_status,
        },
    )


async def _emit_system_event(
    db: AsyncSession,
    *,
    severity: str,
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
                'reconciliation',
                :severity,
                :event_type,
                CAST(:payload AS jsonb)
            )
            """
        ),
        {
            "severity": severity,
            "event_type": event_type,
            "payload": _json(payload),
        },
    )


async def _load_live_intents(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(
        text(
            """
            SELECT
                oi.*,
                p.state AS position_state,
                p.open_quantity
            FROM order_intents oi
            JOIN positions p ON p.id = oi.position_id
            WHERE oi.execution_mode = :execution_mode
              AND oi.status NOT IN ('rejected', 'cancelled')
            ORDER BY oi.created_at ASC
            """
        ),
        {"execution_mode": settings.execution_mode},
    )
    return [dict(row) for row in result.mappings().all()]


async def _load_known_trade_ids(db: AsyncSession) -> set[str]:
    result = await db.execute(
        text(
            """
            SELECT fyers_trade_id
            FROM order_fills
            WHERE fyers_trade_id IS NOT NULL
            """
        )
    )
    return {str(row["fyers_trade_id"]) for row in result.mappings().all()}


async def _load_open_positions(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(
        text(
            """
            SELECT
                p.id,
                p.state,
                p.open_quantity,
                p.quantity,
                i.fyers_symbol,
                i.isin
            FROM positions p
            JOIN instruments i ON i.id = p.instrument_id
            WHERE p.state NOT IN ('closed', 'cancelled')
              AND p.execution_mode = :execution_mode
            ORDER BY i.fyers_symbol ASC
            """
        ),
        {"execution_mode": settings.execution_mode},
    )
    return [dict(row) for row in result.mappings().all()]


def _build_order_indexes(
    orders: list[dict[str, Any]],
    intent_by_id: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    indexes: dict[str, dict[str, Any]] = {}
    for order in orders:
        for key in _order_lookup_keys(order, intent_by_id):
            indexes.setdefault(key, order)
    return indexes


def _build_trade_indexes(
    trades: list[dict[str, Any]],
    intent_by_id: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    indexes: dict[str, dict[str, Any]] = {}
    for trade in trades:
        for key in _trade_lookup_keys(trade, intent_by_id):
            indexes.setdefault(key, trade)
    return indexes


def _match_broker_order(
    intent: dict[str, Any],
    order_indexes: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    for key in _intent_lookup_keys(intent):
        order = order_indexes.get(key)
        if order is not None:
            return order
    return None


def _match_local_intent(
    order: dict[str, Any],
    intent_by_id: dict[str, dict[str, Any]],
    order_indexes: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    tagged_id = _intent_id_from_tag(order.get("orderTag"))
    if tagged_id is not None:
        intent = intent_by_id.get(str(tagged_id))
        if intent is not None:
            return intent
    for key in _order_lookup_keys(order, intent_by_id):
        if key in order_indexes and order_indexes[key] is order:
            for intent in intent_by_id.values():
                if _match_broker_order(intent, {key: order}) is not None:
                    return intent
    return None


def _match_broker_trade(
    trade: dict[str, Any],
    trade_indexes: dict[str, dict[str, Any]],
    intent_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    for key in _trade_lookup_keys(trade, intent_by_id):
        if key in trade_indexes:
            matched_trade = trade_indexes[key]
            if matched_trade is trade:
                for intent in intent_by_id.values():
                    async_id = _string(intent.get("fyers_async_id"))
                    fyers_order_id = _string(intent.get("fyers_order_id"))
                    exchange_order_id = _string(intent.get("exchange_order_id"))
                    if async_id and async_id == _string(
                        matched_trade.get("id_fyers") or matched_trade.get("idFyers")
                    ):
                        return intent
                    if fyers_order_id and fyers_order_id == _string(
                        matched_trade.get("orderNumber") or matched_trade.get("id")
                    ):
                        return intent
                    if exchange_order_id and exchange_order_id == _string(
                        matched_trade.get("exchangeOrderNo")
                        or matched_trade.get("exchOrdId")
                    ):
                        return intent
    return None


def _intent_lookup_keys(intent: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    async_id = _string(intent.get("fyers_async_id"))
    fyers_order_id = _string(intent.get("fyers_order_id"))
    exchange_order_id = _string(intent.get("exchange_order_id"))
    if async_id:
        keys.append(f"async:{async_id}")
    if fyers_order_id:
        keys.append(f"order:{fyers_order_id}")
    if exchange_order_id:
        keys.append(f"exchange:{exchange_order_id}")
    keys.append(f"tag:{intent['id']}")
    return keys


def _order_lookup_keys(
    order: dict[str, Any],
    intent_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    keys: list[str] = []
    async_id = _string(order.get("id_fyers") or order.get("idFyers"))
    fyers_order_id = _string(order.get("id") or order.get("orderNumber"))
    exchange_order_id = _string(order.get("exchOrdId") or order.get("exchangeOrderNo"))
    if async_id:
        keys.append(f"async:{async_id}")
    if fyers_order_id:
        keys.append(f"order:{fyers_order_id}")
    if exchange_order_id:
        keys.append(f"exchange:{exchange_order_id}")
    tagged_id = _intent_id_from_tag(order.get("orderTag"))
    if tagged_id is not None and str(tagged_id) in intent_by_id:
        keys.append(f"tag:{tagged_id}")
    return keys


def _trade_lookup_keys(
    trade: dict[str, Any],
    intent_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    keys: list[str] = []
    async_id = _string(trade.get("id_fyers") or trade.get("idFyers"))
    fyers_order_id = _string(trade.get("orderNumber") or trade.get("id"))
    exchange_order_id = _string(trade.get("exchangeOrderNo") or trade.get("exchOrdId"))
    if async_id:
        keys.append(f"async:{async_id}")
    if fyers_order_id:
        keys.append(f"order:{fyers_order_id}")
    if exchange_order_id:
        keys.append(f"exchange:{exchange_order_id}")
    return keys


def _aggregate_broker_quantities(
    positions: list[dict[str, Any]],
    holdings: list[dict[str, Any]],
) -> dict[str, int]:
    qty_by_symbol: dict[str, int] = defaultdict(int)
    for row in positions:
        if str(row.get("productType", "")).upper() != "CNC":
            continue
        symbol = _string(row.get("symbol"))
        if symbol is None:
            continue
        net_qty = row.get("netQty")
        try:
            net = int(net_qty or 0)
        except (TypeError, ValueError):
            continue
        if net != 0:
            qty_by_symbol[symbol] = max(qty_by_symbol[symbol], abs(net))

    for row in holdings:
        symbol = _string(row.get("symbol"))
        if symbol is None:
            continue
        try:
            remaining = int(row.get("remainingQuantity") or 0)
        except (TypeError, ValueError):
            continue
        if remaining > 0:
            qty_by_symbol[symbol] = max(qty_by_symbol[symbol], remaining)
    return dict(qty_by_symbol)


def _broker_order_key(order: dict[str, Any]) -> str:
    return (
        _string(order.get("id"))
        or _string(order.get("orderNumber"))
        or _string(order.get("exchOrdId"))
        or json.dumps(order, sort_keys=True, default=str)
    )


def _broker_trade_key(trade: dict[str, Any]) -> str:
    return _string(trade.get("tradeNumber")) or json.dumps(trade, sort_keys=True, default=str)


def _local_position_id(
    positions: list[dict[str, Any]],
    symbol: str,
) -> str | None:
    for row in positions:
        if row["fyers_symbol"] == symbol and row["state"] in _OPEN_POSITION_STATES:
            return str(row["id"])
    return None


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
    if not tag.startswith("stvcp-"):
        return None
    try:
        return UUID(tag.removeprefix("stvcp-"))
    except ValueError:
        return None


def _snapshot_intent(intent: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(intent["id"]),
        "status": intent["status"],
        "fyers_async_id": intent.get("fyers_async_id"),
        "fyers_order_id": intent.get("fyers_order_id"),
        "exchange_order_id": intent.get("exchange_order_id"),
        "quantity": int(intent["quantity"]),
        "intent_type": intent["intent_type"],
    }


def _string(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _json(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))
