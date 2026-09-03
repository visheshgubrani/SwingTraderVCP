"""Watchlist REST schemas.

Request/response models for the personal watchlist + instrument-search API.
Watchlists are read + watchlist-write only; nothing here touches the money path.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class WatchlistUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = None
    is_active: bool | None = None

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class WatchlistView(BaseModel):
    id: UUID
    name: str
    description: str | None
    is_active: bool
    item_count: int
    created_at: datetime
    updated_at: datetime


class WatchlistItemCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=200)

    @field_validator("symbol", mode="before")
    @classmethod
    def trim_symbol(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class WatchlistItemView(BaseModel):
    id: UUID
    instrument_id: UUID
    symbol: str
    fyers_symbol: str
    name: str | None
    added_at: datetime


class InstrumentSearchView(BaseModel):
    id: UUID
    symbol: str
    trading_symbol: str
    fyers_symbol: str
    name: str | None
    exchange: str
    segment: str
