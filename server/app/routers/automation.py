"""P10 Automation, Trade Proposals & Risk Policy REST Router.

Exposes thin endpoints for:
- Batch automation runs
- Immutable trade proposals inbox & detail
- Human approve / reject decisions with version & hash verification
- Capacity conflict resolution
- Versioned risk policy configuration
- Entry supervisor status
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import text

from app.database import db_dep
from app.services.execution_engine import publish_tick_subscriptions
from app.schemas.proposals import (
    ProposalDecisionRequest,
    TradeProposalDetailResponse,
    CapacityConflictDecisionRequest,
    RiskPolicyResponse,
    RiskPolicyUpdateRequest,
    AutomationControlRequest,
    MarketContextLatestResponse,
    MarketContextPolicyEnforceRequest,
    StopStreakResetRequest,
    StopStreakResponse,
)
from app.services.risk_stop_streak import reset_stop_streak, synchronize_stop_streak


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/automation", tags=["automation"])


@router.get("/runs/{run_id}")
async def get_automation_run(
    run_id: UUID,
    db: db_dep,
) -> dict[str, Any]:
    """Returns details and progress for an EOD proposal automation batch run."""
    stmt = text("""
        SELECT id, scan_run_id, status, candidates_total, candidates_processed,
               proposals_generated, proposals_rejected, proposals_uncertain,
               proposals_failed, batch_deadline, started_at, completed_at,
               error_code, error_message, created_at, updated_at
        FROM automation_runs
        WHERE id = :run_id;
    """)
    res = await db.execute(stmt, {"run_id": run_id})
    row = res.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Automation run not found")
    return dict(row._mapping)


@router.get("/proposals")
async def list_trade_proposals(
    db: db_dep,
    status_filter: Annotated[
        Literal["all", "pending_approval", "approved", "rejected", "expired_unapproved"],
        Query(alias="status"),
    ] = "pending_approval",
    symbol: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    """Returns trade proposals filtered by status (default: pending_approval) and symbol."""
    where_clauses = []
    params: dict[str, Any] = {"limit": limit}

    if status_filter != "all":
        where_clauses.append("status = :status")
        params["status"] = status_filter

    if symbol:
        where_clauses.append("symbol = :symbol")
        params["symbol"] = symbol.upper()

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    stmt = text(f"""
        SELECT id, automation_run_id, screening_result_id, instrument_id, symbol,
               as_of_date, status, approval_deadline, entry_session_date,
               proposal_hash, source_hash, renderer_version, prompt_version,
               schema_version, geometry_version, model, confidence,
               entry_template, pivot_price, initial_stop, stop_distance_pct,
               chase_ceiling, t1, t2, t3, risk_budget_pct,
               approved_risk_budget_amount, risk_policy_version, leg_count,
               leg_risk_allocations, relative_volume_threshold, gemini_evidence,
               geometry, context_image_hash, detail_image_hash, live_eligible,
               generated_at, created_at, updated_at
        FROM trade_proposals
        {where_sql}
        ORDER BY created_at DESC
        LIMIT :limit;
    """)
    res = await db.execute(stmt, params)
    rows = res.fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/proposals/{proposal_id}")
async def get_trade_proposal(
    proposal_id: UUID,
    db: db_dep,
) -> dict[str, Any]:
    """Returns full details of an immutable trade proposal and its planned entry legs."""
    prop_stmt = text("""
        SELECT id, automation_run_id, screening_result_id, instrument_id, symbol,
               as_of_date, status, approval_deadline, entry_session_date,
               proposal_hash, source_hash, renderer_version, prompt_version,
               schema_version, geometry_version, model, confidence,
               entry_template, pivot_price, initial_stop, stop_distance_pct,
               chase_ceiling, t1, t2, t3, risk_budget_pct,
               approved_risk_budget_amount, risk_policy_version, leg_count,
               leg_risk_allocations, relative_volume_threshold, gemini_evidence,
               geometry, context_image_hash, detail_image_hash, live_eligible,
               generated_at, created_at, updated_at
        FROM trade_proposals
        WHERE id = :proposal_id;
    """)
    prop_res = await db.execute(prop_stmt, {"proposal_id": proposal_id})
    prop = prop_res.fetchone()
    if not prop:
        raise HTTPException(status_code=404, detail="Trade proposal not found")

    legs_stmt = text("""
        SELECT id, leg_index, risk_allocation_pct, status, trigger_type,
               trigger_price, chase_ceiling, relative_volume_threshold,
               hold_required, base_required, hold_count, base_count,
               base_low, base_high, eligible_session_start, eligible_session_end,
               filled_shares, filled_avg_price, position_id, order_intent_id
        FROM entry_legs
        WHERE proposal_id = :proposal_id
        ORDER BY leg_index ASC;
    """)
    legs_res = await db.execute(legs_stmt, {"proposal_id": proposal_id})
    legs = legs_res.fetchall()

    result = dict(prop._mapping)
    result["legs"] = [dict(l._mapping) for l in legs]
    return result


@router.post("/proposals/{proposal_id}/decision")
async def record_proposal_decision(
    proposal_id: UUID,
    payload: ProposalDecisionRequest,
    request: Request,
    db: db_dep,
) -> dict[str, Any]:
    """Records an immutable human Approve or Reject decision for a pending trade proposal.
    Verifies expected proposal hash and 09:00 IST deadline.
    """
    stmt = text("""
        SELECT id, status, approval_deadline, proposal_hash, symbol,
               live_eligible, entry_session_date, approved_risk_budget_amount
        FROM trade_proposals
        WHERE id = :proposal_id
        FOR UPDATE;
    """)
    res = await db.execute(stmt, {"proposal_id": proposal_id})
    prop = res.fetchone()
    if not prop:
        raise HTTPException(status_code=404, detail="Trade proposal not found")

    if prop.status != "pending_approval":
        raise HTTPException(
            status_code=400,
            detail=f"Proposal is not pending approval (current status: {prop.status})",
        )

    if not prop.live_eligible or prop.approved_risk_budget_amount is None:
        raise HTTPException(
            status_code=409,
            detail="Proposal is review-only or has no locked monetary risk budget.",
        )

    # Verify hash
    if payload.expected_proposal_hash != prop.proposal_hash:
        raise HTTPException(
            status_code=409,
            detail="Proposal hash mismatch! The proposal version or parameters have changed.",
        )

    # Verify deadline (09:00 IST on D1)
    now_utc = dt.datetime.now(dt.timezone.utc)
    if now_utc >= prop.approval_deadline:
        await db.execute(text("""
            UPDATE trade_proposals
            SET status = 'expired_unapproved', updated_at = now()
            WHERE id = :id;
        """), {"id": proposal_id})
        await db.commit()
        raise HTTPException(
            status_code=400,
            detail=f"Proposal approval deadline ({prop.approval_deadline}) has passed. Proposal is expired.",
        )

    # Record decision audit record
    decision_stmt = text("""
        INSERT INTO proposal_decisions (
            proposal_id, decision, expected_proposal_hash, notes
        ) VALUES (
            :proposal_id, :decision, :expected_hash, :notes
        ) RETURNING id;
    """)
    dec_res = await db.execute(decision_stmt, {
        "proposal_id": proposal_id,
        "decision": payload.decision,
        "expected_hash": payload.expected_proposal_hash,
        "notes": payload.notes,
    })
    decision_id = dec_res.scalar_one()

    if payload.decision == "approved":
        # Transition proposal to approved
        await db.execute(text("""
            UPDATE trade_proposals
            SET status = 'approved', updated_at = now()
            WHERE id = :id;
        """), {"id": proposal_id})

        # Arm initial leg (L1)
        await db.execute(text("""
            UPDATE entry_legs
            SET status = 'armed', updated_at = now()
            WHERE proposal_id = :id AND leg_index = 1;
        """), {"id": proposal_id})
        logger.info(f"Proposal {proposal_id} ({prop.symbol}) APPROVED and L1 ARMED")
    else:
        # Transition proposal to rejected
        await db.execute(text("""
            UPDATE trade_proposals
            SET status = 'rejected', updated_at = now()
            WHERE id = :id;
        """), {"id": proposal_id})

        # Cancel all planned legs
        await db.execute(text("""
            UPDATE entry_legs
            SET status = 'cancelled', updated_at = now()
            WHERE proposal_id = :id;
        """), {"id": proposal_id})
        logger.info(f"Proposal {proposal_id} ({prop.symbol}) REJECTED")

    await db.commit()
    if payload.decision == "approved":
        await publish_tick_subscriptions(request.app.state.redis, [prop.symbol])
    return {
        "proposal_id": proposal_id,
        "status": payload.decision,
        "decision_id": decision_id,
        "decided_at": now_utc.isoformat(),
    }


@router.get("/capacity-conflicts")
async def list_capacity_conflicts(
    db: db_dep,
    status_filter: Annotated[
        Literal["all", "open", "resolved", "expired_skipped"],
        Query(alias="status"),
    ] = "open",
) -> list[dict[str, Any]]:
    """Returns capacity conflicts requiring operator resolution."""
    where_clause = "WHERE status = :status" if status_filter != "all" else ""
    stmt = text(f"""
        SELECT c.id, c.bar_timestamp, c.competing_leg_ids, c.scanner_score,
               c.status, c.chosen_leg_id, c.resolution_type, c.decided_at,
               c.executed_at, c.created_at, c.updated_at,
               COALESCE((
                   SELECT jsonb_agg(jsonb_build_object(
                       'leg_id', el.id,
                       'symbol', tp.symbol,
                       'leg_index', el.leg_index,
                       'confidence', tp.confidence,
                       'conservative_rr', (tp.t1 - el.chase_ceiling)
                           / NULLIF(el.chase_ceiling - tp.initial_stop, 0)
                   ) ORDER BY tp.symbol, el.leg_index)
                   FROM entry_legs el
                   JOIN trade_proposals tp ON tp.id = el.proposal_id
                   WHERE el.id::text IN (
                       SELECT jsonb_array_elements_text(c.competing_leg_ids)
                   )
               ), '[]'::jsonb) AS candidates
        FROM capacity_conflicts c
        {where_clause}
        ORDER BY c.created_at DESC;
    """)
    params = {"status": status_filter} if status_filter != "all" else {}
    res = await db.execute(stmt, params)
    rows = res.fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/capacity-conflicts/{conflict_id}/decision")
async def resolve_capacity_conflict(
    conflict_id: UUID,
    payload: CapacityConflictDecisionRequest,
    db: db_dep,
) -> dict[str, Any]:
    """Resolves an open capacity conflict by selecting a winner or skipping all."""
    stmt = text("""
        SELECT id, status, competing_leg_ids, bar_timestamp
        FROM capacity_conflicts
        WHERE id = :conflict_id
        FOR UPDATE;
    """)
    res = await db.execute(stmt, {"conflict_id": conflict_id})
    conflict = res.fetchone()
    if not conflict:
        raise HTTPException(status_code=404, detail="Capacity conflict not found")

    if conflict.status != "open":
        raise HTTPException(status_code=400, detail="Conflict is already resolved")

    now_utc = dt.datetime.now(dt.timezone.utc)
    if now_utc > conflict.bar_timestamp + dt.timedelta(minutes=10):
        await db.execute(
            text(
                """
                UPDATE entry_legs
                SET status = 'armed', signal_bar_timestamp = NULL
                WHERE id = ANY(:leg_ids) AND status = 'trigger_observed'
                """
            ),
            {"leg_ids": [UUID(value) for value in conflict.competing_leg_ids]},
        )
        await db.execute(
            text(
                """
                UPDATE capacity_conflicts
                SET status = 'expired_skipped', resolution_type = 'auto_expired',
                    decided_at = :now, executed_at = :now
                WHERE id = :conflict_id AND status = 'open'
                """
            ),
            {"conflict_id": conflict_id, "now": now_utc},
        )
        await db.commit()
        raise HTTPException(status_code=409, detail="Capacity signal has expired")

    if payload.resolution_type == "operator_selected":
        if not payload.chosen_leg_id:
            raise HTTPException(status_code=400, detail="chosen_leg_id is required for operator_selected")
        competing = {UUID(value) for value in conflict.competing_leg_ids}
        if payload.chosen_leg_id not in competing:
            raise HTTPException(
                status_code=400,
                detail="chosen_leg_id is not part of this capacity conflict",
            )

    update_stmt = text("""
        UPDATE capacity_conflicts
        SET status = 'resolved', chosen_leg_id = :chosen_id,
            resolution_type = :res_type, decided_at = :now, updated_at = :now
        WHERE id = :conflict_id;
    """)
    await db.execute(update_stmt, {
        "conflict_id": conflict_id,
        "chosen_id": payload.chosen_leg_id,
        "res_type": payload.resolution_type,
        "now": now_utc,
    })
    await db.commit()

    return {
        "conflict_id": conflict_id,
        "status": "resolved",
        "resolution_type": payload.resolution_type,
        "chosen_leg_id": payload.chosen_leg_id,
    }


@router.get("/risk-policy")
async def get_active_risk_policy(
    db: db_dep,
) -> dict[str, Any]:
    """Returns the currently active risk policy version."""
    stmt = text("""
        SELECT id, version, name, is_active, risk_per_trade_pct, max_total_open_risk_pct,
               max_single_name_notional_pct, max_sector_notional_pct, max_cluster_notional_pct,
               correlation_cluster_threshold, correlation_lookback_sessions,
               daily_loss_limit_pct, max_open_positions, deployable_capital_override,
               consecutive_stop_limit,
               created_at, updated_at
        FROM risk_policies
        WHERE is_active = true
        ORDER BY version DESC
        LIMIT 1;
    """)
    res = await db.execute(stmt)
    row = res.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No active risk policy found")
    return dict(row._mapping)


@router.put("/risk-policy")
async def update_risk_policy(
    payload: RiskPolicyUpdateRequest,
    db: db_dep,
) -> dict[str, Any]:
    """Create and activate a new immutable policy version."""
    await db.execute(text("SELECT pg_advisory_xact_lock(987654322)"))
    next_version = int(
        (await db.execute(text("SELECT COALESCE(MAX(version), 0) + 1 FROM risk_policies"))).scalar_one()
    )
    await db.execute(text("UPDATE risk_policies SET is_active = false WHERE is_active = true"))
    values = payload.model_dump()
    result = await db.execute(
        text(
            """
            INSERT INTO risk_policies (
                version, name, is_active, risk_per_trade_pct,
                max_total_open_risk_pct, max_single_name_notional_pct,
                max_sector_notional_pct, max_cluster_notional_pct,
                correlation_cluster_threshold, correlation_lookback_sessions,
                daily_loss_limit_pct, max_open_positions,
                deployable_capital_override, consecutive_stop_limit
            ) VALUES (
                :version, :name, true, :risk_per_trade_pct,
                :max_total_open_risk_pct, :max_single_name_notional_pct,
                :max_sector_notional_pct, :max_cluster_notional_pct,
                :correlation_cluster_threshold, :correlation_lookback_sessions,
                :daily_loss_limit_pct, :max_open_positions,
                :deployable_capital_override, :consecutive_stop_limit
            )
            RETURNING *
            """
        ),
        {"version": next_version, **values},
    )
    row = result.mappings().one()
    await db.commit()
    return dict(row)


@router.get("/controls")
async def get_automation_controls(db: db_dep) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                """
                SELECT control_key, enabled, reason, changed_by, changed_at
                FROM system_controls
                WHERE control_key IN (
                    'global_kill_switch', 'proposal_processing_paused',
                    'new_entries_paused'
                )
                ORDER BY control_key
                """
            )
        )
    ).mappings().all()
    return [dict(row) for row in rows]


@router.put("/controls/{control_key}")
async def update_automation_control(
    control_key: Literal[
        "proposal_processing_paused",
        "new_entries_paused",
    ],
    payload: AutomationControlRequest,
    request: Request,
    db: db_dep,
) -> dict[str, Any]:
    if control_key == "new_entries_paused" and not payload.enabled:
        tripped = (
            await db.execute(
                text("SELECT EXISTS (SELECT 1 FROM risk_stop_streak_state WHERE tripped = true)")
            )
        ).scalar_one()
        if tripped:
            raise HTTPException(
                status_code=409,
                detail="The stop-streak breaker is tripped; use its owner reset endpoint.",
            )
    result = await db.execute(
        text(
            """
            UPDATE system_controls
            SET enabled = :enabled, reason = :reason,
                changed_by = 'owner_api', changed_at = now()
            WHERE control_key = :control_key
            RETURNING control_key, enabled, reason, changed_by, changed_at
            """
        ),
        {
            "control_key": control_key,
            "enabled": payload.enabled,
            "reason": payload.reason,
        },
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Automation control not found")
    await db.commit()
    await request.app.state.redis.publish(
        "system_controls",
        json.dumps(
            {
                "control_key": control_key,
                "enabled": payload.enabled,
                "reason": payload.reason,
            }
        ),
    )
    return dict(row)


@router.get("/market-context/latest")
async def get_latest_market_context(db: db_dep) -> MarketContextLatestResponse:
    policy = (
        await db.execute(
            text(
                """
                SELECT id, version, mode, replay_report_hash
                FROM market_context_policies
                WHERE mode IN ('enforced', 'shadow')
                ORDER BY CASE mode WHEN 'enforced' THEN 0 ELSE 1 END, created_at DESC
                LIMIT 1
                """
            )
        )
    ).mappings().one_or_none()
    if policy is None:
        raise HTTPException(status_code=404, detail="No P9 market-context policy found")
    regime = (
        await db.execute(
            text(
                """
                SELECT reference_eod_date, market_light, exposure_multiplier,
                       trend_state, breadth_state, distribution_state,
                       source_hash, evidence, data_quality
                FROM market_regime_snapshots
                WHERE market_context_policy_id = :policy_id
                ORDER BY reference_eod_date DESC, created_at DESC LIMIT 1
                """
            ),
            {"policy_id": policy["id"]},
        )
    ).mappings().one_or_none()
    sectors: list[dict[str, Any]] = []
    if regime:
        sectors = [
            dict(row)
            for row in (
                await db.execute(
                    text(
                        """
                        SELECT result.sector_code, result.sector_name,
                               result.index_symbol, result.ordinal_rank,
                               result.rs_rating, result.raw_tier,
                               result.gate_tier, result.blended_score
                        FROM sector_strength_results result
                        JOIN sector_strength_runs run ON run.id = result.run_id
                        WHERE run.market_context_policy_id = :policy_id
                          AND run.reference_eod_date = :reference_date
                        ORDER BY result.ordinal_rank NULLS LAST, result.sector_code
                        """
                    ),
                    {
                        "policy_id": policy["id"],
                        "reference_date": regime["reference_eod_date"],
                    },
                )
            ).mappings().all()
        ]
    return MarketContextLatestResponse(
        policy_id=policy["id"],
        policy_version=str(policy["version"]),
        mode=policy["mode"],
        replay_report_hash=policy["replay_report_hash"],
        reference_eod_date=regime["reference_eod_date"] if regime else None,
        market_light=str(regime["market_light"] or "unavailable") if regime else "unavailable",
        exposure_multiplier=(
            regime["exposure_multiplier"]
            if regime and regime["exposure_multiplier"] is not None
            else 0
        ),
        trend_state=str(regime["trend_state"] or "unavailable") if regime else "unavailable",
        breadth_state=str(regime["breadth_state"] or "unavailable") if regime else "unavailable",
        distribution_state=(
            str(regime["distribution_state"] or "unavailable") if regime else "unavailable"
        ),
        source_hash=regime["source_hash"] if regime else None,
        evidence=dict(regime["evidence"] or {}) if regime else {},
        data_quality=dict(regime["data_quality"] or {}) if regime else {},
        sectors=sectors,
    )


@router.post("/market-context/policies/{version}/enforce")
async def enforce_market_context_policy(
    version: str,
    payload: MarketContextPolicyEnforceRequest,
    db: db_dep,
) -> MarketContextLatestResponse:
    await db.execute(text("SELECT pg_advisory_xact_lock(987654323)"))
    target = (
        await db.execute(
            text("SELECT id, mode FROM market_context_policies WHERE version = :version FOR UPDATE"),
            {"version": version},
        )
    ).mappings().one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Market-context policy not found")
    if target["mode"] == "retired":
        raise HTTPException(status_code=409, detail="A retired policy cannot be re-enforced")
    unverified_symbols = list(
        (
            await db.execute(
                text(
                    """
                    SELECT fyers_symbol FROM instruments
                    WHERE (
                        metadata ->> 'role' IN ('benchmark', 'rs_benchmark', 'p9_trend_benchmark', 'p9_sector_index')
                        OR fyers_symbol IN ('NSE:NIFTY50-INDEX', 'NSE:NIFTY500-INDEX')
                    )
                      AND COALESCE((metadata ->> 'verified')::boolean, false) = false
                    ORDER BY fyers_symbol
                    """
                )
            )
        ).scalars()
    )
    if unverified_symbols:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "P9 symbols must be validated against the current FYERS symbol master before enforcement.",
                "unverified_symbols": unverified_symbols,
            },
        )
    await db.execute(
        text("UPDATE market_context_policies SET mode = 'retired' WHERE mode = 'enforced' AND id <> :id"),
        {"id": target["id"]},
    )
    await db.execute(
        text(
            """
            UPDATE market_context_policies
            SET mode = 'enforced', replay_report_hash = :report_hash,
                replay_membership_mode = :membership_mode,
                approved_at = now(), approved_by = :approved_by
            WHERE id = :id
            """
        ),
        {
            "id": target["id"],
            "report_hash": payload.replay_report_hash,
            "membership_mode": payload.replay_membership_mode,
            "approved_by": payload.approved_by,
        },
    )
    await db.commit()
    return await get_latest_market_context(db)


@router.get("/stop-streak/{execution_mode}")
async def get_stop_streak(
    execution_mode: Literal["paper", "live"], db: db_dep
) -> StopStreakResponse:
    status = await synchronize_stop_streak(db, execution_mode)
    await db.commit()
    return StopStreakResponse(**status.__dict__)


@router.post("/stop-streak/{execution_mode}/reset")
async def reset_stop_streak_control(
    execution_mode: Literal["paper", "live"],
    payload: StopStreakResetRequest,
    db: db_dep,
) -> StopStreakResponse:
    status = await reset_stop_streak(
        db,
        execution_mode=execution_mode,
        changed_by="owner_api",
        reason=payload.reason,
    )
    await db.commit()
    return StopStreakResponse(**status.__dict__)


@router.get("/proposals/{proposal_id}/charts/{chart_type}")
async def get_proposal_chart(
    proposal_id: UUID,
    chart_type: Literal["context", "detail"],
    db: db_dep,
) -> Response:
    column = "context_image" if chart_type == "context" else "detail_image"
    image = (
        await db.execute(
            text(f"SELECT {column} FROM trade_proposals WHERE id = :proposal_id"),
            {"proposal_id": proposal_id},
        )
    ).scalar_one_or_none()
    if image is None:
        raise HTTPException(status_code=404, detail="Proposal chart not found")
    return Response(content=bytes(image), media_type="image/png")


@router.get("/entry-supervisor/status")
async def get_entry_supervisor_status(
    request: Request,
    db: db_dep,
) -> dict[str, Any]:
    """Returns live status of armed proposals, active legs, and recent allocation events."""
    armed_legs_stmt = text("""
        SELECT el.id, el.leg_index, el.risk_allocation_pct, el.status, el.trigger_price,
               el.chase_ceiling, tp.symbol, tp.entry_template
        FROM entry_legs el
        JOIN trade_proposals tp ON el.proposal_id = tp.id
        WHERE el.status = 'armed';
    """)
    res = await db.execute(armed_legs_stmt)
    armed_legs = [dict(r._mapping) for r in res.fetchall()]

    recent_ledger_stmt = text("""
        SELECT id, generation, leg_id, event_type, broker_funds_available,
               open_risk_before, open_risk_after, allocated_shares,
               market_context_mode, context_multiplier,
               context_adjusted_risk_ceiling, context_gate_reasons, details,
               created_at
        FROM allocation_ledger
        ORDER BY created_at DESC
        LIMIT 10;
    """)
    ledger_res = await db.execute(recent_ledger_stmt)
    recent_ledger = [dict(r._mapping) for r in ledger_res.fetchall()]

    raw_status = await request.app.state.redis.get("entry_supervisor:status")
    if isinstance(raw_status, bytes):
        raw_status = raw_status.decode()
    try:
        worker_status = json.loads(raw_status) if raw_status else {"status": "offline"}
    except json.JSONDecodeError:
        worker_status = {"status": "invalid_heartbeat"}
    trigger_observed_count = int(
        (
            await db.execute(
                text("SELECT COUNT(*) FROM entry_legs WHERE status = 'trigger_observed'")
            )
        ).scalar_one()
    )
    pending_conflicts = int(
        (
            await db.execute(
                text("SELECT COUNT(*) FROM capacity_conflicts WHERE status = 'open'")
            )
        ).scalar_one()
    )
    return {
        "status": "active" if worker_status.get("status") == "running" else "inactive",
        "heartbeat": worker_status,
        "armed_legs_count": len(armed_legs),
        "trigger_observed_count": trigger_observed_count,
        "pending_capacity_conflicts": pending_conflicts,
        "armed_legs": armed_legs,
        "recent_allocation_events": recent_ledger,
    }
