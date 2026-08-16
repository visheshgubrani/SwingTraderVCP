"""Allocation-time P9 market and sector gate for new P10 legs only."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.p9_sector_taxonomy import sector_for_industry


def _source_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True)
class P9AllocationGate:
    policy_id: UUID | None
    policy_version: str | None
    mode: Literal["shadow", "enforced"] | None
    reference_eod_date: dt.date
    regime_snapshot_id: UUID | None
    sector_strength_result_id: UUID | None
    market_light: str
    sector_tier: str
    observed_multiplier: Decimal
    effective_multiplier: Decimal
    is_blocked: bool
    reasons: tuple[str, ...]


def previous_nse_session(session_date: dt.date) -> dt.date:
    holidays = {dt.date.fromisoformat(value) for value in settings.nse_trading_holidays}
    candidate = session_date - dt.timedelta(days=1)
    while candidate.weekday() >= 5 or candidate in holidays:
        candidate -= dt.timedelta(days=1)
    return candidate


async def load_p9_allocation_gate(
    db: AsyncSession,
    *,
    industry: str | None,
    session_date: dt.date,
) -> P9AllocationGate:
    reference_date = previous_nse_session(session_date)
    policy = (
        await db.execute(
            text(
                """
                SELECT id, version, mode FROM market_context_policies
                WHERE mode IN ('enforced', 'shadow')
                ORDER BY CASE mode WHEN 'enforced' THEN 0 ELSE 1 END, created_at DESC
                LIMIT 1
                """
            )
        )
    ).mappings().one_or_none()
    if policy is None:
        return P9AllocationGate(
            policy_id=None,
            policy_version=None,
            mode=None,
            reference_eod_date=reference_date,
            regime_snapshot_id=None,
            sector_strength_result_id=None,
            market_light="unavailable",
            sector_tier="unavailable",
            observed_multiplier=Decimal("0"),
            effective_multiplier=Decimal("1"),
            is_blocked=False,
            reasons=("market_context_policy_unavailable",),
        )

    regime = (
        await db.execute(
            text(
                """
                SELECT id, market_light, exposure_multiplier, source_hash,
                       evidence, data_quality
                FROM market_regime_snapshots
                WHERE reference_eod_date = :reference_date
                  AND market_context_policy_id = :policy_id
                ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"reference_date": reference_date, "policy_id": policy["id"]},
        )
    ).mappings().one_or_none()
    light = str(regime["market_light"] or "unavailable") if regime else "unavailable"
    multiplier = (
        Decimal(regime["exposure_multiplier"])
        if regime and regime["exposure_multiplier"] is not None
        else Decimal("0")
    )
    if regime is None or not regime["source_hash"]:
        light = "unavailable"
        multiplier = Decimal("0")
    elif _source_hash(dict(regime["evidence"] or {})) != regime["source_hash"]:
        light = "unavailable"
        multiplier = Decimal("0")
    elif not all(
        bool(dict(regime["data_quality"] or {}).get(key))
        for key in ("trend_complete", "breadth_available", "distribution_available")
    ):
        light = "unavailable"
        multiplier = Decimal("0")

    sector = sector_for_industry(industry)
    sector_row = None
    if sector is not None:
        run = (
            await db.execute(
                text(
                    """
                    SELECT id, taxonomy_version, source_hash, status, data_quality
                    FROM sector_strength_runs
                    WHERE reference_eod_date = :reference_date
                      AND market_context_policy_id = :policy_id
                    ORDER BY created_at DESC LIMIT 1
                    """
                ),
                {
                    "reference_date": reference_date,
                    "policy_id": policy["id"],
                },
            )
        ).mappings().one_or_none()
        if run is not None and run["status"] == "complete":
            rows = (
                await db.execute(
                    text(
                        """
                        SELECT id, sector_code, gate_tier, evidence
                        FROM sector_strength_results WHERE run_id = :run_id
                        """
                    ),
                    {"run_id": run["id"]},
                )
            ).mappings().all()
            inputs = {str(row["sector_code"]): dict(row["evidence"] or {}) for row in rows}
            expected_hash = _source_hash(
                {
                    "reference_date": reference_date,
                    "policy_version": str(policy["version"]),
                    "taxonomy_version": str(run["taxonomy_version"]),
                    "inputs": inputs,
                }
            )
            quality = dict(run["data_quality"] or {})
            if (
                expected_hash == run["source_hash"]
                and int(quality.get("available_sectors", 0)) == int(quality.get("expected_sectors", -1))
            ):
                sector_row = next(
                    (row for row in rows if str(row["sector_code"]) == sector.code),
                    None,
                )
    sector_tier = str(sector_row["gate_tier"]) if sector_row else "unavailable"

    reasons: list[str] = []
    if light in {"red", "unavailable"}:
        reasons.append(f"market_{light}")
    if sector_tier in {"lagging", "unavailable"}:
        reasons.append(f"sector_{sector_tier}")
    enforced = str(policy["mode"]) == "enforced"
    return P9AllocationGate(
        policy_id=policy["id"],
        policy_version=str(policy["version"]),
        mode=str(policy["mode"]),  # type: ignore[arg-type]
        reference_eod_date=reference_date,
        regime_snapshot_id=regime["id"] if regime else None,
        sector_strength_result_id=sector_row["id"] if sector_row else None,
        market_light=light,
        sector_tier=sector_tier,
        observed_multiplier=multiplier,
        effective_multiplier=multiplier if enforced else Decimal("1"),
        is_blocked=enforced and bool(reasons),
        reasons=tuple(reasons),
    )


async def emit_p9_gate_event(
    db: AsyncSession,
    *,
    position_id: UUID | None,
    instrument_id: UUID | None,
    gate: P9AllocationGate,
) -> None:
    if not gate.reasons:
        return
    await db.execute(
        text(
            """
            INSERT INTO system_events (
                component, severity, event_type, instrument_id, position_id, payload
            ) VALUES (
                'entry_supervisor', :severity, :event_type, :instrument_id,
                :position_id, CAST(:payload AS jsonb)
            )
            """
        ),
        {
            "severity": "warning" if gate.mode == "shadow" else "error",
            "event_type": (
                "p9_allocation_would_block" if gate.mode == "shadow" else "p9_allocation_blocked"
            ),
            "instrument_id": instrument_id,
            "position_id": position_id,
            "payload": json.dumps(
                {
                    "reference_eod_date": gate.reference_eod_date.isoformat(),
                    "policy_version": gate.policy_version,
                    "market_light": gate.market_light,
                    "sector_tier": gate.sector_tier,
                    "reasons": gate.reasons,
                }
            ),
        },
    )
