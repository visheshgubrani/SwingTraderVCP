"""Public screening and fundamentals response schemas."""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class FundamentalErrorResponse(BaseModel):
    type: str | None = None
    message: str | None = None


class FundamentalModelResponse(BaseModel):
    provider: str | None = None
    name: str | None = None
    prompt_version: str | None = None
    request_id: str | None = None
    input_hash: str | None = None
    usage: dict[str, Any] | None = None
    reasoning_excluded: bool = True


class FundamentalMetricResponse(BaseModel):
    key: str
    label: str
    value: float | None = None
    unit: str | None = None
    weight: float
    points: float
    available: bool
    status: Literal["positive", "negative", "mixed", "unknown", "not_applicable"]
    evidence_keys: list[str] = Field(default_factory=list)
    unavailable_reason: str | None = None


class FundamentalComponentResponse(BaseModel):
    name: str
    earned_points: float
    available_points: float
    max_points: float
    metrics: list[FundamentalMetricResponse] = Field(default_factory=list)


class FundamentalAssessmentResponse(BaseModel):
    rubric_version: str
    score: float | None = None
    grade: Literal["A", "B", "C", "D", "insufficient"]
    coverage_pct: float
    earned_points: float = 0
    available_points: float = 0
    max_points: float = 100
    components: list[FundamentalComponentResponse] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    provider_limitations: list[str] = Field(default_factory=list)
    insufficient_reason: str | None = None


class FundamentalInstrumentResponse(BaseModel):
    symbol: str
    name: str | None = None
    fyers_symbol: str


class FundamentalSnapshotResponse(BaseModel):
    id: uuid.UUID
    provider: str
    statement_type: Literal["consolidated", "standalone"]
    fetched_at: datetime.datetime
    latest_annual_period: str | None = None
    latest_quarterly_period: str | None = None
    normalized_facts: dict[str, Any]


class FundamentalSourceResponse(BaseModel):
    status: Literal[
        "not_requested", "queued", "running", "completed", "failed", "skipped"
    ]
    assessment: FundamentalAssessmentResponse | None = None
    scorecard: dict[str, Any] = Field(default_factory=dict)
    missing_data: list[str] = Field(default_factory=list)
    provider_limitations: list[str] = Field(default_factory=list)
    error: FundamentalErrorResponse | None = None


class FundamentalAIOpinionResponse(BaseModel):
    status: Literal[
        "not_requested",
        "queued",
        "running",
        "succeeded",
        "cached",
        "failed",
        "paused",
        "budget_exhausted",
        "skipped",
    ]
    verdict: Literal["pass", "fail", "uncertain"] | None = None
    checked_at: datetime.datetime | None = None
    summary: str | None = None
    verdict_reference_ids: list[str] = Field(default_factory=list)
    strengths: list[dict[str, Any]] = Field(default_factory=list)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    review_focus: list[dict[str, Any]] = Field(default_factory=list)
    skip_reason: str | None = None
    error: FundamentalErrorResponse | None = None
    model: FundamentalModelResponse | None = None


class FundamentalDetailResponse(BaseModel):
    result_id: uuid.UUID
    scan_run_id: uuid.UUID
    instrument: FundamentalInstrumentResponse
    fundamental: FundamentalSourceResponse
    ai_opinion: FundamentalAIOpinionResponse
    snapshot: FundamentalSnapshotResponse | None = None


class FundamentalTraceSourceResponse(BaseModel):
    snapshot_id: uuid.UUID | None = None
    provider: str | None = None
    statement_type: str | None = None
    fetched_at: datetime.datetime | None = None
    content_hash: str | None = None
    endpoint_manifest: list[dict[str, Any]] = Field(default_factory=list)
    raw_payload: dict[str, Any] | None = None
    contract_valid: bool | None = None
    contract_error: str | None = None


class FundamentalTraceNormalizedResponse(BaseModel):
    schema_version: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)


class FundamentalTraceRulesResponse(BaseModel):
    rubric_version: str | None = None
    scorecard: dict[str, Any] = Field(default_factory=dict)
    contract_valid: bool
    unresolved_reference_ids: list[str] = Field(default_factory=list)


class FundamentalAIAttemptResponse(BaseModel):
    id: uuid.UUID
    attempt_number: int
    status: str
    model: str
    reasoning_effort: str
    prompt_version: str
    response_schema: str
    input_hash: str
    request_payload: dict[str, Any]
    response_payload: dict[str, Any] | None = None
    http_status: int | None = None
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    cost: float = 0.0
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime.datetime
    completed_at: datetime.datetime | None = None


