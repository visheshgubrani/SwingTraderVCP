"""Entry-leg lifecycle helpers shared by the proposal worker, API and supervisor.

These only move `entry_legs` between non-money states (supersede, orphan
cleanup); they never create intents or talk to a broker.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def supersede_older_initial_legs(
    session: AsyncSession,
    *,
    symbol: str,
    proposal_id: Any,
) -> int:
    """Expire older, still-waiting initial legs for the same symbol.

    Multi-session entry windows let consecutive daily proposals for one name
    overlap; the newest immutable geometry wins. Only legs that have not begun
    an allocation (armed / waiting_for_reset with no intent) are superseded —
    a leg mid-allocation is left to finish, and the same-symbol allocation
    guard prevents a second position either way.
    """
    rows = (
        await session.execute(
            text(
                """
                UPDATE entry_legs el
                SET status = 'expired', signal_bar_timestamp = NULL
                FROM trade_proposals tp
                WHERE el.proposal_id = tp.id
                  AND tp.symbol = :symbol
                  AND tp.id <> :proposal_id
                  AND el.leg_index = 1
                  AND el.status IN ('armed', 'waiting_for_reset')
                  AND el.order_intent_id IS NULL
                RETURNING el.id, el.proposal_id
                """
            ),
            {"symbol": symbol, "proposal_id": proposal_id},
        )
    ).mappings().all()
    for row in rows:
        await session.execute(
            text(
                """
                UPDATE entry_legs
                SET status = 'cancelled'
                WHERE proposal_id = :proposal_id
                  AND leg_index > 1
                  AND status = 'planned'
                """
            ),
            {"proposal_id": row["proposal_id"]},
        )
        logger.info(
            "Superseded initial leg %s of proposal %s for %s by proposal %s",
            row["id"],
            row["proposal_id"],
            symbol,
            proposal_id,
        )
    return len(rows)


async def cancel_orphan_planned_legs(session: AsyncSession) -> int:
    """Cancel `planned` add legs that can never arm.

    A planned leg arms only after its preceding leg fills on an approved
    proposal with an open position. It is unreachable when the proposal is
    not approved (rejected / expired_unapproved), when the preceding leg ended
    without a fill (expired / cancelled), or when the position it would add to
    is closed or cancelled.
    """
    rows = (
        await session.execute(
            text(
                """
                UPDATE entry_legs el
                SET status = 'cancelled'
                FROM trade_proposals tp
                WHERE el.proposal_id = tp.id
                  AND el.status = 'planned'
                  AND el.leg_index > 1
                  AND (
                        tp.status <> 'approved'
                     OR EXISTS (
                            SELECT 1
                            FROM entry_legs prev
                            WHERE prev.proposal_id = el.proposal_id
                              AND prev.leg_index = el.leg_index - 1
                              AND prev.status IN ('expired', 'cancelled')
                              AND prev.filled_shares = 0
                        )
                     OR EXISTS (
                            SELECT 1
                            FROM entry_legs first_leg
                            JOIN positions p ON p.id = first_leg.position_id
                            WHERE first_leg.proposal_id = el.proposal_id
                              AND first_leg.leg_index = 1
                              AND p.state IN ('closed', 'cancelled')
                        )
                  )
                RETURNING el.id
                """
            )
        )
    ).mappings().all()
    if rows:
        logger.info("Cancelled %d orphan planned entry legs", len(rows))
    return len(rows)
