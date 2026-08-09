#!/usr/bin/env python3
"""
Shadow-compare vcp_score_v2 vs vcp_score_v3 over recent trading days.

Uses already-stored market_candles. Does not flip the live default.

Example:
  cd server && .venv/bin/python -m scripts.shadow_score_v3 --sessions 40 --sample every:5
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

# Allow `python -m scripts.shadow_score_v3` from server/
SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.database import async_session  # noqa: E402
from app.services.indicators import (  # noqa: E402
    attach_rs_line_metrics,
    build_equal_weight_index_closes,
    compute_relative_strength_ratings,
    compute_technical_indicators,
    compute_weighted_performance_score,
)
from app.services.screening_config import TechnicalScreeningConfig  # noqa: E402
from app.services.screening_ranker import rank_and_cap_shortlist  # noqa: E402
from app.services.screener import candle_trading_date  # noqa: E402
from app.services.technical_scoring import evaluate_technical_setup  # noqa: E402


def _grade_counts(survivors: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in survivors:
        grade = ((row.get("technical_metrics") or {}).get("score") or {}).get("grade")
        counts[str(grade or "?")] += 1
    return dict(counts)


def _top_symbols(survivors: list[dict[str, Any]], limit: int = 20) -> list[str]:
    return [row["symbol"] for row in survivors[:limit]]


async def _load_universe_and_candles(window_start: dt.date):
    start_dt = dt.datetime.combine(window_start, dt.time.min, tzinfo=dt.timezone.utc)
    async with async_session() as session:
        instruments = (
            await session.execute(
                text(
                    """
                    SELECT i.id, i.symbol, i.fyers_symbol
                    FROM instruments i
                    JOIN universe_memberships m ON i.id = m.instrument_id
                    WHERE m.universe_code = 'NIFTY500'
                      AND m.member_to IS NULL
                      AND i.active = true
                    ORDER BY i.symbol
                    """
                )
            )
        ).all()
        candles = (
            await session.execute(
                text(
                    """
                    SELECT instrument_id, candle_start, high_price, low_price,
                           close_price, volume
                    FROM market_candles
                    WHERE timeframe = '1d' AND candle_start >= :start_dt
                    ORDER BY instrument_id, candle_start
                    """
                ),
                {"start_dt": start_dt},
            )
        ).all()
        index_rows = (
            await session.execute(
                text(
                    """
                    SELECT c.candle_start, c.close_price
                    FROM market_candles c
                    JOIN instruments i ON i.id = c.instrument_id
                    WHERE i.fyers_symbol = 'NSE:NIFTY500-INDEX'
                      AND c.timeframe = '1d'
                      AND c.candle_start >= :start_dt
                    ORDER BY c.candle_start
                    """
                ),
                {"start_dt": start_dt},
            )
        ).all()

    candles_by_inst: dict[Any, list] = {inst.id: [] for inst in instruments}
    for candle in candles:
        if candle.instrument_id in candles_by_inst:
            candles_by_inst[candle.instrument_id].append(candle)
    return instruments, candles_by_inst, index_rows, start_dt


def _frame_from_candles(candles_list: list) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": c.candle_start,
                "high": float(c.high_price),
                "low": float(c.low_price),
                "close": float(c.close_price),
                "volume": int(c.volume),
            }
            for c in candles_list
        ]
    )


def _as_trading_date(value) -> dt.date:
    if isinstance(value, dt.datetime):
        return candle_trading_date(value)
    if hasattr(value, "to_pydatetime"):
        return candle_trading_date(value.to_pydatetime())
    if isinstance(value, dt.date):
        return value
    return candle_trading_date(pd.Timestamp(value).to_pydatetime())


def _truncate_to_date(df: pd.DataFrame, as_of: dt.date) -> pd.DataFrame:
    mask = [_as_trading_date(value) <= as_of for value in df["date"]]
    return df.loc[mask].reset_index(drop=True)


def _score_universe(
    *,
    instruments,
    candles_by_inst,
    index_rows,
    as_of: dt.date,
    config: TechnicalScreeningConfig,
) -> list[dict[str, Any]]:
    prepared = []
    for inst in instruments:
        candles_list = candles_by_inst.get(inst.id) or []
        if not candles_list:
            continue
        df = _truncate_to_date(_frame_from_candles(candles_list), as_of)
        if len(df) < config.minimum_history_days:
            continue
        if _as_trading_date(df.iloc[-1]["date"]) != as_of:
            continue
        try:
            df_ind = compute_technical_indicators(df, config)
            perf = compute_weighted_performance_score(df_ind)
            prepared.append(
                {
                    "instrument_id": inst.id,
                    "symbol": inst.symbol,
                    "df_ind": df_ind,
                    "perf_score": perf,
                }
            )
        except Exception:
            continue

    if config.pipeline_version == "vcp_score_v3":
        if len(index_rows) >= 60:
            index_closes = pd.Series(
                {
                    candle_trading_date(row.candle_start): float(row.close_price)
                    for row in index_rows
                    if candle_trading_date(row.candle_start) <= as_of
                },
                dtype=float,
            )
            index_closes.index = pd.to_datetime(index_closes.index)
            source = "fyers_index"
        else:
            index_closes = build_equal_weight_index_closes(
                [row["df_ind"] for row in prepared]
            )
            source = "synthetic_equal_weight"
        for row in prepared:
            row["df_ind"] = attach_rs_line_metrics(
                row["df_ind"],
                index_closes,
                lookback_days=config.rs_line_lookback_days,
            )
            row["rs_benchmark_source"] = source
    else:
        for row in prepared:
            row["rs_benchmark_source"] = "not_required"

    rs_ratings = compute_relative_strength_ratings(prepared)
    survivors: list[dict[str, Any]] = []
    for row in prepared:
        scoring = evaluate_technical_setup(
            row["df_ind"],
            rs_rating=rs_ratings.get(row["instrument_id"], 0),
            history_days=len(row["df_ind"]),
            config=config,
        )
        if not scoring["eligible"]:
            continue
        survivors.append(
            {
                "symbol": row["symbol"],
                "technical_score": scoring["score"],
                "rs_rating": rs_ratings.get(row["instrument_id"], 0),
                "pct_from_52w_high": scoring["raw_inputs"]["distance_52w_high_pct"]
                / 100.0,
                "technical_metrics": {
                    "score": {
                        "version": config.pipeline_version,
                        "total": scoring["score"],
                        "grade": scoring["grade"],
                        "components": scoring["components"],
                    },
                    "rs_benchmark_source": row["rs_benchmark_source"],
                },
            }
        )
    return rank_and_cap_shortlist(survivors, config.shortlist_limit)


async def run_shadow(sessions: int, sample_every: int) -> dict[str, Any]:
    window_start = dt.date.today() - dt.timedelta(days=max(450, sessions * 3))
    instruments, candles_by_inst, index_rows, _ = await _load_universe_and_candles(
        window_start
    )

    # Collect trading dates present on a majority of stocks.
    date_counts: Counter[dt.date] = Counter()
    for candles_list in candles_by_inst.values():
        if not candles_list:
            continue
        date_counts[candle_trading_date(candles_list[-1].candle_start)] += 1
        for candle in candles_list[-sessions:]:
            date_counts[candle_trading_date(candle.candle_start)] += 1

    all_dates = sorted(date_counts.keys())
    if not all_dates:
        raise RuntimeError("No candle dates found for shadow run")
    recent = all_dates[-sessions:]
    sample_dates = recent[:: max(1, sample_every)]

    v2 = TechnicalScreeningConfig.for_version("vcp_score_v2")
    v3 = TechnicalScreeningConfig.for_version("vcp_score_v3")
    rows = []
    for as_of in sample_dates:
        s2 = _score_universe(
            instruments=instruments,
            candles_by_inst=candles_by_inst,
            index_rows=index_rows,
            as_of=as_of,
            config=v2,
        )
        s3 = _score_universe(
            instruments=instruments,
            candles_by_inst=candles_by_inst,
            index_rows=index_rows,
            as_of=as_of,
            config=v3,
        )
        top2 = _top_symbols(s2)
        top3 = _top_symbols(s3)
        overlap = len(set(top2) & set(top3))
        rows.append(
            {
                "as_of": as_of.isoformat(),
                "v2_count": len(s2),
                "v3_count": len(s3),
                "v2_grades": _grade_counts(s2),
                "v3_grades": _grade_counts(s3),
                "top20_overlap": overlap,
                "v2_top20": top2,
                "v3_top20": top3,
            }
        )

    report = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sessions_requested": sessions,
        "sample_every": sample_every,
        "sample_dates": [r["as_of"] for r in rows],
        "summary": {
            "avg_v2_count": round(sum(r["v2_count"] for r in rows) / max(len(rows), 1), 2),
            "avg_v3_count": round(sum(r["v3_count"] for r in rows) / max(len(rows), 1), 2),
            "avg_v2_ab": round(
                sum(
                    r["v2_grades"].get("A", 0) + r["v2_grades"].get("B", 0)
                    for r in rows
                )
                / max(len(rows), 1),
                2,
            ),
            "avg_v3_ab": round(
                sum(
                    r["v3_grades"].get("A", 0) + r["v3_grades"].get("B", 0)
                    for r in rows
                )
                / max(len(rows), 1),
                2,
            ),
            "avg_top20_overlap": round(
                sum(r["top20_overlap"] for r in rows) / max(len(rows), 1), 2
            ),
            "default_flip_recommended": False,
            "default_flip_reason": (
                "Eligibility unchanged; review A+B grade band and real "
                "NIFTY500-INDEX RS-line backfill before flipping default."
            ),
        },
        "days": rows,
        "notes": [
            "Live default remains vcp_score_v2 until this report is reviewed.",
            "Eligible count is intentionally identical: v3 changes scoring, not gates.",
            "Use grade A+B (or a lower shortlist_limit) as the actionable shortlist proxy.",
            "Eyeball v3_top20 for setup quality before flipping the default.",
        ],
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=int, default=40)
    parser.add_argument(
        "--sample",
        default="every:5",
        help="Sampling cadence, e.g. every:5 for weekly-ish over daily sessions",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=SERVER_ROOT / "scripts" / "shadow_score_v3_report.json",
    )
    args = parser.parse_args()
    every = 5
    if args.sample.startswith("every:"):
        every = int(args.sample.split(":", 1)[1])

    import asyncio

    report = asyncio.run(run_shadow(args.sessions, every))
    args.out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report["summary"], indent=2))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
