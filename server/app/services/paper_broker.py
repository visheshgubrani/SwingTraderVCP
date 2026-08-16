"""Durable paper broker: cash ledger, Fyers-shaped books, synthetic fills."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.journal_charges import FillLeg, estimate_cnc_charges
from app.services.fyers_broker_reads import (
    BrokerBooks,
    BrokerPreflightSnapshot,
    normalize_holding,
    normalize_order,
    normalize_position,
    normalize_trade,
)

REDIS_PAPER_ORDER_CHANNEL = "paper_order_events"


class PaperBrokerError(RuntimeError):
    """Paper broker ledger is missing or inconsistent."""


@dataclass(frozen=True)
class PaperPlaceResult:
    fyers_async_id: str
    fyers_order_id: str
    trade_number: str
    payload: dict[str, Any]
    order_message: dict[str, Any]
    trade_message: dict[str, Any]


def _order_tag(intent_id: UUID) -> str:
    import base64

    encoded = base64.urlsafe_b64encode(intent_id.bytes).decode().rstrip("=")
    return f"stv-{encoded}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


async def seed_paper_account(
    db: AsyncSession,
    *,
    starting_cash: Decimal | None = None,
    policy_version: int | None = None,
) -> dict[str, Any]:
    """Insert the single paper account if missing. Never silently top up cash."""
    cash = starting_cash if starting_cash is not None else settings.paper_initial_capital
    if cash <= 0:
        raise PaperBrokerError("Paper starting cash must be positive.")
    existing = (
        await db.execute(
            text(
                """
                SELECT starting_cash, cash_available, seeded_from_policy_version
                FROM paper_broker_account WHERE id = true
                """
            )
        )
    ).mappings().one_or_none()
    if existing is not None:
        return dict(existing)
    await db.execute(
        text(
            """
            INSERT INTO paper_broker_account (
                id, starting_cash, cash_available, seeded_from_policy_version
            ) VALUES (true, :cash, :cash, :policy_version)
            """
        ),
        {"cash": cash, "policy_version": policy_version},
    )
    return {
        "starting_cash": cash,
        "cash_available": cash,
        "seeded_from_policy_version": policy_version,
    }


async def reset_paper_account(
    db: AsyncSession,
    *,
    starting_cash: Decimal | None = None,
    policy_version: int | None = None,
) -> dict[str, Any]:
    """Re-seed cash only when no nonterminal paper positions or intents exist."""
    open_positions = (
        await db.execute(
            text(
                """
                SELECT COUNT(*) FROM positions
                WHERE execution_mode = 'paper'
                  AND state NOT IN ('closed', 'cancelled')
                """
            )
        )
    ).scalar_one()
    open_intents = (
        await db.execute(
            text(
                """
                SELECT COUNT(*) FROM order_intents
                WHERE execution_mode = 'paper'
                  AND status NOT IN ('filled', 'rejected', 'cancelled')
                """
            )
        )
    ).scalar_one()
    if int(open_positions) > 0 or int(open_intents) > 0:
        raise PaperBrokerError(
            "Paper account reset is blocked while paper positions or intents remain open."
        )
    cash = starting_cash if starting_cash is not None else settings.paper_initial_capital
    await db.execute(text("DELETE FROM paper_broker_trades"))
    await db.execute(text("DELETE FROM paper_broker_orders"))
    await db.execute(text("DELETE FROM paper_broker_positions"))
    await db.execute(
        text(
            """
            INSERT INTO paper_broker_account (
                id, starting_cash, cash_available, seeded_from_policy_version, seeded_at
            ) VALUES (true, :cash, :cash, :policy_version, now())
            ON CONFLICT (id) DO UPDATE SET
                starting_cash = EXCLUDED.starting_cash,
                cash_available = EXCLUDED.cash_available,
                seeded_from_policy_version = EXCLUDED.seeded_from_policy_version,
                seeded_at = now(),
                updated_at = now()
            """
        ),
        {"cash": cash, "policy_version": policy_version},
    )
    return {
        "starting_cash": cash,
        "cash_available": cash,
        "seeded_from_policy_version": policy_version,
    }


async def _lock_account(db: AsyncSession) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                """
                SELECT starting_cash, cash_available, seeded_from_policy_version
                FROM paper_broker_account
                WHERE id = true
                FOR UPDATE
                """
            )
        )
    ).mappings().one_or_none()
    if row is None:
        raise PaperBrokerError(
            "Paper broker account is not seeded; promote P10 to paper first."
        )
    return dict(row)


async def fetch_preflight(db: AsyncSession) -> BrokerPreflightSnapshot:
    """Build a Fyers-shaped snapshot from the paper ledger. Never calls Fyers."""
    account = (
        await db.execute(
            text(
                """
                SELECT cash_available FROM paper_broker_account WHERE id = true
                """
            )
        )
    ).mappings().one_or_none()
    if account is None:
        raise PaperBrokerError(
            "Paper broker account is not seeded; promote P10 to paper first."
        )
    orders = (
        await db.execute(
            text(
                """
                SELECT fyers_async_id, fyers_order_id, symbol, side, quantity,
                       filled_quantity, product_type, status, traded_price, order_tag
                FROM paper_broker_orders
                ORDER BY created_at
                """
            )
        )
    ).mappings().all()
    trades = (
        await db.execute(
            text(
                """
                SELECT t.trade_number, t.symbol, t.side, t.quantity, t.price,
                       t.product_type, o.fyers_async_id, o.fyers_order_id, o.order_tag
                FROM paper_broker_trades t
                JOIN paper_broker_orders o ON o.id = t.paper_order_id
                ORDER BY t.filled_at
                """
            )
        )
    ).mappings().all()
    positions = (
        await db.execute(
            text(
                """
                SELECT symbol, net_qty, avg_price, product_type
                FROM paper_broker_positions
                WHERE net_qty <> 0
                """
            )
        )
    ).mappings().all()
    order_rows = [
        normalize_order(
            {
                "id": row["fyers_order_id"],
                "id_fyers": row["fyers_async_id"],
                "exchOrdId": row["fyers_order_id"],
                "symbol": row["symbol"],
                "side": 1 if row["side"] == "buy" else -1,
                "qty": int(row["quantity"]),
                "filledQty": int(row["filled_quantity"]),
                "productType": row["product_type"],
                "status": "2" if row["status"] == "filled" else "6",
                "tradedPrice": row["traded_price"],
                "orderTag": row["order_tag"],
            }
        )
        for row in orders
    ]
    trade_rows = [
        normalize_trade(
            {
                "tradeNumber": row["trade_number"],
                "symbol": row["symbol"],
                "side": 1 if row["side"] == "buy" else -1,
                "tradedQty": int(row["quantity"]),
                "tradePrice": row["price"],
                "productType": row["product_type"],
                "id_fyers": row["fyers_async_id"],
                "orderNumber": row["fyers_order_id"],
                "orderTag": row["order_tag"],
            }
        )
        for row in trades
    ]
    position_rows = [
        normalize_position(
            {
                "symbol": row["symbol"],
                "netQty": int(row["net_qty"]),
                "avgPrice": row["avg_price"],
                "productType": row["product_type"],
                "ltp": row["avg_price"],
            }
        )
        for row in positions
    ]
    holding_rows = [
        normalize_holding(
            {
                "symbol": row["symbol"],
                "remainingQuantity": int(row["net_qty"]),
                "productType": row["product_type"],
            }
        )
        for row in positions
        if int(row["net_qty"]) > 0
    ]
    return BrokerPreflightSnapshot(
        books=BrokerBooks(
            orders=order_rows,
            trades=trade_rows,
            positions=position_rows,
            holdings=holding_rows,
        ),
        available_funds=Decimal(account["cash_available"]),
        fetched_at=_now(),
    )


async def place_paper_order(
    db: AsyncSession,
    *,
    snapshot: dict[str, Any],
    fill_price: Decimal,
) -> PaperPlaceResult:
    """Accept one paper order, fill it at LTP, and mutate cash/books."""
    if fill_price <= 0:
        raise PaperBrokerError("Paper fill price must be positive.")
    account = await _lock_account(db)
    quantity = int(snapshot["quantity"])
    side = str(snapshot["side"])
    symbol = str(snapshot["symbol"])
    intent_id = snapshot["id"]
    existing = (
        await db.execute(
            text(
                """
                SELECT fyers_async_id, fyers_order_id, id AS paper_order_id
                FROM paper_broker_orders
                WHERE order_intent_id = :intent_id
                """
            ),
            {"intent_id": intent_id},
        )
    ).mappings().one_or_none()
    if existing is not None:
        trade = (
            await db.execute(
                text(
                    """
                    SELECT trade_number FROM paper_broker_trades
                    WHERE paper_order_id = :paper_order_id
                    """
                ),
                {"paper_order_id": existing["paper_order_id"]},
            )
        ).mappings().one()
        return build_paper_fill_messages(
            snapshot=snapshot,
            fyers_async_id=existing["fyers_async_id"],
            fyers_order_id=existing["fyers_order_id"],
            trade_number=trade["trade_number"],
            fill_price=fill_price,
        )

    if side not in ("buy", "sell"):
        raise PaperBrokerError(f"Unsupported paper order side {side}.")
    notional = Decimal(quantity) * fill_price
    charges = estimate_cnc_charges(
        [FillLeg(side=side, quantity=quantity, price=fill_price)]
    ).total
    cash = Decimal(account["cash_available"])
    if side == "buy":
        debit = notional + charges
        if cash < debit:
            raise PaperBrokerError("Paper broker rejected the order: insufficient cash.")
        new_cash = cash - debit
    else:
        new_cash = cash + notional - charges

    fyers_async_id = f"paper-async:{intent_id}"
    fyers_order_id = f"paper-ord:{intent_id}"
    trade_number = f"paper-trd:{intent_id}"
    order_tag = _order_tag(intent_id)
    paper_order_id = uuid4()
    await db.execute(
        text(
            """
            INSERT INTO paper_broker_orders (
                id, order_intent_id, fyers_async_id, fyers_order_id, symbol, side,
                quantity, filled_quantity, product_type, status, traded_price, order_tag
            ) VALUES (
                :id, :intent_id, :async_id, :order_id, :symbol, :side,
                :quantity, :quantity, 'CNC', 'filled', :price, :order_tag
            )
            """
        ),
        {
            "id": paper_order_id,
            "intent_id": intent_id,
            "async_id": fyers_async_id,
            "order_id": fyers_order_id,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": fill_price,
            "order_tag": order_tag,
        },
    )
    await db.execute(
        text(
            """
            INSERT INTO paper_broker_trades (
                paper_order_id, order_intent_id, trade_number, symbol, side,
                quantity, price, product_type
            ) VALUES (
                :paper_order_id, :intent_id, :trade_number, :symbol, :side,
                :quantity, :price, 'CNC'
            )
            """
        ),
        {
            "paper_order_id": paper_order_id,
            "intent_id": intent_id,
            "trade_number": trade_number,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": fill_price,
        },
    )
    await _apply_position_fill(
        db,
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=fill_price,
    )
    await db.execute(
        text(
            """
            UPDATE paper_broker_account
            SET cash_available = :cash, updated_at = now()
            WHERE id = true
            """
        ),
        {"cash": _money(new_cash)},
    )
    return build_paper_fill_messages(
        snapshot=snapshot,
        fyers_async_id=fyers_async_id,
        fyers_order_id=fyers_order_id,
        trade_number=trade_number,
        fill_price=fill_price,
    )


def build_paper_fill_messages(
    *,
    snapshot: dict[str, Any],
    fyers_async_id: str,
    fyers_order_id: str,
    trade_number: str,
    fill_price: Decimal,
) -> PaperPlaceResult:
    quantity = int(snapshot["quantity"])
    symbol = str(snapshot["symbol"])
    order_tag = _order_tag(snapshot["id"])
    filled_at = _now().strftime("%d-%b-%Y %H:%M:%S")
    order_payload = {
        "id_fyers": fyers_async_id,
        "id": fyers_order_id,
        "exchOrdId": fyers_order_id,
        "symbol": symbol,
        "qty": quantity,
        "filledQty": quantity,
        "status": 2,
        "tradedPrice": float(fill_price),
        "productType": "CNC",
        "orderTag": order_tag,
        "orderDateTime": filled_at,
        "message": "paper fill",
    }
    trade_payload = {
        "id_fyers": fyers_async_id,
        "orderNumber": fyers_order_id,
        "exchangeOrderNo": fyers_order_id,
        "tradeNumber": trade_number,
        "symbol": symbol,
        "tradedQty": quantity,
        "tradePrice": float(fill_price),
        "productType": "CNC",
        "orderTag": order_tag,
        "orderDateTime": filled_at,
    }
    return PaperPlaceResult(
        fyers_async_id=fyers_async_id,
        fyers_order_id=fyers_order_id,
        trade_number=trade_number,
        payload={"s": "ok", "id": fyers_async_id, "source": "paper_broker"},
        order_message={"orders": order_payload},
        trade_message={"trades": trade_payload},
    )


async def _apply_position_fill(
    db: AsyncSession,
    *,
    symbol: str,
    side: str,
    quantity: int,
    price: Decimal,
) -> None:
    row = (
        await db.execute(
            text(
                """
                SELECT net_qty, avg_price FROM paper_broker_positions
                WHERE symbol = :symbol FOR UPDATE
                """
            ),
            {"symbol": symbol},
        )
    ).mappings().one_or_none()
    signed = quantity if side == "buy" else -quantity
    if row is None:
        if signed < 0:
            raise PaperBrokerError(f"Paper short of {symbol} is not allowed.")
        await db.execute(
            text(
                """
                INSERT INTO paper_broker_positions (symbol, net_qty, avg_price)
                VALUES (:symbol, :qty, :price)
                """
            ),
            {"symbol": symbol, "qty": signed, "price": price},
        )
        return
    current_qty = int(row["net_qty"])
    avg = Decimal(row["avg_price"])
    new_qty = current_qty + signed
    if new_qty < 0:
        raise PaperBrokerError(f"Paper exit exceeds holdings for {symbol}.")
    if signed > 0 and new_qty > 0:
        avg = ((avg * current_qty) + (price * quantity)) / Decimal(new_qty)
    if new_qty == 0:
        await db.execute(
            text("DELETE FROM paper_broker_positions WHERE symbol = :symbol"),
            {"symbol": symbol},
        )
        return
    await db.execute(
        text(
            """
            UPDATE paper_broker_positions
            SET net_qty = :qty, avg_price = :price, updated_at = now()
            WHERE symbol = :symbol
            """
        ),
        {"symbol": symbol, "qty": new_qty, "price": avg},
    )


async def publish_paper_fill_events(redis, result: PaperPlaceResult) -> None:
    await redis.publish(
        REDIS_PAPER_ORDER_CHANNEL,
        json.dumps({"kind": "order", "message": result.order_message}),
    )
    await redis.publish(
        REDIS_PAPER_ORDER_CHANNEL,
        json.dumps({"kind": "trade", "message": result.trade_message}),
    )


async def release_unaccepted_paper_claims(db: AsyncSession) -> int:
    """Release paper claims that never reached the paper broker, so submit can retry."""
    result = await db.execute(
        text(
            """
            UPDATE order_intents oi
            SET
                status = 'created',
                broker_requested_at = NULL,
                reason = 'Paper claim released after restart; paper broker never accepted.'
            WHERE oi.execution_mode = 'paper'
              AND oi.status = 'submission_pending'
              AND NOT EXISTS (
                  SELECT 1 FROM paper_broker_orders pbo
                  WHERE pbo.order_intent_id = oi.id
              )
            RETURNING oi.id
            """
        )
    )
    return len(result.mappings().all())


async def load_unfilled_submitted_paper_intents(
    db: AsyncSession,
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                """
                SELECT oi.id, oi.quantity, oi.side, i.fyers_symbol AS symbol,
                       pbo.fyers_async_id, pbo.fyers_order_id, pbo.traded_price,
                       pbt.trade_number
                FROM order_intents oi
                JOIN positions p ON p.id = oi.position_id
                JOIN instruments i ON i.id = p.instrument_id
                JOIN paper_broker_orders pbo ON pbo.order_intent_id = oi.id
                JOIN paper_broker_trades pbt ON pbt.paper_order_id = pbo.id
                WHERE oi.execution_mode = 'paper'
                  AND oi.status IN ('submission_pending', 'submitted', 'acknowledged')
                  AND NOT EXISTS (
                      SELECT 1 FROM order_fills f WHERE f.order_intent_id = oi.id
                  )
                """
            )
        )
    ).mappings().all()
    return [dict(row) for row in rows]
