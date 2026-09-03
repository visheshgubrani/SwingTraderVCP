"""Instrument search REST router.

Thin read-only lookup over ``instruments`` for watchlist-building clients.
Never places orders and never touches trade state.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.database import db_dep
from app.schemas.watchlists import InstrumentSearchView
from app.services.watchlist_service import search_instruments

router = APIRouter(prefix="/instruments", tags=["instruments"])


@router.get("/search", response_model=list[InstrumentSearchView])
async def get_instruments(
    db: db_dep,
    q: Annotated[str, Query(min_length=1, description="Search query")],
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
) -> list[InstrumentSearchView]:
    """Case-insensitive substring search over active instruments (symbol/name)."""
    query = q.strip()
    if not query:
        raise HTTPException(
            status_code=422,
            detail="Search query must not be empty.",
        )
    rows = await search_instruments(db, query=query, limit=limit)
    return [InstrumentSearchView.model_validate(row) for row in rows]
