"""Read-only paper portfolio summary from the paper ledger and journal."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.paper_broker import PaperBrokerError


async def load_paper_portfolio(db: AsyncSession) -> dict[str, Any]:
    account = (
        await db.execute(
            text(
                """
                SELECT starting_cash, cash_available, seeded_from_policy_version,
                       seeded_at, updated_at
                FROM paper_broker_account WHERE id = true
                """
            )
        )
    ).mappings().one_or_none()
    if account is None:
        raise PaperBrokerError(
            "Paper broker account is not seeded; promote P10 to paper first."
        )

    positions = (
        await db.execute(
            text(
                """
                SELECT p.id, i.fyers_symbol AS symbol, p.state, p.open_quantity,
                       p.average_entry_price, p.realized_pnl, p.current_stop_loss
                FROM positions p
                JOIN instruments i ON i.id = p.instrument_id
                WHERE p.execution_mode = 'paper'
                  AND p.state IN ('pending_entry', 'open', 'trailing_active', 'exit_pending')
                ORDER BY i.fyers_symbol
                """
            )
        )
    ).mappings().all()

    invested = Decimal("0")
    unrealized = Decimal("0")
    open_risk = Decimal("0")
    position_payload: list[dict[str, Any]] = []
    for row in positions:
        qty = int(row["open_quantity"] or 0)
        entry = Decimal(row["average_entry_price"] or 0)
        stop = Decimal(row["current_stop_loss"] or 0)
        notional = entry * qty
        invested += notional
        if qty > 0 and entry > 0 and stop > 0:
            open_risk += max(Decimal("0"), (entry - stop) * qty)
        position_payload.append(
            {
                "id": row["id"],
                "symbol": row["symbol"],
                "state": row["state"],
                "open_quantity": qty,
                "average_entry_price": row["average_entry_price"],
                "realized_pnl": row["realized_pnl"],
            }
        )

    closed = (
        await db.execute(
            text(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'closed') AS closed_count,
                    COUNT(*) FILTER (WHERE status = 'closed' AND net_pnl > 0) AS wins,
                    COALESCE(SUM(net_pnl) FILTER (WHERE status = 'closed'), 0) AS realized_pnl,
                    COALESCE(AVG(net_r_multiple) FILTER (WHERE status = 'closed'), 0) AS avg_r
                FROM journal_entries
                WHERE execution_mode = 'paper'
                """
            )
        )
    ).mappings().one()

    closed_count = int(closed["closed_count"] or 0)
    wins = int(closed["wins"] or 0)
    realized = Decimal(closed["realized_pnl"] or 0)
    cash = Decimal(account["cash_available"])
    starting = Decimal(account["starting_cash"])
    equity = cash + invested
    drawdown = max(Decimal("0"), starting - equity)
    return {
        "starting_cash": starting,
        "cash_available": cash,
        "invested_notional": invested,
        "equity": equity,
        "open_risk": open_risk,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "closed_trade_count": closed_count,
        "win_rate": (Decimal(wins) / Decimal(closed_count)) if closed_count else None,
        "average_r_multiple": closed["avg_r"],
        "max_drawdown_from_start": drawdown,
        "seeded_from_policy_version": account["seeded_from_policy_version"],
        "seeded_at": account["seeded_at"],
        "updated_at": account["updated_at"],
        "open_positions": position_payload,
    }
