"""Insert durable journal outbox events alongside order fills."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def trigger_journal_dispatcher(redis) -> None:
    """Best-effort enqueue of pending journal outbox processing."""
    try:
        await redis.enqueue_job("run_journal_dispatcher")
    except Exception:
        pass


async def enqueue_journal_fill_event(
    db: AsyncSession,
    *,
    order_fill_id: UUID,
    position_id: UUID,
    fill_side: str,
) -> bool:
    """
    Enqueue a journal fill event in the same transaction as the fill insert.

    Returns False when the event already exists (idempotent replay).
    """
    result = await db.execute(
        text(
            """
            INSERT INTO journal_fill_outbox (
                id,
                order_fill_id,
                position_id,
                fill_side,
                status
            )
            VALUES (
                :id,
                :order_fill_id,
                :position_id,
                :fill_side,
                'pending'
            )
            ON CONFLICT (order_fill_id) DO NOTHING
            RETURNING id
            """
        ),
        {
            "id": uuid4(),
            "order_fill_id": order_fill_id,
            "position_id": position_id,
            "fill_side": fill_side,
        },
    )
    return result.mappings().one_or_none() is not None
