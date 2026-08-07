from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class JournalListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    position_id: UUID
    symbol: str
    execution_mode: Literal["paper", "live"]
    status: Literal["open", "closed"]
    first_entry_fill_at: datetime | None = None
    closed_at: datetime | None = None
    weighted_entry_price: Decimal | None = None
    weighted_exit_price: Decimal | None = None
    gross_pnl: Decimal | None = None
    net_pnl: Decimal | None = None
    gross_r_multiple: Decimal | None = None
    net_r_multiple: Decimal | None = None
    hold_duration_hours: Decimal | None = None
    exit_outcome: str | None = None
    setup_tags: list[str] = Field(default_factory=list)
    execution_rating: int | None = None
    charge_quality: Literal["estimated", "reconciled"] = "estimated"
    pnl_mismatch: bool = False
    regime: str | None = None


class JournalListResponse(BaseModel):
    items: list[JournalListItem]
    total: int
    offset: int
    limit: int


class JournalDetailView(JournalListItem):
    entry_snapshot: dict[str, Any] = Field(default_factory=dict)
    exit_fills: list[dict[str, Any]] = Field(default_factory=list)
    exit_reasons: list[str] = Field(default_factory=list)
    estimated_charges: dict[str, Any] = Field(default_factory=dict)
    actual_charges: dict[str, Any] | None = None
    risk_amount: Decimal | None = None
    pnl_mismatch_delta: Decimal | None = None
    notes: str | None = None
    mistake_tags: list[str] = Field(default_factory=list)
    emotion_tags: list[str] = Field(default_factory=list)
    lessons: str | None = None
    first_entry_price: Decimal | None = None
    first_entry_quantity: int | None = None
    final_entry_quantity: int | None = None
    entry_frozen_at: datetime | None = None
    market_regime_snapshot_id: UUID | None = None
    reference_eod_date: str | None = None
    regime_evidence: dict[str, Any] = Field(default_factory=dict)
    artifact_status: str | None = None
    artifact_content_hash: str | None = None


class JournalReviewUpdate(BaseModel):
    notes: str | None = None
    execution_rating: int | None = Field(default=None, ge=1, le=5)
    setup_tags: list[str] | None = None
    mistake_tags: list[str] | None = None
    emotion_tags: list[str] | None = None
    lessons: str | None = None


class ActualChargesUpdate(BaseModel):
    version: str
    label: Literal["reconciled"] = "reconciled"
    brokerage: Decimal = Decimal("0")
    stt: Decimal = Decimal("0")
    exchange_charges: Decimal = Decimal("0")
    sebi_charges: Decimal = Decimal("0")
    stamp_duty: Decimal = Decimal("0")
    gst: Decimal = Decimal("0")
    dp_charges: Decimal = Decimal("0")
    total: Decimal
    per_fill: list[dict[str, Any]] = Field(default_factory=list)


class PeriodSummaryRequest(BaseModel):
    bucket: Literal["day", "week", "month", "year"] = "month"
    execution_mode: Literal["paper", "live"] | None = None
    symbol: str | None = None
    setup_tag: str | None = None
    regime: str | None = None
    exit_outcome: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


class ChartArtifactClaimView(BaseModel):
    id: UUID
    journal_entry_id: UUID
    chart_source: dict[str, Any]
    capture_attempts: int


class AiCoachFilters(BaseModel):
    execution_mode: Literal["paper", "live"] | None = None
    symbol: str | None = None
    setup_tag: str | None = None
    regime: str | None = None
    exit_outcome: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    limit: int = Field(default=500, ge=1, le=1000)


class AiCoachRunCreate(BaseModel):
    filters: AiCoachFilters = Field(default_factory=AiCoachFilters)


class AiCoachRunView(BaseModel):
    id: UUID
    status: str
    filters: dict[str, Any] = Field(default_factory=dict)
    input_hash: str
    result: dict[str, Any] | None = None
    model: str
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
