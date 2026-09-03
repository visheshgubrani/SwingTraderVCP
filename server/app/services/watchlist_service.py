"""Watchlist domain service (all SQL lives here; routers stay thin).

Owns every read/write over ``watchlists`` / ``watchlist_items``. The REST
surface never places orders or touches trade state. The single-active
semantics mirror the tick worker's subscription query: the worker feeds LTP
for items in *any* watchlist with ``w.is_active = true AND
wi.removed_at IS NULL`` (see ``workers/tick_worker.py``). These helpers keep
the same reading contract and, for writes, maintain the single-active
invariant the UI relies on.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.watchlists import WatchlistItemView, WatchlistView

DEFAULT_WATCHLIST_NAME = "Core"
DEFAULT_WATCHLIST_DESCRIPTION = "Default watchlist"

# Sentinel distinguishing "field absent" from an explicit ``None`` in PATCH.
_UNSET = object()


class WatchlistNotFoundError(LookupError):
    pass


class WatchlistItemNotFoundError(LookupError):
    pass


class InstrumentNotFoundError(LookupError):
    pass


class WatchlistConflictError(RuntimeError):
    pass


class WatchlistValidationError(ValueError):
    pass


WATCHLIST_VIEW_SELECT = """
    SELECT
        w.id,
        w.name,
        w.description,
        w.is_active,
        w.created_at,
        w.updated_at,
        count(wi.id) FILTER (WHERE wi.removed_at IS NULL)::integer AS item_count
    FROM watchlists w
    LEFT JOIN watchlist_items wi ON wi.watchlist_id = w.id
"""

WATCHLIST_ITEM_VIEW_SELECT = """
    SELECT
        wi.id,
        wi.instrument_id,
        i.symbol,
        i.fyers_symbol,
        i.name,
        wi.added_at
    FROM watchlist_items wi
    JOIN instruments i ON i.id = wi.instrument_id
