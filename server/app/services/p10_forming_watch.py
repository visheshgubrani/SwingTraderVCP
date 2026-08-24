"""P10 forming-pattern watch persistence. No prices, no LTP watchlist writes."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.proposal_generator import calculate_next_session_and_deadline


FORMING_EXPIRY_SESSIONS = 10
FORMING_RECHECK_CAP = 10


def completed_nse_sessions_since(
    first_seen: dt.date,
    as_of: dt.date,
    *,
    holidays: set[dt.date],
) -> int:
    if as_of <= first_seen:
        return 0
    count = 0
    cursor = first_seen
    while cursor < as_of:
        cursor, _ = calculate_next_session_and_deadline(cursor, holidays=holidays)
        count += 1
        if count > 400:
            break
    return count


async def instrument_in_nifty500(session: AsyncSession, instrument_id: str) -> bool:
    result = await session.execute(
        text(
            """
            SELECT 1
            FROM universe_memberships
            WHERE instrument_id = :instrument_id
              AND universe_code = 'NIFTY500'
              AND member_to IS NULL
            LIMIT 1
            """
        ),
        {"instrument_id": instrument_id},
    )
    return result.scalar_one_or_none() is not None


async def upsert_forming_watch(
    session: AsyncSession,
    *,
    instrument_id: str,
    screening_result_id: str,
    symbol: str,
    as_of_date: dt.date,
    forming_state: str,
    llm_snapshot: dict[str, Any],
    python_candidates: list[dict[str, Any]],
    attempt_id: str | None,
    holidays: set[dt.date],
) -> None:
    next_check, _ = calculate_next_session_and_deadline(as_of_date, holidays=holidays)
    await session.execute(
        text(
            """
            INSERT INTO p10_forming_patterns (
                instrument_id, screening_result_id, symbol,
                first_seen_as_of, last_as_of, forming_state, status,
                next_check_date, llm_snapshot, python_candidates, last_attempt_id
            ) VALUES (
                :instrument_id, :screening_result_id, :symbol,
                :as_of_date, :as_of_date, :forming_state, 'watching',
                :next_check_date, CAST(:llm_snapshot AS jsonb),
                CAST(:python_candidates AS jsonb), :last_attempt_id
            )
            ON CONFLICT (instrument_id) WHERE status = 'watching'
            DO UPDATE SET
                screening_result_id = EXCLUDED.screening_result_id,
                last_as_of = EXCLUDED.last_as_of,
                forming_state = EXCLUDED.forming_state,
                next_check_date = EXCLUDED.next_check_date,
                llm_snapshot = EXCLUDED.llm_snapshot,
                python_candidates = EXCLUDED.python_candidates,
                last_attempt_id = EXCLUDED.last_attempt_id,
                updated_at = now()
            """
        ),
        {
            "instrument_id": instrument_id,
            "screening_result_id": screening_result_id,
            "symbol": symbol,
            "as_of_date": as_of_date,
            "forming_state": forming_state,
            "next_check_date": next_check,
            "llm_snapshot": json.dumps(llm_snapshot),
            "python_candidates": json.dumps(python_candidates),
            "last_attempt_id": attempt_id,
        },
    )


async def close_forming_watch(
    session: AsyncSession,
    *,
    instrument_id: str,
    status: str,
    proposal_id: str | None = None,
) -> None:
    await session.execute(
        text(
            """
            UPDATE p10_forming_patterns
            SET status = :status,
                promoted_proposal_id = COALESCE(:proposal_id, promoted_proposal_id),
                updated_at = now()
            WHERE instrument_id = :instrument_id AND status = 'watching'
            """
        ),
        {
            "instrument_id": instrument_id,
            "status": status,
            "proposal_id": proposal_id,
        },
    )


async def expire_stale_forming_watches(
    session: AsyncSession,
    *,
    as_of_date: dt.date,
    holidays: set[dt.date],
) -> int:
    rows = (
        await session.execute(
            text(
                """
                SELECT id, instrument_id, first_seen_as_of
                FROM p10_forming_patterns
                WHERE status = 'watching'
                """
            )
        )
    ).mappings().all()
    expired = 0
    for row in rows:
        in_universe = await instrument_in_nifty500(session, str(row["instrument_id"]))
        sessions = completed_nse_sessions_since(
            row["first_seen_as_of"], as_of_date, holidays=holidays
        )
        if (not in_universe) or sessions >= FORMING_EXPIRY_SESSIONS:
            await session.execute(
                text(
                    """
                    UPDATE p10_forming_patterns
                    SET status = 'expired', updated_at = now()
                    WHERE id = :id AND status = 'watching'
                    """
                ),
                {"id": row["id"]},
            )
            expired += 1
    return expired


async def load_forming_rechecks(
    session: AsyncSession,
    *,
    as_of_date: dt.date,
    cap: int = FORMING_RECHECK_CAP,
) -> list[Any]:
    result = await session.execute(
        text(
            """
            SELECT f.screening_result_id, f.instrument_id, f.symbol,
                   i.tick_size, i.lot_size,
                   sr.result_rank, sr.technical_score
            FROM p10_forming_patterns f
            JOIN instruments i ON i.id = f.instrument_id
            LEFT JOIN screening_results sr ON sr.id = f.screening_result_id
            WHERE f.status = 'watching'
              AND f.next_check_date <= :as_of_date
            ORDER BY f.next_check_date ASC, f.created_at ASC
            LIMIT :cap
            """
        ),
        {"as_of_date": as_of_date, "cap": cap},
    )
    return list(result.all())
