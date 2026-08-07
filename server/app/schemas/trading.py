from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class TrailingRule(BaseModel):
    type: Literal["none", "step_pct", "atr"] = "none"
    value: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_value(self) -> "TrailingRule":
        if self.type == "none" and self.value is not None:
            raise ValueError("A 'none' trailing rule cannot have a value.")
        if self.type != "none" and self.value is None:
            raise ValueError(f"A '{self.type}' trailing rule requires a value.")
        if self.type == "step_pct" and self.value is not None and self.value >= 100:
            raise ValueError("Step percentage must be below 100.")
        return self


class TradeInstructionCreate(BaseModel):
    symbol: str = Field(min_length=3, max_length=100)
    screening_result_id: UUID | None = None
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0)
    product_type: Literal["CNC"] = "CNC"
    entry_order_type: Literal["market", "limit"]
    planned_entry_price: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    entry_limit_price: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=18,
        decimal_places=4,
    )
    initial_stop_loss: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    initial_target: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=18,
        decimal_places=4,
    )
    trailing_rule: TrailingRule = Field(default_factory=TrailingRule)
    risk_amount: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=18,
        decimal_places=4,
    )
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_order_price(self) -> "TradeInstructionCreate":
        if self.entry_order_type == "limit" and self.entry_limit_price is None:
            raise ValueError("A limit order requires entry_limit_price.")
        if self.entry_order_type == "market" and self.entry_limit_price is not None:
            raise ValueError("A market order cannot include entry_limit_price.")
        return self


class TradeInstructionView(BaseModel):
    id: UUID
    instrument_id: UUID
    screening_result_id: UUID | None
    symbol: str
    display_symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    product_type: Literal["CNC"]
    entry_order_type: Literal["market", "limit", "stop", "stop_limit"]
    planned_entry_price: Decimal
    entry_limit_price: Decimal | None
    initial_stop_loss: Decimal
    initial_target: Decimal | None
    trailing_rule: dict
    risk_amount: Decimal | None
    status: Literal["draft", "confirmed", "submitted", "cancelled", "rejected"]
    manual_confirmed_at: datetime | None
    submitted_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ManualConfirmation(BaseModel):
    confirmation: Literal["CONFIRM_PAPER_TRADE", "CONFIRM_LIVE_ORDER"]


class ExecutionStatusView(BaseModel):
    execution_mode: Literal["paper", "live"]
    live_order_placement_enabled: bool
    required_confirmation: Literal["CONFIRM_PAPER_TRADE", "CONFIRM_LIVE_ORDER"]


class PositionView(BaseModel):
    id: UUID
    trade_instruction_id: UUID | None
    screening_result_id: UUID | None
    symbol: str
    display_symbol: str
    state: Literal[
        "pending_entry",
        "open",
        "trailing_active",
        "exit_pending",
        "closed",
        "cancelled",
    ]
    side: Literal["long", "short"]
    quantity: int
    open_quantity: int
    product_type: Literal["CNC"]
    average_entry_price: Decimal | None
    current_stop_loss: Decimal | None
    current_target: Decimal | None
    trailing_rule: dict
    realized_pnl: Decimal
    opened_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class OrderIntentView(BaseModel):
    id: UUID
    idempotency_key: str
    trade_instruction_id: UUID | None
    position_id: UUID | None
    symbol: str
    display_symbol: str
    intent_type: str
    side: Literal["buy", "sell"]
    quantity: int
    product_type: Literal["CNC"]
    order_type: Literal["market", "limit", "stop", "stop_limit"]
    limit_price: Decimal | None
    trigger_price: Decimal | None
    status: str
    execution_mode: Literal["paper", "live"]
    fyers_async_id: str | None
    fyers_order_id: str | None
    exchange_order_id: str | None
    broker_requested_at: datetime | None
    broker_responded_at: datetime | None
    requested_by_component: Literal["execution_engine"]
    reason: str | None
    created_at: datetime
    updated_at: datetime


class TradeConfirmationResult(BaseModel):
    instruction: TradeInstructionView
    position: PositionView
    order_intent: OrderIntentView
    idempotent_replay: bool
    broker_call_made: bool = False
    submission_outcome: Literal[
        "paper_logged",
        "submitted",
        "already_submitted",
        "already_in_progress",
        "rejected",
        "submission_unknown",
    ] = "paper_logged"
    submission_message: str | None = None


class KillSwitchUpdate(BaseModel):
    enabled: bool
    reason: str = Field(min_length=3, max_length=500)


class KillSwitchView(BaseModel):
    control_key: Literal["global_kill_switch"] = "global_kill_switch"
    enabled: bool
    reason: str | None
    changed_by: str
    changed_at: datetime
    redis_published: bool | None = None


class FundamentalControlUpdate(BaseModel):
    paused: bool
    reason: str = Field(min_length=3, max_length=500)


class FundamentalControlView(BaseModel):
    control_key: str
    enabled: bool
    paused: bool
    reason: str | None
    changed_by: str
    changed_at: datetime
    redis_published: bool | None = None


class FundamentalControlsView(BaseModel):
    processing: FundamentalControlView
    ai: FundamentalControlView


class ReconciliationRunView(BaseModel):
    id: UUID
    status: Literal["running", "succeeded", "failed"]
    started_at: datetime
    completed_at: datetime | None
    discrepancies_found: int
    summary: dict
    error_message: str | None


class ReconciliationItemView(BaseModel):
    id: UUID
    reconciliation_run_id: UUID
    domain: str
    local_record_id: str | None
    broker_record_id: str | None
    issue_type: str
    severity: Literal["info", "warning", "critical"]
    local_snapshot: dict
    broker_snapshot: dict
    resolution_status: Literal["open", "ignored", "resolved"]
    resolved_at: datetime | None
    created_at: datetime


class ReconciliationTriggerResponse(BaseModel):
    status: str
    job_id: str | None
    message: str