class FundamentalTraceResponse(BaseModel):
    result_id: uuid.UUID
    source: FundamentalTraceSourceResponse
    normalized: FundamentalTraceNormalizedResponse
    python_fit: FundamentalTraceRulesResponse
    ai_request: dict[str, Any] | None = None
    ai_attempts: list[FundamentalAIAttemptResponse] = Field(default_factory=list)
    legacy_response_captured: bool = False
    pipeline_errors: dict[str, Any] = Field(default_factory=dict)


class ScanTriggerResponse(BaseModel):
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    scan_run_id: uuid.UUID
    reused: bool = False
    as_of_date: datetime.date | None = None
    message: str


class FundamentalPassRequest(BaseModel):
    mode: Literal["retry_incomplete", "refresh_stale"] = "retry_incomplete"


class FundamentalPassProgressResponse(BaseModel):
    analysis_run_id: uuid.UUID
    scan_run_id: uuid.UUID
    status: Literal["queued", "running", "succeeded", "partial", "failed", "cancelled"]
    current_rank: int | None = None
    current_symbol: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    provider_requests: int = 0
    token_budget: int = 150000
    input_tokens: int = 0
    reasoning_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_cost: float = 0
    error_message: str | None = None
    heartbeat_at: datetime.datetime | None = None


class ScanRunResponse(BaseModel):
    id: uuid.UUID
    universe_code: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    triggered_by: str
    started_at: datetime.datetime | None = None
    completed_at: datetime.datetime | None = None
    error_message: str | None = None
    technical_config: dict[str, Any]
    as_of_date: datetime.date | None = None
    created_at: datetime.datetime
    passing_count: int


class FundamentalsProvenanceResponse(BaseModel):
    provider: str
    statement_type: Literal["consolidated", "standalone"]
    fetched_at: datetime.datetime
    latest_annual_period: str | None = None
    latest_quarterly_period: str | None = None


class ScanResultResponse(BaseModel):
    id: uuid.UUID
    rank: int
    symbol: str
    name: str | None = None
    fyers_symbol: str
    technical_score: float | None = None
    score_grade: Literal["A", "B", "C", "D"] | None = None
    score_components: dict[str, Any] = Field(default_factory=dict)
    eligibility: dict[str, bool] = Field(default_factory=dict)
    core_checks: dict[str, bool] = Field(default_factory=dict)
    close_price: float
    sma_50: float
    sma_150: float
    sma_200: float
    sma_200_yesterday: float
    sma_200_prev_22: float | None = None
    sma_200_prev_110: float | None = None
    high_52w: float
    low_52w: float
    avg_volume_20: int
    pct_from_52w_high: float
    rs_rating: int
    adtv_crore: float
    atr_10: float
    atr_50: float
    atr_ratio: float
    atr_ratio_3m_low: float
    atr_proximity_factor: float | None = None
    bb_width: float
    bb_width_20th_pct: float
    bb_width_percentile: float | None = None
    avg_volume_10: int
    avg_volume_50: int
    volume_dry_up_ratio: float
    up_down_volume_ratio: float | None = None
    pocket_pivot_age: float | None = None
    rs_line: float | None = None
    rs_line_high_52w: float | None = None
    rs_line_pct_off_high: float | None = None
    rs_benchmark_symbol: str | None = None
    rs_benchmark_source: str | None = None
    criteria_matches: dict[str, bool] = Field(default_factory=dict)
    fundamental_selected: bool
    llm_status: Literal[
        "not_requested",
        "queued",
        "running",
        "succeeded",
        "failed",
        "skipped",
    ]
    llm_verdict: Literal["pass", "fail", "uncertain"] | None = None
    llm_flags: dict[str, Any] = Field(default_factory=dict)
    llm_checked_at: datetime.datetime | None = None
    fundamental_status: str = "not_requested"
    fundamental_verdict: Literal["pass", "fail", "uncertain"] | None = None
    fundamental_scorecard: dict[str, Any] = Field(default_factory=dict)
    fundamental_assessment: FundamentalAssessmentResponse | None = None
    ai_status: str = "not_requested"
    fundamental_snapshot_id: uuid.UUID | None = None
    fundamentals_provenance: FundamentalsProvenanceResponse | None = None
    reviewer_status: Literal[
        "pending",
        "watchlisted",
        "rejected",
        "trade_planned",
    ]
