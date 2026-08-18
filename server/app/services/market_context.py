"""Persist versioned deterministic P9 EOD market and sector context."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.domain.p9_market_context import (
    POLICY_VERSION,
    blended_sector_score,
    classify_breadth,
    classify_distribution_count,
    classify_index_trend,
    classify_market_light,
    excess_return,
    exposure_multiplier,
    is_distribution_session,
    majority_light,
    rank_sector_strength,
    typical_turnover,
)
from app.domain.p9_sector_taxonomy import SECTORS, TAXONOMY_VERSION, sector_for_industry


logger = logging.getLogger(__name__)
TREND_SYMBOLS = (
    "NSE:NIFTY50-INDEX",
    "NSE:NIFTY500-INDEX",
    "NSE:NIFTYMIDCAP150-INDEX",
)
BENCHMARK_SYMBOL = "NSE:NIFTY500-INDEX"
FORMULA_VERSION = "sector_rs_63_126_60_40"


@dataclass(frozen=True)
class MarketContextResult:
    reference_eod_date: dt.date
    policy_id: UUID
    policy_version: str
    mode: str
    regime_snapshot_id: UUID
    sector_run_id: UUID
    market_light: str
    exposure_multiplier: Decimal
    source_hash: str
    status: str


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


async def _active_policy(db: AsyncSession) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                """
                SELECT id, version, mode, config, replay_report_hash
                FROM market_context_policies
                WHERE mode IN ('enforced', 'shadow')
                ORDER BY CASE mode WHEN 'enforced' THEN 0 ELSE 1 END, created_at DESC
                LIMIT 1
                """
            )
        )
    ).mappings().one_or_none()
    if row is None:
        raise RuntimeError("No active P9 market-context policy is configured")
    return dict(row)


async def _load_index_series(
    db: AsyncSession, symbols: tuple[str, ...], reference_date: dt.date
) -> dict[str, list[tuple[dt.date, Decimal]]]:
    rows = (
        await db.execute(
            text(
                """
                SELECT i.fyers_symbol,
                       (c.candle_start AT TIME ZONE 'Asia/Kolkata')::date AS trade_date,
                       c.close_price
                FROM instruments i
                JOIN market_candles c ON c.instrument_id = i.id AND c.timeframe = '1d'
                WHERE i.fyers_symbol = ANY(:symbols)
                  AND (c.candle_start AT TIME ZONE 'Asia/Kolkata')::date <= :reference_date
                  AND (c.candle_start AT TIME ZONE 'Asia/Kolkata')::date >= :window_start
                ORDER BY i.fyers_symbol, trade_date
                """
            ),
            {
                "symbols": list(symbols),
                "reference_date": reference_date,
                "window_start": reference_date - dt.timedelta(days=400),
            },
        )
    ).mappings().all()
    output: dict[str, list[tuple[dt.date, Decimal]]] = defaultdict(list)
    for row in rows:
        output[str(row["fyers_symbol"])].append(
            (row["trade_date"], _decimal(row["close_price"]))
        )
    return dict(output)


async def _load_constituent_candles(
    db: AsyncSession, reference_date: dt.date
) -> tuple[list[dict[str, Any]], str, int]:
    has_closed_membership = bool(
        (
            await db.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM universe_memberships
                        WHERE universe_code = 'NIFTY500' AND member_to IS NOT NULL
                    )
                    """
                )
            )
        ).scalar_one()
    )
    rows = (
        await db.execute(
            text(
                """
                SELECT m.instrument_id, i.metadata ->> 'industry' AS industry,
                       (c.candle_start AT TIME ZONE 'Asia/Kolkata')::date AS trade_date,
                       c.high_price, c.low_price, c.close_price, c.volume
                FROM universe_memberships m
                JOIN instruments i ON i.id = m.instrument_id
                JOIN market_candles c ON c.instrument_id = i.id AND c.timeframe = '1d'
                WHERE m.universe_code = 'NIFTY500'
                  AND (
                    (:point_in_time AND m.member_from <= :reference_date
                     AND (m.member_to IS NULL OR m.member_to >= :reference_date))
                    OR (NOT :point_in_time AND m.member_to IS NULL)
                  )
                  AND (c.candle_start AT TIME ZONE 'Asia/Kolkata')::date <= :reference_date
                  AND (c.candle_start AT TIME ZONE 'Asia/Kolkata')::date >= :window_start
                ORDER BY m.instrument_id, trade_date
                """
            ),
            {
                "reference_date": reference_date,
                "window_start": reference_date - dt.timedelta(days=100),
                "point_in_time": has_closed_membership,
            },
        )
    ).mappings().all()
    # Historical membership ranges are considered point-in-time evidence. A
    # current-only import is made explicit in replay output instead of hidden.
    membership_mode = (
        "point_in_time" if has_closed_membership else "current_membership_survivorship_biased"
    )
    expected_members = int(
        (
            await db.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT instrument_id) FROM universe_memberships
                    WHERE universe_code = 'NIFTY500'
                      AND (
                        (:point_in_time AND member_from <= :reference_date
                         AND (member_to IS NULL OR member_to >= :reference_date))
                        OR (NOT :point_in_time AND member_to IS NULL)
                      )
                    """
                ),
                {
                    "reference_date": reference_date,
                    "point_in_time": has_closed_membership,
                },
            )
        ).scalar_one()
    )
    return [dict(row) for row in rows], membership_mode, expected_members


def _trend_evidence(
    series: dict[str, list[tuple[dt.date, Decimal]]], reference_date: dt.date
) -> tuple[str, dict[str, Any], bool]:
    evidence: dict[str, Any] = {}
    states: list[str] = []
    complete = True
    for symbol in TREND_SYMBOLS:
        values = series.get(symbol, [])
        if len(values) < 220 or values[-1][0] != reference_date:
            state = "unavailable"
            close = sma50 = sma200 = sma200_prior = None
            complete = False
        else:
            closes = [value for _, value in values]
            close = closes[-1]
            sma50 = sum(closes[-50:]) / Decimal("50")
            sma200 = sum(closes[-200:]) / Decimal("200")
            sma200_prior = sum(closes[-220:-20]) / Decimal("200")
            state = classify_index_trend(
                close=close,
                sma50=sma50,
                sma200=sma200,
                sma200_20_sessions_ago=sma200_prior,
            )
        states.append(state)
        evidence[symbol] = {
            "state": state,
            "close": close,
            "sma50": sma50,
            "sma200": sma200,
            "sma200_20_sessions_ago": sma200_prior,
            "sessions": len(values),
        }
    return majority_light(states), evidence, complete


def _breadth_and_distribution(
    constituent_rows: list[dict[str, Any]],
    benchmark: list[tuple[dt.date, Decimal]],
    reference_date: dt.date,
    expected_members: int,
) -> tuple[str, Decimal | None, str, int | None, dict[str, Any]]:
    by_instrument: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
    turnover_by_date: dict[dt.date, Decimal] = defaultdict(lambda: Decimal("0"))
    instruments_by_date: dict[dt.date, set[UUID]] = defaultdict(set)
    for row in constituent_rows:
        by_instrument[row["instrument_id"]].append(row)
        turnover_by_date[row["trade_date"]] += typical_turnover(
            high=_decimal(row["high_price"]),
            low=_decimal(row["low_price"]),
            close=_decimal(row["close_price"]),
            volume=int(row["volume"]),
        )
        instruments_by_date[row["trade_date"]].add(row["instrument_id"])

    current_members = 0
    above = 0
    for rows in by_instrument.values():
        if len(rows) < 50 or rows[-1]["trade_date"] != reference_date:
            continue
        closes = [_decimal(row["close_price"]) for row in rows]
        current_members += 1
        if closes[-1] > sum(closes[-50:]) / Decimal("50"):
            above += 1
    breadth_complete = expected_members > 0 and current_members == expected_members
    breadth_pct = (
        Decimal(above * 100) / Decimal(current_members) if current_members else None
    )
    breadth_state = classify_breadth(breadth_pct) if breadth_complete else "unavailable"

    benchmark_map = dict(benchmark)
    dates = [date for date, _ in benchmark if date <= reference_date][-26:]
    dist_count: int | None = None
    distribution_dates: list[str] = []
    turnover_complete = len(dates) == 26 and all(
        len(instruments_by_date[date]) == expected_members for date in dates
    )
    if turnover_complete:
        dist_count = 0
        for previous_date, current_date in zip(dates, dates[1:]):
            if is_distribution_session(
                current_close=benchmark_map[current_date],
                previous_close=benchmark_map[previous_date],
                current_turnover=turnover_by_date[current_date],
                previous_turnover=turnover_by_date[previous_date],
            ):
                dist_count += 1
                distribution_dates.append(current_date.isoformat())
    distribution_state = classify_distribution_count(dist_count)
    evidence = {
        "scoreable_members": current_members,
        "expected_members": expected_members,
        "breadth_complete": breadth_complete,
        "above_sma50": above,
        "breadth_pct": breadth_pct,
        "turnover_method": "typical_price_times_volume",
        "distribution_dates": distribution_dates,
        "turnover_complete": turnover_complete,
    }
    return breadth_state, breadth_pct, distribution_state, dist_count, evidence


def _return_at(
    sector: dict[dt.date, Decimal], benchmark: dict[dt.date, Decimal], dates: list[dt.date], offset: int
) -> Decimal:
    historic_date = dates[-offset - 1]
    current_date = dates[-1]
    return excess_return(
        current=sector[current_date],
        historic=sector[historic_date],
        benchmark_current=benchmark[current_date],
        benchmark_historic=benchmark[historic_date],
    )


async def compute_market_context(
    db: AsyncSession, reference_date: dt.date
) -> MarketContextResult:
    policy = await _active_policy(db)
    symbols = TREND_SYMBOLS + tuple(sector.fyers_symbol for sector in SECTORS)
    index_series = await _load_index_series(db, symbols, reference_date)
    constituents, membership_mode, expected_members = await _load_constituent_candles(
        db, reference_date
    )

    trend_state, trend_evidence, trend_complete = _trend_evidence(
        index_series, reference_date
    )
    benchmark = index_series.get(BENCHMARK_SYMBOL, [])
    breadth_state, breadth_pct, distribution_state, dist_count, broad_evidence = (
        _breadth_and_distribution(
            constituents, benchmark, reference_date, expected_members
        )
    )
    light = classify_market_light(
        trend=trend_state, breadth=breadth_state, distribution=distribution_state
    )
    if not trend_complete:
        light = "unavailable"
    multiplier = exposure_multiplier(light)
    quality = {
        "trend_complete": trend_complete,
        "breadth_available": breadth_state != "unavailable",
        "distribution_available": dist_count is not None,
        "membership_mode": membership_mode,
    }
    source_payload = {
        "reference_date": reference_date,
        "policy_version": policy["version"],
        "index_source_hash": _hash(
            {symbol: index_series.get(symbol, [])[-220:] for symbol in TREND_SYMBOLS}
        ),
        "constituent_source_hash": _hash(
            [
                (
                    row["instrument_id"], row["trade_date"], row["high_price"],
                    row["low_price"], row["close_price"], row["volume"],
                )
                for row in constituents
            ]
        ),
        "trend": trend_evidence,
        "breadth_distribution": broad_evidence,
        "distribution_count": dist_count,
    }
    regime_hash = _hash(source_payload)
    existing_regime = (
        await db.execute(
            text(
                """
                SELECT id FROM market_regime_snapshots
                WHERE reference_eod_date = :reference_date
                  AND market_context_policy_id = :policy_id
                  AND source_hash = :source_hash
                """
            ),
            {
                "reference_date": reference_date,
                "policy_id": policy["id"],
                "source_hash": regime_hash,
            },
        )
    ).scalar_one_or_none()
    regime_id = existing_regime or uuid4()
    if existing_regime is None:
        legacy_regime = {
            "green": "bullish",
            "yellow": "neutral",
            "red": "bearish",
            "unavailable": "unavailable",
        }[light]
        benchmark_price = dict(benchmark).get(reference_date)
        await db.execute(
            text(
                """
                INSERT INTO market_regime_snapshots (
                    id, reference_eod_date, classifier_version, regime,
                    benchmark_symbol, benchmark_price, benchmark_price_source,
                    breadth_above_sma_50_pct, evidence,
                    market_context_policy_id, market_light, exposure_multiplier,
                    trend_state, breadth_state, distribution_state, source_hash,
                    data_quality
                ) VALUES (
                    :id, :reference_date, :classifier_version, :regime,
                    :benchmark_symbol, :benchmark_price, 'eod_close', :breadth,
                    CAST(:evidence AS jsonb), :policy_id, :light, :multiplier,
                    :trend_state, :breadth_state, :distribution_state, :source_hash,
                    CAST(:quality AS jsonb)
                )
                """
            ),
            {
                "id": regime_id,
                "reference_date": reference_date,
                "classifier_version": policy["version"],
                "regime": legacy_regime,
                "benchmark_symbol": BENCHMARK_SYMBOL,
                "benchmark_price": benchmark_price,
                "breadth": breadth_pct,
                "evidence": json.dumps(source_payload, default=str),
                "policy_id": policy["id"],
                "light": light,
                "multiplier": multiplier,
                "trend_state": trend_state,
                "breadth_state": breadth_state,
                "distribution_state": distribution_state,
                "source_hash": regime_hash,
                "quality": json.dumps(quality),
            },
        )

    benchmark_map = dict(benchmark)
    prior_rows = (
        await db.execute(
            text(
                """
                SELECT ssr.sector_code, ssr.raw_tier
                FROM sector_strength_results ssr
                JOIN sector_strength_runs run ON run.id = ssr.run_id
                WHERE run.reference_eod_date < :reference_date
                  AND run.market_context_policy_id = :policy_id
                ORDER BY run.reference_eod_date DESC, run.created_at DESC
                """
            ),
            {"reference_date": reference_date, "policy_id": policy["id"]},
        )
    ).mappings().all()
    previous: dict[str, str] = {}
    for row in prior_rows:
        previous.setdefault(str(row["sector_code"]), str(row["raw_tier"]))

    sector_inputs: dict[str, dict[str, Any]] = {}
    champion_scores: dict[str, Decimal] = {}
    sector_complete = True
    for sector in SECTORS:
        sector_map = dict(index_series.get(sector.fyers_symbol, []))
        common_dates = sorted(set(sector_map) & set(benchmark_map))
        if len(common_dates) < 127 or common_dates[-1] != reference_date:
            sector_complete = False
            sector_inputs[sector.code] = {"status": "unavailable"}
            continue
        ex42 = _return_at(sector_map, benchmark_map, common_dates, 42)
        ex63 = _return_at(sector_map, benchmark_map, common_dates, 63)
        ex126 = _return_at(sector_map, benchmark_map, common_dates, 126)
        champion = blended_sector_score(excess_short=ex63, excess_long=ex126)
        champion_scores[sector.code] = champion
        sector_inputs[sector.code] = {
            "status": "complete",
            "source_hash": _hash(
                [
                    (date, sector_map[date], benchmark_map[date])
                    for date in common_dates[-127:]
                ]
            ),
            "excess_42": ex42,
            "excess_63": ex63,
            "excess_126": ex126,
            "champion": champion,
            "challenger_63_126_50_50": blended_sector_score(
                excess_short=ex63, excess_long=ex126, short_weight=Decimal("0.50")
            ),
            "challenger_42_126_60_40": blended_sector_score(
                excess_short=ex42, excess_long=ex126
            ),
        }
    ranked = {
        item.sector_code: item
        for item in rank_sector_strength(
            champion_scores,
            previous_raw_tiers=previous,  # type: ignore[arg-type]
        )
    }
    sector_payload = {
        "reference_date": reference_date,
        "policy_version": policy["version"],
        "taxonomy_version": TAXONOMY_VERSION,
        "inputs": sector_inputs,
    }
    sector_hash = _hash(sector_payload)
    existing_run = (
        await db.execute(
            text(
                """
                SELECT id, status FROM sector_strength_runs
                WHERE reference_eod_date = :reference_date
                  AND market_context_policy_id = :policy_id
                  AND source_hash = :source_hash
                """
            ),
            {
                "reference_date": reference_date,
                "policy_id": policy["id"],
                "source_hash": sector_hash,
            },
        )
    ).scalar_one_or_none()
    sector_run_id = existing_run or uuid4()
    if existing_run is None:
        await db.execute(
            text(
                """
                INSERT INTO sector_strength_runs (
                    id, reference_eod_date, market_context_policy_id,
                    taxonomy_version, formula_version, source_hash,
                    membership_mode, status, challenger_summary, data_quality
                ) VALUES (
                    :id, :reference_date, :policy_id, :taxonomy_version,
                    :formula_version, :source_hash, :membership_mode, :status,
                    CAST(:challengers AS jsonb), CAST(:quality AS jsonb)
                )
                """
            ),
            {
                "id": sector_run_id,
                "reference_date": reference_date,
                "policy_id": policy["id"],
                "taxonomy_version": TAXONOMY_VERSION,
                "formula_version": FORMULA_VERSION,
                "source_hash": sector_hash,
                "membership_mode": membership_mode,
                "status": "complete" if sector_complete else "unavailable",
                "challengers": json.dumps(
                    {
                        code: {
                            key: str(value)
                            for key, value in inputs.items()
                            if key.startswith("challenger_")
                        }
                        for code, inputs in sector_inputs.items()
                    }
                ),
                "quality": json.dumps(
                    {"available_sectors": len(champion_scores), "expected_sectors": len(SECTORS)}
                ),
            },
        )
        instrument_rows = (
            await db.execute(
                text(
                    """
                    SELECT id, fyers_symbol FROM instruments
                    WHERE fyers_symbol = ANY(:symbols)
                    """
                ),
                {"symbols": [sector.fyers_symbol for sector in SECTORS]},
            )
        ).mappings().all()
        instrument_by_symbol = {
            str(row["fyers_symbol"]): row["id"] for row in instrument_rows
        }
        for sector in SECTORS:
            inputs = sector_inputs[sector.code]
            rank = ranked.get(sector.code)
            await db.execute(
                text(
                    """
                    INSERT INTO sector_strength_results (
                        run_id, sector_code, sector_name, index_instrument_id,
                        index_symbol, excess_return_42, excess_return_63,
                        excess_return_126, blended_score, ordinal_rank, rs_rating,
                        raw_tier, gate_tier, evidence
                    ) VALUES (
                        :run_id, :sector_code, :sector_name, :instrument_id,
                        :index_symbol, :ex42, :ex63, :ex126, :score, :rank,
                        :rating, :raw_tier, :gate_tier, CAST(:evidence AS jsonb)
                    )
                    """
                ),
                {
                    "run_id": sector_run_id,
                    "sector_code": sector.code,
                    "sector_name": sector.name,
                    "instrument_id": instrument_by_symbol.get(sector.fyers_symbol),
                    "index_symbol": sector.fyers_symbol,
                    "ex42": inputs.get("excess_42"),
                    "ex63": inputs.get("excess_63"),
                    "ex126": inputs.get("excess_126"),
                    "score": inputs.get("champion"),
                    "rank": rank.ordinal_rank if rank else None,
                    "rating": rank.rs_rating if rank else None,
                    "raw_tier": rank.raw_tier if rank else "unavailable",
                    "gate_tier": rank.gate_tier if rank else "unavailable",
                    "evidence": json.dumps(inputs, default=str),
                },
            )

    status = "complete" if light != "unavailable" and sector_complete else "unavailable"
    if status == "unavailable":
        await db.execute(
            text(
                """
                INSERT INTO system_events (component, severity, event_type, payload)
                VALUES ('market_context', 'warning', 'p9_context_unavailable', CAST(:payload AS jsonb))
                """
            ),
            {
                "payload": json.dumps(
                    {"reference_eod_date": reference_date.isoformat(), "quality": quality}
                )
            },
        )
    return MarketContextResult(
        reference_eod_date=reference_date,
        policy_id=policy["id"],
        policy_version=str(policy["version"]),
        mode=str(policy["mode"]),
        regime_snapshot_id=regime_id,
        sector_run_id=sector_run_id,
        market_light=light,
        exposure_multiplier=multiplier,
        source_hash=regime_hash,
        status=status,
    )


async def run_market_context(
    ctx: dict[str, Any], reference_date_iso: str, enqueue_personal: bool = True
) -> dict[str, Any]:
    reference_date = dt.date.fromisoformat(reference_date_iso)
    async with async_session() as db:
        result = await compute_market_context(db, reference_date)
        await db.commit()
    if enqueue_personal:
        from app.services.personal_scan import ensure_personal_scan

        await ensure_personal_scan(
            ctx["redis"], triggered_by="p9_eod_chain", as_of_date=reference_date
        )
    return {**asdict(result), "reference_eod_date": reference_date.isoformat()}


async def load_sector_context_for_industries(
    db: AsyncSession, reference_date: dt.date, industries: set[str]
) -> tuple[str, dict[str, dict[str, Any]]]:
    policy = await _active_policy(db)
    run = (
        await db.execute(
            text(
                """
                SELECT id, status FROM sector_strength_runs
                WHERE reference_eod_date = :reference_date
                  AND market_context_policy_id = :policy_id
                ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"reference_date": reference_date, "policy_id": policy["id"]},
        )
    ).mappings().one_or_none()
    if run is None:
        return str(policy["mode"]), {}
    if run["status"] != "complete":
        return str(policy["mode"]), {}
    rows = (
        await db.execute(
            text(
                """
                SELECT id, sector_code, rs_rating, raw_tier, gate_tier
                FROM sector_strength_results WHERE run_id = :run_id
                """
            ),
            {"run_id": run["id"]},
        )
    ).mappings().all()
    by_code = {str(row["sector_code"]): dict(row) for row in rows}
    output: dict[str, dict[str, Any]] = {}
    for industry in industries:
        sector = sector_for_industry(industry)
        if sector is None or sector.code not in by_code:
            output[industry] = {
                "sector_code": None,
                "sector_tier": "unavailable",
                "sector_rs_rating": None,
                "sector_strength_result_id": None,
            }
            continue
        row = by_code[sector.code]
        output[industry] = {
            "sector_code": sector.code,
            "sector_tier": row["raw_tier"],
            "sector_gate_tier": row["gate_tier"],
            "sector_rs_rating": row["rs_rating"],
            "sector_strength_result_id": row["id"],
        }
    return str(policy["mode"]), output
