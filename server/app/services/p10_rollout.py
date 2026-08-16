"""Owner-gated P10 rollout stage: shadow → paper → reduced_live → full_live."""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.paper_broker import PaperBrokerError, seed_paper_account

RolloutStage = Literal["shadow", "paper", "reduced_live", "full_live"]

STAGE_ORDER: tuple[RolloutStage, ...] = (
    "shadow",
    "paper",
    "reduced_live",
    "full_live",
)
CONFIRMATIONS: dict[RolloutStage, str] = {
    "paper": "CONFIRM_P10_PAPER",
    "reduced_live": "CONFIRM_P10_REDUCED_LIVE",
    "full_live": "CONFIRM_P10_FULL_LIVE",
}


class RolloutBlockedError(RuntimeError):
    """Raised when a rollout promotion or approval is not allowed."""


async def get_rollout_state(db: AsyncSession) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                """
                SELECT stage, changed_by, changed_at, reason
                FROM p10_rollout_state WHERE id = true
                """
            )
        )
    ).mappings().one_or_none()
    if row is None:
        raise RolloutBlockedError("P10 rollout state is missing.")
    stage = row["stage"]
    idx = STAGE_ORDER.index(stage)
    next_stage = STAGE_ORDER[idx + 1] if idx + 1 < len(STAGE_ORDER) else None
    return {
        "stage": stage,
        "changed_by": row["changed_by"],
        "changed_at": row["changed_at"],
        "reason": row["reason"],
        "next_stage": next_stage,
        "required_confirmation": CONFIRMATIONS.get(next_stage) if next_stage else None,
        "execution_mode": settings.execution_mode,
        "live_order_placement_enabled": settings.live_order_placement_enabled,
        "approvals_allowed": stage != "shadow",
    }


async def require_approvals_allowed(db: AsyncSession) -> None:
    state = await get_rollout_state(db)
    if not state["approvals_allowed"]:
        raise RolloutBlockedError(
            "P10 is in Shadow: proposals may be reviewed or rejected, but approve is blocked."
        )


async def promote_rollout_stage(
    db: AsyncSession,
    *,
    target_stage: RolloutStage,
    confirmation: str,
    changed_by: str,
    reason: str,
) -> dict[str, Any]:
    await db.execute(text("SELECT pg_advisory_xact_lock(987654324)"))
    current = await get_rollout_state(db)
    expected = current["next_stage"]
    if expected is None:
        raise RolloutBlockedError("P10 is already at the terminal rollout stage.")
    if target_stage != expected:
        raise RolloutBlockedError(
            f"P10 can only promote from {current['stage']} to {expected}."
        )
    required = CONFIRMATIONS[target_stage]
    if confirmation != required:
        raise RolloutBlockedError(f"Promotion requires the phrase {required}.")

    if target_stage == "paper":
        if settings.execution_mode != "paper" or settings.live_order_placement_enabled:
            raise RolloutBlockedError(
                "Paper promotion requires EXECUTION_MODE=paper and LIVE_ORDER_PLACEMENT_ENABLED=false."
            )
        policy = (
            await db.execute(
                text(
                    """
                    SELECT version, deployable_capital_override
                    FROM risk_policies WHERE is_active = true
                    """
                )
            )
        ).mappings().one_or_none()
        if policy is None or policy["deployable_capital_override"] is None:
            raise RolloutBlockedError(
                "Active risk policy must have deployable_capital_override before paper."
            )
        try:
            await seed_paper_account(
                db,
                starting_cash=policy["deployable_capital_override"],
                policy_version=int(policy["version"]),
            )
        except PaperBrokerError as exc:
            raise RolloutBlockedError(str(exc)) from exc

    if target_stage in {"reduced_live", "full_live"}:
        await _assert_ready_for_live(db)

    await db.execute(
        text(
            """
            INSERT INTO p10_rollout_events (
                from_stage, to_stage, changed_by, reason, confirmation
            ) VALUES (:from_stage, :to_stage, :changed_by, :reason, :confirmation)
            """
        ),
        {
            "from_stage": current["stage"],
            "to_stage": target_stage,
            "changed_by": changed_by,
            "reason": reason,
            "confirmation": confirmation,
        },
    )
    await db.execute(
        text(
            """
            UPDATE p10_rollout_state
            SET stage = :stage, changed_by = :changed_by, changed_at = now(),
                reason = :reason
            WHERE id = true
            """
        ),
        {
            "stage": target_stage,
            "changed_by": changed_by,
            "reason": reason,
        },
    )
    return await get_rollout_state(db)


async def _assert_ready_for_live(db: AsyncSession) -> None:
    if settings.execution_mode != "live" or not settings.live_order_placement_enabled:
        raise RolloutBlockedError(
            "Live promotion requires EXECUTION_MODE=live and LIVE_ORDER_PLACEMENT_ENABLED=true."
        )
    open_paper = (
        await db.execute(
            text(
                """
                SELECT
                    (SELECT COUNT(*) FROM positions
                     WHERE execution_mode = 'paper'
                       AND state NOT IN ('closed', 'cancelled')) AS positions,
                    (SELECT COUNT(*) FROM order_intents
                     WHERE execution_mode = 'paper'
                       AND status NOT IN ('filled', 'rejected', 'cancelled')) AS intents
                """
            )
        )
    ).mappings().one()
    if int(open_paper["positions"]) or int(open_paper["intents"]):
        raise RolloutBlockedError(
            "Live promotion is blocked while paper positions or intents remain open."
        )
    enforced = (
        await db.execute(
            text(
                """
                SELECT replay_report_hash FROM market_context_policies
                WHERE mode = 'enforced' LIMIT 1
                """
            )
        )
    ).mappings().one_or_none()
    if enforced is None or not enforced["replay_report_hash"]:
        raise RolloutBlockedError(
            "Reduced live requires an enforced P9 policy with an owner-approved replay-report hash."
        )
