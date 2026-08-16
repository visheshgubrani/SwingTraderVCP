"""P10 Trade Proposal Pydantic Schemas.

Defines the strict Gemini AI output schema, proposal generation requests/responses,
and human decision models according to AGENTS.md §5.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.p10_sizing import EntryTemplate


VerdictType = Literal["valid", "invalid", "uncertain"]
DecisionType = Literal["approved", "rejected"]
ProposalStatusType = Literal["pending_approval", "approved", "rejected", "expired_unapproved"]


class GeminiContractionAnchor(BaseModel):
    """One dated, price-specific geometry observation from the chart reader."""

    model_config = ConfigDict(extra="forbid")

    date: dt.date
    price: Decimal = Field(gt=0)
    anchor_type: Literal["contraction_high", "contraction_low", "resistance"]


class GeminiVcpProposalOutput(BaseModel):
    """Strict JSON schema for serial Gemini VCP pattern reading.
    Contains NO money, stop, quantity, or risk fields.
    """
    model_config = ConfigDict(extra="forbid")

    verdict: VerdictType
    contradicts_scanner: bool
    confidence: float = Field(ge=0.0, le=1.0)
    contraction_anchors: list[GeminiContractionAnchor] = Field(
        min_length=2,
        max_length=8,
        description="List of dated contraction peak/trough anchors.",
    )
    pivot_price: Decimal = Field(gt=0)
    t1: Decimal = Field(gt=0)
    t2: Decimal = Field(gt=0)
    t3: Decimal = Field(gt=0)
    entry_template: EntryTemplate
    base_tightness: Literal["solid", "loose", "unclear"]
    dry_up_quality: Literal["drying_up", "supportive", "mixed", "weak", "unclear"]
    resistance_room: Literal["clear", "moderate", "congested", "unclear"]
    evidence_summary: str = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def validate_anchor_shape(self) -> "GeminiVcpProposalOutput":
        kinds = {anchor.anchor_type for anchor in self.contraction_anchors}
        if "contraction_low" not in kinds or not (
            {"contraction_high", "resistance"} & kinds
        ):
            raise ValueError(
                "contraction_anchors must contain a contraction_low and a high/resistance anchor"
            )
        return self


class ProposalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: DecisionType
    expected_proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    notes: str | None = Field(default=None, max_length=2000)


class TradeProposalDetailResponse(BaseModel):
    id: UUID
    automation_run_id: UUID | None
    screening_result_id: UUID
    instrument_id: UUID
    symbol: str
    as_of_date: dt.date
    status: ProposalStatusType
    approval_deadline: dt.datetime
    entry_session_date: dt.date
    proposal_hash: str
    source_hash: str
    renderer_version: str
    prompt_version: str
    schema_version: str
    geometry_version: str
    model: str
    confidence: Decimal
    entry_template: str
    pivot_price: Decimal
    initial_stop: Decimal
    stop_distance_pct: Decimal
    chase_ceiling: Decimal
    t1: Decimal
    t2: Decimal
    t3: Decimal
    risk_budget_pct: Decimal
    approved_risk_budget_amount: Decimal
    risk_policy_version: int
    leg_count: int
    leg_risk_allocations: list[Decimal]
    relative_volume_threshold: Decimal
    gemini_evidence: dict[str, Any]
    geometry: dict[str, Any]
    context_image_hash: str | None
    detail_image_hash: str | None
    live_eligible: bool
    generated_at: dt.datetime
    created_at: dt.datetime
    updated_at: dt.datetime


class CapacityConflictDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chosen_leg_id: UUID | None = None
    resolution_type: Literal["operator_selected", "operator_skipped"]

    @model_validator(mode="after")
    def validate_selection(self) -> "CapacityConflictDecisionRequest":
        if self.resolution_type == "operator_selected" and self.chosen_leg_id is None:
            raise ValueError("chosen_leg_id is required for operator_selected")
        if self.resolution_type == "operator_skipped" and self.chosen_leg_id is not None:
            raise ValueError("chosen_leg_id must be empty for operator_skipped")
        return self


class RiskPolicyResponse(BaseModel):
    id: UUID
    version: int
    name: str
    is_active: bool
    risk_per_trade_pct: Decimal
    max_total_open_risk_pct: Decimal
    max_single_name_notional_pct: Decimal
    max_sector_notional_pct: Decimal
    max_cluster_notional_pct: Decimal
    correlation_cluster_threshold: Decimal
    correlation_lookback_sessions: int
    daily_loss_limit_pct: Decimal
    max_open_positions: int
    deployable_capital_override: Decimal | None


class RiskPolicyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    risk_per_trade_pct: Decimal = Field(gt=0, le=Decimal("0.01"))
    max_total_open_risk_pct: Decimal = Field(gt=0, le=Decimal("0.04"))
    max_single_name_notional_pct: Decimal = Field(gt=0, le=Decimal("0.15"))
    max_sector_notional_pct: Decimal = Field(gt=0, le=Decimal("0.30"))
    max_cluster_notional_pct: Decimal = Field(gt=0, le=Decimal("0.30"))
    correlation_cluster_threshold: Decimal = Field(ge=0, le=Decimal("0.80"))
    correlation_lookback_sessions: int = Field(ge=60, le=252)
    daily_loss_limit_pct: Decimal = Field(gt=0, le=Decimal("0.02"))
    max_open_positions: int = Field(ge=1, le=8)
    deployable_capital_override: Decimal = Field(gt=0)


class AutomationControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    reason: str = Field(min_length=1, max_length=500)
