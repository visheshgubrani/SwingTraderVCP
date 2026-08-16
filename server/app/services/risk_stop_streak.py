"""Durable deterministic three-stop circuit breaker for P10 entries/adds."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.journal_charges import CHARGE_VERSION, FillLeg, estimate_cnc_charges


ExecutionMode = Literal["paper", "live"]


def classify_stop_closure(exit_purposes: set[str], net_pnl: Decimal) -> str:
    """Classify a completed proposal trade without mutating streak state."""
    if not exit_purposes or exit_purposes & {"manual", "external", "invalid_fill", "risk_reduction"}:
        return "ignored"
    if exit_purposes == {"stop_loss"} and net_pnl < 0:
        return "increment"
    return "reset"


def advance_stop_streak(
    *, count: int, tripped: bool, classification: str, limit: int
) -> tuple[int, bool, bool]:
    """Return (new count, tripped, newly tripped) for one ordered closure."""
    if tripped:
        return count, True, False
    new_count = count + 1 if classification == "increment" else 0 if classification == "reset" else count
    newly_tripped = new_count >= limit
    return new_count, newly_tripped, newly_tripped


@dataclass(frozen=True)
class StopStreakStatus:
    execution_mode: ExecutionMode
    consecutive_count: int
    limit: int
    tripped: bool
    tripped_at: Any | None
    trip_position_id: UUID | None


async def _lock_state(db: AsyncSession, execution_mode: ExecutionMode) -> dict[str, Any]:
    await db.execute(
        text(
            """
            INSERT INTO risk_stop_streak_state (execution_mode)
            VALUES (:execution_mode) ON CONFLICT (execution_mode) DO NOTHING
            """
        ),
        {"execution_mode": execution_mode},
    )
    row = (
        await db.execute(
            text(
                """
                SELECT * FROM risk_stop_streak_state
                WHERE execution_mode = :execution_mode FOR UPDATE
                """
            ),
            {"execution_mode": execution_mode},
        )
    ).mappings().one()
    return dict(row)


async def _active_limit(db: AsyncSession) -> int:
    value = (
        await db.execute(
            text(
                """
                SELECT consecutive_stop_limit FROM risk_policies
                WHERE is_active = true LIMIT 1
                """
            )
        )
    ).scalar_one_or_none()
    return int(value or 3)


async def _classify_position(
    db: AsyncSession, position_id: UUID
) -> tuple[str, list[str], Decimal | None, dict[str, Any]]:
    position = (
        await db.execute(
            text(
                """
                SELECT id, proposal_id, side, realized_pnl, closed_at
                FROM positions WHERE id = :position_id AND state = 'closed'
                """
            ),
            {"position_id": position_id},
        )
    ).mappings().one_or_none()
    if position is None or position["proposal_id"] is None:
        return "ignored", [], None, {"reason": "not_closed_p10_proposal"}

    fills = (
        await db.execute(
            text(
                """
                SELECT oi.intent_type, oi.exit_purpose, oi.side,
                       f.quantity, f.price
                FROM order_fills f
                JOIN order_intents oi ON oi.id = f.order_intent_id
                WHERE oi.position_id = :position_id
                ORDER BY f.filled_at, f.id
                """
            ),
            {"position_id": position_id},
        )
    ).mappings().all()
    charge_legs = [
        FillLeg(
            side="buy" if str(fill["side"]).lower() == "buy" else "sell",
            quantity=int(fill["quantity"]),
            price=Decimal(fill["price"]),
        )
        for fill in fills
    ]
    charges = estimate_cnc_charges(charge_legs).total if charge_legs else Decimal("0")
    net_pnl = Decimal(position["realized_pnl"] or 0) - charges
    purposes: list[str] = []
    for fill in fills:
        if str(fill["side"]).lower() == ("buy" if position["side"] == "long" else "sell"):
            continue
        purpose = fill["exit_purpose"]
        if purpose is None:
            purpose = {
                "stop_loss_exit": "stop_loss",
                "target_exit": "target",
                "trailing_exit": "runner_trail",
                "manual_exit": "manual",
                "risk_reduction_exit": "risk_reduction",
                "invalid_fill_exit": "invalid_fill",
            }.get(str(fill["intent_type"]), "manual")
        purposes.append(str(purpose))
    unique = set(purposes)
    classification = classify_stop_closure(unique, net_pnl)
    return classification, sorted(unique), net_pnl, {
        "estimated_charges": str(charges),
        "gross_realized_pnl": str(position["realized_pnl"] or 0),
    }


async def _apply_closed_position(
    db: AsyncSession,
    *,
    state: dict[str, Any],
    position_id: UUID,
    closed_at: Any,
    limit: int,
) -> dict[str, Any]:
    existing = (
        await db.execute(
            text("SELECT id FROM risk_stop_streak_events WHERE position_id = :position_id"),
            {"position_id": position_id},
        )
    ).scalar_one_or_none()
    if existing is not None:
        return state

    classification, purposes, net_pnl, details = await _classify_position(db, position_id)
    previous_count = int(state["consecutive_count"])
    new_count, tripped, newly_tripped = advance_stop_streak(
        count=previous_count,
        tripped=bool(state["tripped"]),
        classification=classification,
        limit=limit,
    )

    await db.execute(
        text(
            """
            INSERT INTO risk_stop_streak_events (
                execution_mode, position_id, closed_at, classification,
                exit_purposes, estimated_net_pnl, charge_policy_version,
                previous_count, new_count, tripped, details
            ) VALUES (
                :execution_mode, :position_id, :closed_at, :classification,
                CAST(:purposes AS jsonb), :net_pnl, :charge_version,
                :previous_count, :new_count, :tripped, CAST(:details AS jsonb)
            )
            """
        ),
        {
            "execution_mode": state["execution_mode"],
            "position_id": position_id,
            "closed_at": closed_at,
            "classification": classification,
            "purposes": json.dumps(purposes),
            "net_pnl": net_pnl,
            "charge_version": CHARGE_VERSION,
            "previous_count": previous_count,
            "new_count": new_count,
            "tripped": tripped,
            "details": json.dumps(details),
        },
    )
    await db.execute(
        text(
            """
            UPDATE risk_stop_streak_state
            SET consecutive_count = :count,
                tripped = :tripped,
                tripped_at = CASE WHEN :newly_tripped THEN :closed_at ELSE tripped_at END,
                trip_position_id = CASE WHEN :newly_tripped THEN :position_id ELSE trip_position_id END,
                last_evaluated_closed_at = :closed_at,
                last_evaluated_position_id = :position_id,
                updated_at = now()
            WHERE execution_mode = :execution_mode
            """
        ),
        {
            "count": new_count,
            "tripped": tripped,
            "newly_tripped": newly_tripped,
            "closed_at": closed_at,
            "position_id": position_id,
            "execution_mode": state["execution_mode"],
        },
    )
    if newly_tripped:
        await db.execute(
            text(
                """
                UPDATE system_controls
                SET enabled = true,
                    reason = CASE WHEN enabled THEN reason ELSE :reason END,
                    changed_by = CASE WHEN enabled THEN changed_by ELSE 'risk_stop_streak' END,
                    changed_at = now()
                WHERE control_key = 'new_entries_paused'
                """
            ),
            {
                "reason": (
                    f"Automatically paused after {new_count} consecutive "
                    "proposal-backed pure stop-loss closures."
                )
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO system_events (
                    component, severity, event_type, position_id, payload
                ) VALUES (
                    'risk_stop_streak', 'critical', 'three_stop_circuit_tripped',
                    :position_id, CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "position_id": position_id,
                "payload": json.dumps(
                    {
                        "execution_mode": state["execution_mode"],
                        "consecutive_count": new_count,
                        "limit": limit,
                    }
                ),
            },
        )
    return {
        **state,
        "consecutive_count": new_count,
        "tripped": tripped,
        "tripped_at": closed_at if newly_tripped else state.get("tripped_at"),
        "trip_position_id": position_id if newly_tripped else state.get("trip_position_id"),
        "last_evaluated_closed_at": closed_at,
        "last_evaluated_position_id": position_id,
    }


async def synchronize_stop_streak(
    db: AsyncSession, execution_mode: ExecutionMode
) -> StopStreakStatus:
    state = await _lock_state(db, execution_mode)
    limit = await _active_limit(db)
    closures = (
        await db.execute(
            text(
                """
                SELECT p.id, p.closed_at
                FROM positions p
                WHERE p.execution_mode = :execution_mode
                  AND p.proposal_id IS NOT NULL
                  AND p.state = 'closed'
                  AND p.closed_at >= :activation_watermark
                  AND NOT EXISTS (
                      SELECT 1 FROM risk_stop_streak_events e WHERE e.position_id = p.id
                  )
                ORDER BY p.closed_at, p.id
                """
            ),
            {
                "execution_mode": execution_mode,
                "activation_watermark": state["activation_watermark"],
            },
        )
    ).mappings().all()
    for closure in closures:
        state = await _apply_closed_position(
            db,
            state=state,
            position_id=closure["id"],
            closed_at=closure["closed_at"],
            limit=limit,
        )
    return StopStreakStatus(
        execution_mode=execution_mode,
        consecutive_count=int(state["consecutive_count"]),
        limit=limit,
        tripped=bool(state["tripped"]),
        tripped_at=state.get("tripped_at"),
        trip_position_id=state.get("trip_position_id"),
    )


async def record_closed_position(
    db: AsyncSession, execution_mode: ExecutionMode, position_id: UUID, closed_at: Any
) -> StopStreakStatus:
    state = await _lock_state(db, execution_mode)
    limit = await _active_limit(db)
    state = await _apply_closed_position(
        db,
        state=state,
        position_id=position_id,
        closed_at=closed_at,
        limit=limit,
    )
    return StopStreakStatus(
        execution_mode=execution_mode,
        consecutive_count=int(state["consecutive_count"]),
        limit=limit,
        tripped=bool(state["tripped"]),
        tripped_at=state.get("tripped_at"),
        trip_position_id=state.get("trip_position_id"),
    )


async def reset_stop_streak(
    db: AsyncSession,
    *,
    execution_mode: ExecutionMode,
    changed_by: str,
    reason: str,
) -> StopStreakStatus:
    state = await _lock_state(db, execution_mode)
    await db.execute(
        text(
            """
            UPDATE risk_stop_streak_state
            SET activation_watermark = now(), consecutive_count = 0,
                tripped = false, tripped_at = NULL, trip_position_id = NULL,
                owner_reset_at = now(), owner_reset_by = :changed_by,
                owner_reset_reason = :reason, updated_at = now()
            WHERE execution_mode = :execution_mode
            """
        ),
        {
            "execution_mode": execution_mode,
            "changed_by": changed_by,
            "reason": reason,
        },
    )
    # Clear only the pause owned by this breaker. An operator pause survives.
    await db.execute(
        text(
            """
            UPDATE system_controls
            SET enabled = false, reason = :reason, changed_by = :changed_by,
                changed_at = now()
            WHERE control_key = 'new_entries_paused'
              AND changed_by = 'risk_stop_streak'
              AND NOT EXISTS (
                  SELECT 1 FROM risk_stop_streak_state
                  WHERE execution_mode <> :execution_mode AND tripped = true
              )
            """
        ),
        {
            "reason": f"Stop-streak reset: {reason}",
            "changed_by": changed_by,
            "execution_mode": execution_mode,
        },
    )
    await db.execute(
        text(
            """
            INSERT INTO system_events (component, severity, event_type, payload)
            VALUES ('risk_stop_streak', 'warning', 'three_stop_circuit_reset', CAST(:payload AS jsonb))
            """
        ),
        {
            "payload": json.dumps(
                {
                    "execution_mode": execution_mode,
                    "previous_count": int(state["consecutive_count"]),
                    "changed_by": changed_by,
                    "reason": reason,
                }
            )
        },
    )
    return StopStreakStatus(
        execution_mode=execution_mode,
        consecutive_count=0,
        limit=await _active_limit(db),
        tripped=False,
        tripped_at=None,
        trip_position_id=None,
    )