"""


async def _select_all_watchlists(db: AsyncSession) -> list[WatchlistView]:
    result = await db.execute(
        text(
            f"{WATCHLIST_VIEW_SELECT} "
            "GROUP BY w.id "
            "ORDER BY w.created_at ASC, w.name ASC"
        )
    )
    return [WatchlistView.model_validate(dict(row)) for row in result.mappings().all()]


async def _select_watchlist_view(db: AsyncSession, watchlist_id: UUID) -> WatchlistView:
    result = await db.execute(
        text(f"{WATCHLIST_VIEW_SELECT} WHERE w.id = :watchlist_id GROUP BY w.id"),
        {"watchlist_id": watchlist_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise WatchlistNotFoundError("Watchlist not found.")
    return WatchlistView.model_validate(dict(row))


async def _select_item_view(db: AsyncSession, item_id: UUID) -> WatchlistItemView:
    result = await db.execute(
        text(
            f"{WATCHLIST_ITEM_VIEW_SELECT} "
            "WHERE wi.id = :item_id AND wi.removed_at IS NULL"
        ),
        {"item_id": item_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise WatchlistItemNotFoundError("Watchlist item not found.")
    return WatchlistItemView.model_validate(dict(row))


async def _watchlist_exists(db: AsyncSession, watchlist_id: UUID) -> bool:
    result = await db.execute(
        text("SELECT 1 FROM watchlists WHERE id = :watchlist_id"),
        {"watchlist_id": watchlist_id},
    )
    return result.scalar_one_or_none() is not None


async def _resolve_instrument(db: AsyncSession, symbol: str) -> UUID:
    """Resolve ``symbol`` case-insensitively to an active instrument id.

    Prefers an exact ``fyers_symbol`` match, then ``symbol`` with an NSE row
    preferred when ambiguous. Exactly one usable match is required.
    """
    fyers_result = await db.execute(
        text(
            """
            SELECT id
            FROM instruments
            WHERE lower(fyers_symbol) = lower(:symbol) AND active = true
            LIMIT 1
            """
        ),
        {"symbol": symbol},
    )
    fyers_row = fyers_result.mappings().one_or_none()
    if fyers_row is not None:
        return fyers_row["id"]

    symbol_result = await db.execute(
        text(
            """
            SELECT id, exchange
            FROM instruments
            WHERE lower(symbol) = lower(:symbol) AND active = true
            ORDER BY (exchange = 'NSE') DESC, exchange ASC
            """
        ),
        {"symbol": symbol},
    )
    rows = symbol_result.mappings().all()
    if len(rows) == 1:
        return rows[0]["id"]
    nse_rows = [row for row in rows if row["exchange"] == "NSE"]
    if len(nse_rows) == 1:
        return nse_rows[0]["id"]
    raise InstrumentNotFoundError(f"Instrument not found: {symbol}")


async def list_watchlists(
    db: AsyncSession,
    *,
    auto_create_default: bool = True,
) -> list[WatchlistView]:
    """List every watchlist; lazily create the default when none exist at all."""
    watchlists = await _select_all_watchlists(db)
    if not watchlists and auto_create_default:
        try:
            await db.execute(
                text(
                    """
                    INSERT INTO watchlists (name, description, is_active)
                    SELECT :name, :description, true
                    WHERE NOT EXISTS (SELECT 1 FROM watchlists)
                    """
                ),
                {
                    "name": DEFAULT_WATCHLIST_NAME,
                    "description": DEFAULT_WATCHLIST_DESCRIPTION,
                },
            )
        except IntegrityError:
            # A concurrent caller created the default between our read and
            # write (the unique name guard makes the second insert collide).
            await db.rollback()
        watchlists = await _select_all_watchlists(db)
    return watchlists


async def create_watchlist(
    db: AsyncSession,
    *,
    name: str,
    description: str | None,
) -> WatchlistView:
    duplicate = await db.execute(
        text(
            """
            SELECT 1
            FROM watchlists
            WHERE lower(name) = lower(:name)
            LIMIT 1
            """
        ),
        {"name": name},
    )
    if duplicate.scalar_one_or_none() is not None:
        raise WatchlistConflictError(f"A watchlist named '{name}' already exists.")

    active_exists = await db.execute(
        text("SELECT EXISTS (SELECT 1 FROM watchlists WHERE is_active = true)")
    )
    is_active = not bool(active_exists.scalar_one())

    inserted = await db.execute(
        text(
            """
            INSERT INTO watchlists (name, description, is_active)
            VALUES (:name, :description, :is_active)
            RETURNING id, created_at, updated_at
            """
        ),
        {
            "name": name,
            "description": description,
            "is_active": is_active,
        },
    )
    row = inserted.mappings().one()
    return WatchlistView(
        id=row["id"],
        name=name,
        description=description,
        is_active=is_active,
        item_count=0,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def update_watchlist(
    db: AsyncSession,
    watchlist_id: UUID,
    *,
    name: Any = _UNSET,
    description: Any = _UNSET,
    is_active: Any = _UNSET,
) -> WatchlistView:
    """Patch a watchlist, maintaining the single-active invariant."""
    locked = await db.execute(
        text(
            """
            SELECT id, name, description, is_active
            FROM watchlists
            WHERE id = :watchlist_id
            FOR UPDATE
            """
        ),
        {"watchlist_id": watchlist_id},
    )
    row = locked.mappings().one_or_none()
    if row is None:
        raise WatchlistNotFoundError("Watchlist not found.")

    new_name: str = row["name"]
    new_description: str | None = row["description"]
    new_is_active: bool = bool(row["is_active"])

    if name is not _UNSET and name is not None:
        candidate = name.strip()
        if not candidate:
            raise WatchlistValidationError("Watchlist name cannot be empty.")
        if len(candidate) > 80:
            raise WatchlistValidationError(
                "Watchlist name must be 80 characters or fewer."
            )
        duplicate = await db.execute(
            text(
                """
                SELECT 1
                FROM watchlists
                WHERE lower(name) = lower(:name) AND id <> :watchlist_id
                LIMIT 1
                """
            ),
            {"name": candidate, "watchlist_id": watchlist_id},
        )
        if duplicate.scalar_one_or_none() is not None:
            raise WatchlistConflictError(
                f"A watchlist named '{candidate}' already exists."
            )
        new_name = candidate

    if description is not _UNSET:
        new_description = description

    if is_active is not _UNSET and is_active is not None:
        if is_active:
            await db.execute(
                text(
                    """
                    UPDATE watchlists
                    SET is_active = false
                    WHERE is_active = true AND id <> :watchlist_id
                    """
                ),
                {"watchlist_id": watchlist_id},
            )
            new_is_active = True
        else:
            other_active = await db.execute(
                text(
                    """
                    SELECT count(*)
                    FROM watchlists
                    WHERE is_active = true AND id <> :watchlist_id
                    """
                ),
                {"watchlist_id": watchlist_id},
            )
            if other_active.scalar_one() == 0 and new_is_active:
                raise WatchlistValidationError(
                    "An active watchlist is required; deactivate a different "
                    "watchlist before removing the last active one."
                )
            new_is_active = False

    await db.execute(
        text(
            """
            UPDATE watchlists
            SET name = :name, description = :description, is_active = :is_active
            WHERE id = :watchlist_id
            """
        ),
        {
            "name": new_name,
            "description": new_description,
            "is_active": new_is_active,
            "watchlist_id": watchlist_id,
        },
    )
    return await _select_watchlist_view(db, watchlist_id)


async def delete_watchlist(db: AsyncSession, watchlist_id: UUID) -> None:
    """Delete a watchlist; items cascade via the FK. Deleting the last one is
    allowed — the default is lazily recreated by the next list call."""
    result = await db.execute(
        text("DELETE FROM watchlists WHERE id = :watchlist_id"),
        {"watchlist_id": watchlist_id},
    )
    if result.rowcount == 0:
        raise WatchlistNotFoundError("Watchlist not found.")


async def list_watchlist_items(
    db: AsyncSession,
    watchlist_id: UUID,
) -> list[WatchlistItemView]:
    if not await _watchlist_exists(db, watchlist_id):
        raise WatchlistNotFoundError("Watchlist not found.")
    result = await db.execute(
        text(
            f"{WATCHLIST_ITEM_VIEW_SELECT} "
            "WHERE wi.watchlist_id = :watchlist_id AND wi.removed_at IS NULL "
            "ORDER BY wi.added_at ASC"
        ),
        {"watchlist_id": watchlist_id},
    )
    return [
        WatchlistItemView.model_validate(dict(row)) for row in result.mappings().all()
    ]


async def add_watchlist_item(
    db: AsyncSession,
    watchlist_id: UUID,
    *,
    symbol: str,
) -> WatchlistItemView:
    if not await _watchlist_exists(db, watchlist_id):
        raise WatchlistNotFoundError("Watchlist not found.")

    instrument_id = await _resolve_instrument(db, symbol)

    existing_active = await db.execute(
        text(
            """
            SELECT 1
            FROM watchlist_items
            WHERE watchlist_id = :watchlist_id
              AND instrument_id = :instrument_id
              AND removed_at IS NULL
            LIMIT 1
            """
        ),
        {"watchlist_id": watchlist_id, "instrument_id": instrument_id},
    )
    if existing_active.scalar_one_or_none() is not None:
        raise WatchlistConflictError("already in watchlist")

    try:
        removed_row = await db.execute(
            text(
                """
                SELECT id
                FROM watchlist_items
                WHERE watchlist_id = :watchlist_id
                  AND instrument_id = :instrument_id
                  AND removed_at IS NOT NULL
                ORDER BY removed_at DESC
                LIMIT 1
                """
            ),
            {"watchlist_id": watchlist_id, "instrument_id": instrument_id},
        )
        removed = removed_row.mappings().one_or_none()
        if removed is not None:
            # Re-add: resurrect the historical row (fresh added_at, clear any
            # screening linkage) instead of inserting a new one.
            item_result = await db.execute(
                text(
                    """
                    UPDATE watchlist_items
                    SET removed_at = NULL,
                        added_at = now(),
                        screening_result_id = NULL
                    WHERE id = :item_id
                    RETURNING id
                    """
                ),
                {"item_id": removed["id"]},
            )
        else:
            item_result = await db.execute(
                text(
                    """
                    INSERT INTO watchlist_items (watchlist_id, instrument_id)
                    VALUES (:watchlist_id, :instrument_id)
                    RETURNING id
                    """
                ),
                {"watchlist_id": watchlist_id, "instrument_id": instrument_id},
            )
    except IntegrityError as exc:
        # Unique partial index watchlist_items_active_unique_idx — a concurrent
        # add won the race between our check and the write.
        if getattr(getattr(exc, "orig", None), "sqlstate", None) == "23505":
            raise WatchlistConflictError("already in watchlist") from exc
        raise

    item_id = item_result.mappings().one()["id"]
    return await _select_item_view(db, item_id)


async def remove_watchlist_item(
    db: AsyncSession,
    watchlist_id: UUID,
    instrument_id: UUID,
) -> None:
    """Soft-delete an active watchlist item (removed_at = now())."""
    if not await _watchlist_exists(db, watchlist_id):
        raise WatchlistNotFoundError("Watchlist not found.")
    result = await db.execute(
        text(
            """
            UPDATE watchlist_items
            SET removed_at = now()
            WHERE watchlist_id = :watchlist_id
              AND instrument_id = :instrument_id
              AND removed_at IS NULL
            """
        ),
        {"watchlist_id": watchlist_id, "instrument_id": instrument_id},
    )
    if result.rowcount == 0:
        raise WatchlistItemNotFoundError("Watchlist item not found.")


def _escape_like_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def search_instruments(
    db: AsyncSession,
    *,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Case-insensitive substring search over active instruments (symbol/name)."""
    pattern = _escape_like_pattern(query)
    result = await db.execute(
        text(
            """
            SELECT id, symbol, trading_symbol, fyers_symbol, name, exchange, segment
            FROM instruments
            WHERE active = true
              AND (
                  symbol ILIKE :pattern ESCAPE '\\'
                  OR (name IS NOT NULL AND name ILIKE :pattern ESCAPE '\\')
              )
            ORDER BY symbol ASC
            LIMIT :limit
            """
        ),
        {"pattern": pattern, "limit": limit},
    )
    return [dict(row) for row in result.mappings().all()]
