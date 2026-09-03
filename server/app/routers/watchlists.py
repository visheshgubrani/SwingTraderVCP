"""Watchlist REST router.

Thin HTTP layer over :mod:`app.services.watchlist_service`. Read + watchlist
writes only — never places orders and never touches trade state. Registered
under ``/api/v1`` in ``main.py`` with the session-authentication dependency.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from app.database import db_dep
from app.schemas.watchlists import (
    WatchlistCreate,
    WatchlistItemCreate,
    WatchlistItemView,
    WatchlistUpdate,
    WatchlistView,
)
from app.services import watchlist_service as service

router = APIRouter(prefix="/watchlists", tags=["watchlists"])


def _raise_watchlist_http_error(exc: Exception) -> None:
    if isinstance(
        exc,
        (
            service.WatchlistNotFoundError,
            service.WatchlistItemNotFoundError,
            service.InstrumentNotFoundError,
        ),
    ):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, service.WatchlistConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, service.WatchlistValidationError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


@router.get("", response_model=list[WatchlistView])
async def get_watchlists(db: db_dep) -> list[WatchlistView]:
    """List watchlists, lazily creating the default watchlist on first use."""
    try:
        watchlists = await service.list_watchlists(db, auto_create_default=True)
        await db.commit()
        return watchlists
    except Exception as exc:
        await db.rollback()
        _raise_watchlist_http_error(exc)
        raise


@router.post(
    "",
    response_model=WatchlistView,
    status_code=status.HTTP_201_CREATED,
)
async def create_watchlist(
    payload: WatchlistCreate,
    db: db_dep,
) -> WatchlistView:
    try:
        watchlist = await service.create_watchlist(
            db,
            name=payload.name,
            description=payload.description,
        )
        await db.commit()
        return watchlist
    except Exception as exc:
        await db.rollback()
        _raise_watchlist_http_error(exc)
        raise


@router.patch("/{watchlist_id}", response_model=WatchlistView)
async def patch_watchlist(
    watchlist_id: UUID,
    payload: WatchlistUpdate,
    db: db_dep,
) -> WatchlistView:
    kwargs: dict[str, Any] = {}
    if "name" in payload.model_fields_set:
        kwargs["name"] = payload.name
    if "description" in payload.model_fields_set:
        kwargs["description"] = payload.description
    if "is_active" in payload.model_fields_set:
        kwargs["is_active"] = payload.is_active
    try:
        watchlist = await service.update_watchlist(db, watchlist_id, **kwargs)
        await db.commit()
        return watchlist
    except Exception as exc:
        await db.rollback()
        _raise_watchlist_http_error(exc)
        raise


@router.delete("/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist(watchlist_id: UUID, db: db_dep) -> Response:
    try:
        await service.delete_watchlist(db, watchlist_id)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        _raise_watchlist_http_error(exc)
        raise
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{watchlist_id}/items", response_model=list[WatchlistItemView])
async def get_watchlist_items(
    watchlist_id: UUID,
    db: db_dep,
) -> list[WatchlistItemView]:
    try:
        return await service.list_watchlist_items(db, watchlist_id)
    except Exception as exc:
        _raise_watchlist_http_error(exc)
        raise


@router.post(
    "/{watchlist_id}/items",
    response_model=WatchlistItemView,
    status_code=status.HTTP_201_CREATED,
)
async def add_watchlist_item(
    watchlist_id: UUID,
    payload: WatchlistItemCreate,
    db: db_dep,
) -> WatchlistItemView:
    try:
        item = await service.add_watchlist_item(
            db,
            watchlist_id,
            symbol=payload.symbol,
        )
        await db.commit()
        return item
    except Exception as exc:
        await db.rollback()
        _raise_watchlist_http_error(exc)
        raise


@router.delete(
    "/{watchlist_id}/items/{instrument_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_watchlist_item(
    watchlist_id: UUID,
    instrument_id: UUID,
    db: db_dep,
) -> Response:
    try:
        await service.remove_watchlist_item(db, watchlist_id, instrument_id)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        _raise_watchlist_http_error(exc)
        raise
    return Response(status_code=status.HTTP_204_NO_CONTENT)
