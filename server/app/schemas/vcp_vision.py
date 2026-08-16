"""On-demand VCP vision validator API schemas (advisory, personal app only)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

VcpVisionAnalysisStatus = Literal[
    "awaiting_capture", "queued", "running", "succeeded", "failed"
]
VcpVisionVerdict = Literal["valid", "invalid", "uncertain"]


class VcpVisionStatusResponse(BaseModel):
    enabled: bool
    model: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)


class VcpVisionCreateResponse(BaseModel):
    analysis_id: uuid.UUID
    status: VcpVisionAnalysisStatus
    reused: bool
    message: str


class VcpVisionChartUploadResponse(BaseModel):
    analysis_id: uuid.UUID
    chart: Literal["context", "detail"]
    status: Literal["awaiting_capture", "queued"]
    message: str


class VcpVisionReviewRequest(BaseModel):
    verdict: VcpVisionVerdict
    note: str = Field(default="", max_length=1000)


class VcpVisionCandleResponse(BaseModel):
    date: datetime.date
    open: float
    high: float
    low: float
    close: float
    volume: int


class VcpVisionFrozenResponse(BaseModel):
    symbol: str | None = None
    as_of_date: datetime.date
    context_sessions: int
    detail_sessions: int
    source_hash: str
    candles: list[VcpVisionCandleResponse] = Field(default_factory=list)


class VcpVisionContractionResponse(BaseModel):
    label: str
    start: datetime.date
    end: datetime.date
    high: float
    low: float
    depth_pct: float
    sessions: int


class VcpVisionResultResponse(BaseModel):
    schema_version: str
    verdict: VcpVisionVerdict
    confidence: int
    summary: str
    prior_uptrend: dict[str, Any] = Field(default_factory=dict)
    volume: dict[str, Any] = Field(default_factory=dict)
    bases: list[dict[str, Any]] = Field(default_factory=list)
    contraction_anchors: list[dict[str, Any]] = Field(default_factory=list)
    pivot_zone: dict[str, Any] | None = None
    supporting_evidence: list[str] = Field(default_factory=list)
    contrary_evidence: list[str] = Field(default_factory=list)
    human_review_focus: list[str] = Field(default_factory=list)
    derived: dict[str, Any] = Field(default_factory=dict)


class VcpVisionAttemptResponse(BaseModel):
    id: uuid.UUID
    attempt_number: int
    status: Literal[
        "started", "succeeded", "invalid_response", "provider_error", "transport_unknown"
    ]
    model: str
    reasoning_effort: str
    prompt_version: str
    input_hash: str
    request_id: str | None = None
    http_status: int | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    cost: float = 0.0
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime.datetime
    completed_at: datetime.datetime | None = None


class VcpVisionHumanReviewResponse(BaseModel):
    verdict: VcpVisionVerdict | None = None
    note: str | None = None
    reviewed_at: datetime.datetime | None = None


class VcpVisionAnalysisResponse(BaseModel):
    id: uuid.UUID
    screening_result_id: uuid.UUID
    status: VcpVisionAnalysisStatus
    chart_source: dict[str, Any] = Field(default_factory=dict)
    renderer_version: str
    model: str | None = None
    reasoning_effort: str
    max_tokens: int
    prompt_version: str
    schema_version: str
    result: VcpVisionResultResponse | None = None
    ai_verdict: VcpVisionVerdict | None = None
    error_code: str | None = None
    error_message: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    cost: float = 0.0
    human_review: VcpVisionHumanReviewResponse | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    attempts: list[VcpVisionAttemptResponse] = Field(default_factory=list)
    frozen: VcpVisionFrozenResponse | None = None
    candles_stale: bool = False


class VcpVisionSummary(BaseModel):
    """Compact latest-analysis summary embedded in ScanResultResponse."""

    id: uuid.UUID
    status: VcpVisionAnalysisStatus
    ai_verdict: VcpVisionVerdict | None = None
    human_verdict: VcpVisionVerdict | None = None
    created_at: datetime.datetime | None = None
    error_code: str | None = None
