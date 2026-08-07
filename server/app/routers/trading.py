from typing import Annotated
from uuid import UUID

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.config import settings
from app.database import db_dep
from app.domain.trading import TradeValidationError
from app.schemas.trading import (
    ExecutionStatusView,
    ManualConfirmation,
    OrderIntentView,
    PositionView,
    TradeConfirmationResult,
    TradeInstructionCreate,
    TradeInstructionView,
)
from app.services.execution_engine import (
    ExecutionBlockedError,
    ExecutionSafetyError,
    complete_paper_entry_fill,
    publish_tick_subscriptions,
    submit_live_entry_intent,
)
from app.services.trade_service import (
    TradeConflictError,
    TradeNotFoundError,
    confirm_trade_instruction,
    create_trade_instruction,
    get_trade_instruction,
    load_confirmation_records,
    list_order_intents,
    list_positions,
    list_trade_instructions,
)

router = APIRouter(prefix="/trading", tags=["trading"])


@router.get("/execution-status", response_model=ExecutionStatusView)
async def get_execution_status() -> ExecutionStatusView:
    return ExecutionStatusView(
        execution_mode=settings.execution_mode,
        live_order_placement_enabled=settings.live_order_placement_enabled,
        required_confirmation=(
            "CONFIRM_LIVE_ORDER"
            if settings.execution_mode == "live"
            else "CONFIRM_PAPER_TRADE"
        ),
    )


def _raise_trade_http_error(exc: Exception) -> None:
    if isinstance(exc, TradeNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (TradeConflictError, ExecutionBlockedError)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (TradeValidationError, ExecutionSafetyError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


@router.post(
    "/trade-instructions",
    response_model=TradeInstructionView,
    status_code=status.HTTP_201_CREATED,
)
async def create_instruction(
    payload: TradeInstructionCreate,
    db: db_dep,
) -> TradeInstructionView:
    try:
        instruction = await create_trade_instruction(db, payload)
        await db.commit()
        return instruction
    except Exception as exc:
        await db.rollback()
        _raise_trade_http_error(exc)
        raise


@router.get("/trade-instructions", response_model=list[TradeInstructionView])
async def get_instructions(
    db: db_dep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[TradeInstructionView]:
    return await list_trade_instructions(db, limit=limit)


@router.get(
    "/trade-instructions/{instruction_id}",
    response_model=TradeInstructionView,
)
async def get_instruction(
    instruction_id: UUID,
    db: db_dep,
) -> TradeInstructionView:
    try:
        return await get_trade_instruction(db, instruction_id)
    except Exception as exc:
        _raise_trade_http_error(exc)
        raise


@router.post(
    "/trade-instructions/{instruction_id}/confirm",
    response_model=TradeConfirmationResult,
)
async def confirm_instruction(
    instruction_id: UUID,
    payload: ManualConfirmation,
    request: Request,
    db: db_dep,
) -> TradeConfirmationResult:
    expected_confirmation = (
        "CONFIRM_LIVE_ORDER"
        if settings.execution_mode == "live"
        else "CONFIRM_PAPER_TRADE"
    )
    if payload.confirmation != expected_confirmation:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Execution mode requires the explicit phrase "
                f"'{expected_confirmation}'."
            ),
        )
    try:
        result = await confirm_trade_instruction(db, instruction_id)
        # The transaction containing the manual checkpoint, position, and
        # idempotent intent must be durable before a live broker call.
        await db.commit()
    except Exception as exc:
        await db.rollback()
        _raise_trade_http_error(exc)
        raise

    if result.order_intent.execution_mode == "paper":
        if not result.idempotent_replay:
            try:
                await complete_paper_entry_fill(
                    db,
                    order_intent_id=result.order_intent.id,
                    position_id=result.position.id,
                    trade_instruction_id=instruction_id,
                    fill_price=Decimal(result.instruction.planned_entry_price),
                    quantity=result.instruction.quantity,
                )
                await db.commit()
                await publish_tick_subscriptions(
                    request.app.state.redis,
                    [result.position.symbol],
                )
                from app.services.journal_outbox import trigger_journal_dispatcher

                await trigger_journal_dispatcher(request.app.state.redis)
            except Exception as exc:
                await db.rollback()
                _raise_trade_http_error(exc)
                raise
        position, intent = await load_confirmation_records(
            db,
            instruction_id=instruction_id,
        )
        return TradeConfirmationResult(
            instruction=await get_trade_instruction(db, instruction_id),
            position=position,
            order_intent=intent,
            idempotent_replay=result.idempotent_replay,
            broker_call_made=False,
        )

    try:
        submission = await submit_live_entry_intent(
            db,
            request.app.state.redis,
            order_intent_id=result.order_intent.id,
        )
    except Exception as exc:
        # submit_live_entry_intent owns its durability commits. Never roll
        # back the already-committed human instruction/intent here.
        _raise_trade_http_error(exc)
        raise

    position, intent = await load_confirmation_records(
        db,
        instruction_id=instruction_id,
    )
    return TradeConfirmationResult(
        instruction=await get_trade_instruction(db, instruction_id),
        position=position,
        order_intent=intent,
        idempotent_replay=result.idempotent_replay,
        broker_call_made=submission.broker_call_made,
        submission_outcome=submission.outcome,
        submission_message=submission.message,
    )


@router.get("/positions", response_model=list[PositionView])
async def get_positions(
    db: db_dep,
    active_only: bool = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[PositionView]:
    return await list_positions(db, active_only=active_only, limit=limit)


@router.get("/order-intents", response_model=list[OrderIntentView])
async def get_order_intents(
    db: db_dep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[OrderIntentView]:
    return await list_order_intents(db, limit=limit)
