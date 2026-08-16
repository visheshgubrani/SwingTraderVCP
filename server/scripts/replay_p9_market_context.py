#!/usr/bin/env python3
"""Replay P9 from stored EOD candles and write a review-only evidence report.

The replay runs inside one transaction and always rolls it back. It therefore
uses the production deterministic calculator without promoting a policy or
leaving synthetic snapshots behind.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import text

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.database import async_session
from app.domain.p9_market_context import rank_sector_strength
from app.domain.p9_sector_taxonomy import sector_for_industry
from app.services.market_context import compute_market_context


REVIEW_WINDOWS = {
    "2018_midcap_decline": (dt.date(2018, 1, 1), dt.date(2018, 12, 31)),
    "2020_crash_recovery": (dt.date(2020, 1, 1), dt.date(2020, 9, 30)),
    "2022_deterioration": (dt.date(2022, 1, 1), dt.date(2022, 12, 31)),
}
SECTOR_ROTATION_WINDOWS = {
    "it_weakness_2022": (dt.date(2022, 1, 1), dt.date(2022, 12, 31)),
    "broad_rotation_2023_2024": (dt.date(2023, 1, 1), dt.date(2024, 12, 31)),
}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2018, 1, 1))
    parser.add_argument("--end", type=dt.date.fromisoformat, default=dt.date.today())
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _canonical_hash(report: dict[str, Any]) -> str:
    payload = json.dumps(report, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.end < args.start:
        raise ValueError("--end must be on or after --start")
    async with async_session() as db:
        dates = list(
            (
                await db.execute(
                    text(
                        """
                        SELECT DISTINCT (c.candle_start AT TIME ZONE 'Asia/Kolkata')::date
                        FROM market_candles c
                        JOIN instruments i ON i.id = c.instrument_id
                        WHERE i.fyers_symbol = 'NSE:NIFTY500-INDEX'
                          AND c.timeframe = '1d'
                          AND (c.candle_start AT TIME ZONE 'Asia/Kolkata')::date
                              BETWEEN :start_date AND :end_date
                        ORDER BY 1
                        """
                    ),
                    {"start_date": args.start, "end_date": args.end},
                )
            ).scalars()
        )
        snapshots: list[dict[str, Any]] = []
        sector_tiers: dict[str, list[str]] = defaultdict(list)
        formula_top_five: dict[str, list[set[str]]] = defaultdict(list)
        formula_tiers_by_date: dict[str, dict[str, dict[str, str]]] = {}
        formula_top_by_date: dict[str, dict[str, list[str]]] = {}
        membership_modes: set[str] = set()
        failures: list[dict[str, str]] = []
        for reference_date in dates:
            try:
                async with db.begin_nested():
                    result = await compute_market_context(db, reference_date)
                regime = (
                    await db.execute(
                        text(
                            """
                            SELECT market_light, exposure_multiplier, trend_state,
                                   breadth_state, distribution_state, evidence, data_quality
                            FROM market_regime_snapshots WHERE id = :id
                            """
                        ),
                        {"id": result.regime_snapshot_id},
                    )
                ).mappings().one()
                sectors = (
                    await db.execute(
                        text(
                            """
                            SELECT sector_code, gate_tier, ordinal_rank, evidence
                            FROM sector_strength_results
                            WHERE run_id = :run_id ORDER BY ordinal_rank NULLS LAST
                            """
                        ),
                        {"run_id": result.sector_run_id},
                    )
                ).mappings().all()
                quality = dict(regime["data_quality"] or {})
                membership_modes.add(str(quality.get("membership_mode", "unknown")))
                snapshots.append(
                    {
                        "date": reference_date.isoformat(),
                        "light": regime["market_light"],
                        "multiplier": str(regime["exposure_multiplier"]),
                        "axes": {
                            "trend": regime["trend_state"],
                            "breadth": regime["breadth_state"],
                            "distribution": regime["distribution_state"],
                        },
                    }
                )
                top_by_formula: dict[str, list[tuple[str, float]]] = defaultdict(list)
                for sector in sectors:
                    sector_tiers[str(sector["sector_code"])].append(str(sector["gate_tier"]))
                    evidence = dict(sector["evidence"] or {})
                    for formula in (
                        "champion",
                        "challenger_63_126_50_50",
                        "challenger_42_126_60_40",
                    ):
                        value = evidence.get(formula)
                        if value is not None:
                            top_by_formula[formula].append((str(sector["sector_code"]), float(value)))
                for formula, values in top_by_formula.items():
                    top_five = [code for code, _ in sorted(values, key=lambda item: item[1], reverse=True)[:5]]
                    formula_top_five[formula].append(set(top_five))
                    formula_top_by_date.setdefault(reference_date.isoformat(), {})[formula] = top_five
                    formula_tiers_by_date.setdefault(reference_date.isoformat(), {})[formula] = {
                        item.sector_code: item.raw_tier
                        for item in rank_sector_strength(
                            {code: Decimal(str(value)) for code, value in values}
                        )
                    }
            except Exception as exc:  # one bad historical date must remain visible
                failures.append({"date": reference_date.isoformat(), "error": str(exc)})

        trades = (
            await db.execute(
                text(
                    """
                    SELECT srn.as_of_date, sr.technical_metrics ->> 'industry' AS industry,
                           p.realized_pnl
                    FROM positions p
                    JOIN trade_proposals tp ON tp.id = p.proposal_id
                    JOIN screening_results sr ON sr.id = tp.screening_result_id
                    JOIN scan_runs srn ON srn.id = sr.scan_run_id
                    WHERE p.state = 'closed'
                      AND srn.as_of_date BETWEEN :start_date AND :end_date
                    """
                ),
                {"start_date": args.start, "end_date": args.end},
            )
        ).mappings().all()
        await db.rollback()

    light_counts = Counter(item["light"] for item in snapshots)
    transitions = sum(
        left["light"] != right["light"] for left, right in zip(snapshots, snapshots[1:])
    )
    tier_turnover = {
        sector: sum(left != right for left, right in zip(tiers, tiers[1:]))
        for sector, tiers in sector_tiers.items()
    }
    tier_dwell = {
        sector: dict(Counter(tiers)) for sector, tiers in sector_tiers.items()
    }
    champion = formula_top_five.get("champion", [])
    overlap: dict[str, float | None] = {}
    for formula in ("challenger_63_126_50_50", "challenger_42_126_60_40"):
        challenger = formula_top_five.get(formula, [])
        pairs = list(zip(champion, challenger))
        overlap[formula] = round(sum(len(a & b) / 5 for a, b in pairs) / len(pairs), 4) if pairs else None
    candidate_buckets: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for trade in trades:
        sector = sector_for_industry(trade["industry"])
        if sector is None:
            continue
        for formula, tiers in formula_tiers_by_date.get(
            trade["as_of_date"].isoformat(), {}
        ).items():
            candidate_buckets[formula][tiers.get(sector.code, "unavailable")].append(
                float(trade["realized_pnl"] or 0)
            )
    candidate_outcomes = {
        formula: {
            tier: {
                "closed_trades": len(values),
                "win_rate": round(sum(value > 0 for value in values) / len(values), 4),
                "mean_realized_pnl": round(sum(values) / len(values), 2),
            }
            for tier, values in tiers.items()
        }
        for formula, tiers in candidate_buckets.items()
    }
    report: dict[str, Any] = {
        "policy_version": "market_context_v1",
        "period": {"start": args.start.isoformat(), "end": args.end.isoformat()},
        "membership_modes_observed": sorted(membership_modes),
        "survivorship_bias_warning": (
            "Historical results use current Nifty 500 membership and are survivorship-biased."
            if "current_membership_survivorship_biased" in membership_modes
            else None
        ),
        "sessions_requested": len(dates),
        "sessions_computed": len(snapshots),
        "failures": failures,
        "market_regime": {
            "light_counts": dict(light_counts),
            "light_transitions": transitions,
            "review_windows": {
                name: [item for item in snapshots if start <= dt.date.fromisoformat(item["date"]) <= end]
                for name, (start, end) in REVIEW_WINDOWS.items()
            },
        },
        "sector_comparison": {
            "champion_tier_dwell_sessions": tier_dwell,
            "champion_boundary_turnover_by_sector": tier_turnover,
            "mean_top_five_overlap_with_champion": overlap,
            "representative_rotation_windows": {
                name: {
                    date: formulas
                    for date, formulas in formula_top_by_date.items()
                    if start <= dt.date.fromisoformat(date) <= end
                }
                for name, (start, end) in SECTOR_ROTATION_WINDOWS.items()
            },
            "candidate_outcomes": candidate_outcomes,
            "candidate_outcome_note": (
                "No closed proposal-backed trades overlapped replay dates."
                if not candidate_outcomes
                else "Gross realized P&L only; review immutable journals for charge-adjusted R outcomes."
            ),
        },
        "promotion": "review_only_no_formula_or_threshold_auto_promotion",
    }
    report["report_hash"] = _canonical_hash(report)
    return report


def main() -> None:
    args = _args()
    report = asyncio.run(_run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(report["report_hash"])


if __name__ == "__main__":
    main()
