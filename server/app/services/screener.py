import datetime
import json
import logging
from collections import Counter
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import text

from app.config import settings
from app.database import async_session
from app.services.fundamental_pass import p7_run_config
from app.services.indicators import (
    compute_technical_indicators,
    compute_relative_strength_ratings,
    compute_weighted_performance_score,
)
from app.services.screening_config import TechnicalScreeningConfig
from app.services.screening_ranker import (
    fundamental_selection_status,
    rank_and_cap_shortlist,
)
from app.services.technical_scoring import evaluate_technical_setup

logger = logging.getLogger(__name__)
INDIA_TZ = ZoneInfo("Asia/Kolkata")


def select_reference_eod_date(
    latest_dates: list[datetime.date],
) -> datetime.date | None:
    """Choose the latest date shared by the largest part of the universe."""
    if not latest_dates:
        return None
    date_counts = Counter(latest_dates)
    return max(
        date_counts,
        key=lambda candle_date: (date_counts[candle_date], candle_date),
    )


def candle_trading_date(candle_start: datetime.datetime) -> datetime.date:
    if candle_start.tzinfo is None:
        candle_start = candle_start.replace(tzinfo=datetime.timezone.utc)
    return candle_start.astimezone(INDIA_TZ).date()


async def run_technical_scan(ctx: Dict[str, Any], scan_run_id: str) -> None:
    """
    Orchestrates the technical screening scan over the active Nifty 500 universe.
    Runs as an arq worker job.
    """
    logger.info("Starting technical scan run: %s", scan_run_id)
    
    # 1. Update status to 'running'
    async with async_session() as session:
        update_run_query = text("""
            UPDATE scan_runs
            SET status = 'running', started_at = now()
            WHERE id = :scan_run_id
        """)
        await session.execute(update_run_query, {"scan_run_id": scan_run_id})
        await session.commit()

    try:
        # Load the snapshot saved with this run so every shortlist is reproducible.
        async with async_session() as session:
            config_query = text("""
                SELECT technical_config
                FROM scan_runs
                WHERE id = :scan_run_id
            """)
            config_result = await session.execute(
                config_query,
                {"scan_run_id": scan_run_id},
            )
            config_payload = config_result.scalar_one_or_none() or {}
        config = TechnicalScreeningConfig.model_validate(config_payload)

        # 2. Fetch active universe members
        async with async_session() as session:
            instruments_query = text("""
                SELECT i.id, i.symbol, i.fyers_symbol, i.name
                FROM instruments i
                JOIN universe_memberships m ON i.id = m.instrument_id
                WHERE m.universe_code = 'NIFTY500' AND m.member_to IS NULL AND i.active = true
                ORDER BY i.symbol ASC
            """)
            result = await session.execute(instruments_query)
            instruments = result.all()

        if not instruments:
            raise ValueError("No active Nifty 500 instruments found in database.")

        inst_map = {inst.id: inst for inst in instruments}
        logger.info("Loaded %s active Nifty 500 instruments.", len(instruments))

        # 3. Fetch candles for all instruments with windowing (trailing 450 calendar days ~ 310 trading days)
        today = datetime.date.today()
        window_start = today - datetime.timedelta(days=450)
        start_dt = datetime.datetime.combine(window_start, datetime.time.min, tzinfo=datetime.timezone.utc)
        
        logger.info("Querying daily candles since %s...", window_start)
        async with async_session() as session:
            candles_query = text("""
                SELECT
                    instrument_id,
                    candle_start,
                    high_price,
                    low_price,
                    close_price,
                    volume
                FROM market_candles
                WHERE timeframe = '1d' AND candle_start >= :start_dt
                ORDER BY instrument_id, candle_start ASC
            """)
            candles_res = await session.execute(candles_query, {"start_dt": start_dt})
            all_candles = candles_res.all()

        logger.info("Loaded %s candles. Grouping by instrument...", len(all_candles))

        # Group by instrument_id
        candles_by_inst: Dict[Any, List[Any]] = {inst.id: [] for inst in instruments}
        for candle in all_candles:
            if candle.instrument_id in candles_by_inst:
                candles_by_inst[candle.instrument_id].append(candle)

        latest_dates_by_inst = {
            inst_id: candle_trading_date(candles[-1].candle_start)
            for inst_id, candles in candles_by_inst.items()
            if candles
        }
        reference_eod_date = select_reference_eod_date(
            list(latest_dates_by_inst.values())
        )
        stale_instrument_ids = {
            inst_id
            for inst_id in candles_by_inst
            if latest_dates_by_inst.get(inst_id) != reference_eod_date
        }
        if stale_instrument_ids:
            logger.warning(
                "Skipping %s instruments that are not current through the "
                "reference EOD date %s.",
                len(stale_instrument_ids),
                reference_eod_date,
            )

        # 4. Perform calculations and evaluate criteria
        # Step 4a: Prepare DataFrame and calculate technical indicators + performance score for each stock
        prepared_stocks = []
        for inst_id, candles_list in candles_by_inst.items():
            if inst_id in stale_instrument_ids:
                continue
            if len(candles_list) < config.minimum_history_days:
                # Skip instruments without enough history for the score inputs.
                continue
                
            inst = inst_map[inst_id]
            
            # Convert to DataFrame
            df = pd.DataFrame([{
                'date': c.candle_start,
                'high': float(c.high_price),
                'low': float(c.low_price),
                'close': float(c.close_price),
                'volume': int(c.volume)
            } for c in candles_list])
            
            try:
                # Compute indicators
                df_ind = compute_technical_indicators(df, config)
                
                # Compute performance score for cross-sectional ranking
                perf_score = compute_weighted_performance_score(df_ind)
                
                prepared_stocks.append({
                    "instrument_id": inst_id,
                    "inst": inst,
                    "df_ind": df_ind,
                    "perf_score": perf_score
                })
            except Exception as prep_err:
                logger.exception("Error preparing indicators for %s: %s", inst.symbol, prep_err)

        # Step 4b: Compute relative strength ratings across the entire prepared universe
        logger.info(
            "Prepared %s stocks. Calculating Relative Strength (RS) ratings...",
            len(prepared_stocks),
        )
        rs_ratings = compute_relative_strength_ratings(prepared_stocks)

        # Step 4c: Apply the small eligibility layer, then score every eligible setup.
        survivors = []
        rejection_counts: Counter[str] = Counter()
        for s in prepared_stocks:
            inst_id = s["instrument_id"]
            inst = s["inst"]
            df_ind = s["df_ind"]
            rs_rating = rs_ratings.get(inst_id, 0)
            
            try:
                scoring = evaluate_technical_setup(
                    df_ind,
                    rs_rating=rs_rating,
                    history_days=len(df_ind),
                    config=config,
                )
                if not scoring["eligible"]:
                    for check, passed in scoring.get("eligibility", {}).items():
                        if not passed:
                            rejection_counts[check] += 1
                    continue

                raw_inputs = scoring["raw_inputs"]

                survivors.append({
                    "scan_run_id": scan_run_id,
                    "instrument_id": inst_id,
                    "symbol": inst.symbol,
                    "close_price": raw_inputs["close"],
                    "sma_50": raw_inputs["sma_50"],
                    "sma_200": raw_inputs["sma_200"],
                    "avg_volume_20": int(df_ind.iloc[-1]["avg_volume_20"]),
                    "pct_from_52w_high": (
                        raw_inputs["distance_52w_high_pct"] / 100
                    ),
                    "technical_score": scoring["score"],
                    "rs_rating": rs_rating,
                    "technical_metrics": {
                        "sma_150": raw_inputs["sma_150"],
                        "sma_200_yesterday": float(df_ind.iloc[-1]["sma_200_prev"]),
                        "sma_200_prev_22": raw_inputs["sma_200_prev_22"],
                        "sma_200_prev_110": (
                            float(df_ind.iloc[-1]["sma_200_prev_110"])
                            if not pd.isna(df_ind.iloc[-1]["sma_200_prev_110"])
                            else None
                        ),
                        "high_52w": raw_inputs["high_52w"],
                        "low_52w": raw_inputs["low_52w"],
                        "rs_rating": rs_rating,
                        "perf_score": float(s["perf_score"]) if not pd.isna(s["perf_score"]) else None,
                        "adtv_crore": raw_inputs["adtv_crore"],
                        "atr_10": float(df_ind.iloc[-1]["atr_10"]),
                        "atr_50": float(df_ind.iloc[-1]["atr_50"]),
                        "atr_ratio": raw_inputs["atr_ratio"],
                        "atr_ratio_3m_low": raw_inputs["atr_ratio_3m_low"],
                        "atr_proximity_factor": raw_inputs["atr_proximity_factor"],
                        "bb_width": raw_inputs["bb_width"],
                        "bb_width_20th_pct": float(
                            df_ind.iloc[-1]["bb_width_20th_pct"]
                        ),
                        "bb_width_percentile": raw_inputs["bb_width_percentile"],
                        "avg_volume_10": float(df_ind.iloc[-1]["avg_volume_10"]),
                        "avg_volume_50": float(df_ind.iloc[-1]["avg_volume_50"]),
                        "volume_dry_up_ratio": raw_inputs["volume_dry_up_ratio"],
                        "score": {
                            "version": config.pipeline_version,
                            "total": scoring["score"],
                            "grade": scoring["grade"],
                            "components": scoring["components"],
                        },
                        "eligibility": scoring["eligibility"],
                        "core_checks": scoring["core_checks"],
                        "criteria_matches": scoring["core_checks"],
                    },
                })
            except Exception as eval_err:
                logger.exception("Error evaluating shortlist criteria for %s: %s", inst.symbol, eval_err)

        # Step 4d: Score first, deterministic tie-breaks, and a broad top-50 cap.
        survivors = rank_and_cap_shortlist(survivors, config.shortlist_limit)
        for survivor in survivors:
            fundamental_selected, llm_status = fundamental_selection_status(
                survivor["result_rank"],
                limit=config.fundamental_limit,
                enabled=settings.p7_fundamental_pass_enabled,
            )
            survivor["llm_status"] = llm_status
            survivor["technical_metrics"]["fundamental_selected"] = (
                fundamental_selected
            )
            survivor["technical_metrics"] = json.dumps(survivor["technical_metrics"])
            survivor.pop("rs_rating")
            survivor.pop("symbol")

        logger.info(
            "Technical score eligibility rejections=%s. Ranked setups retained=%s "
            "(cap=%s, fundamental limit=%s).",
            dict(rejection_counts),
            len(survivors),
            config.shortlist_limit,
            config.fundamental_limit,
        )

        # 5. Insert results in batch
        if survivors:
            async with async_session() as session:
                insert_query = text("""
                    INSERT INTO screening_results (
                        scan_run_id,
                        instrument_id,
                        result_rank,
                        technical_passed,
                        technical_score,
                        close_price,
                        sma_50,
                        sma_200,
                        avg_volume_20,
                        pct_from_52w_high,
                        technical_metrics,
                        llm_status
                    )
                    VALUES (
                        :scan_run_id,
                        :instrument_id,
                        :result_rank,
                        true,
                        :technical_score,
                        :close_price,
                        :sma_50,
                        :sma_200,
                        :avg_volume_20,
                        :pct_from_52w_high,
                        CAST(:technical_metrics AS jsonb),
                        :llm_status
                    )
                """)
                await session.execute(insert_query, survivors)
                await session.commit()
                logger.info("Saved screening results to database.")

        # 6. Update scan run status to 'succeeded'
        async with async_session() as session:
            success_query = text("""
                UPDATE scan_runs
                SET
                    status = 'succeeded',
                    completed_at = now(),
                    llm_config = CAST(:llm_config AS jsonb)
                WHERE id = :scan_run_id
            """)
            await session.execute(
                success_query,
                {
                    "scan_run_id": scan_run_id,
                    "llm_config": json.dumps(
                        p7_run_config(technical_rank_limit=config.fundamental_limit)
                    ),
                },
            )
            await session.commit()

        # P7 is intentionally a separate background job. Enqueue failures are
        # recorded on the annotations and never turn a valid technical scan
        # into a failed scan.
        if (
            survivors
            and settings.p7_fundamental_pass_enabled
            and config.fundamental_limit > 0
        ):
            redis = ctx.get("redis")
            try:
                if redis is None:
                    raise RuntimeError("arq Redis context is unavailable")
                await redis.enqueue_job(
                    "run_fundamental_pass",
                    str(scan_run_id),
                    _job_id=f"fundamental-pass:{scan_run_id}",
                )
            except Exception as enqueue_error:
                logger.exception(
                    "Could not enqueue P7 for scan %s",
                    scan_run_id,
                )
                failure_flags = json.dumps(
                    {
                        "schema_version": "fundamental_result_v3",
                        "summary": (
                            "Fundamental annotation could not be queued; "
                            "manual review remains available."
                        ),
                        "criteria": [],
                        "red_flags": [],
                        "missing_data": [],
                        "error": {
                            "type": type(enqueue_error).__name__,
                            "message": str(enqueue_error)[:500],
                        },
                    },
                    separators=(",", ":"),
                )
                async with async_session() as session:
                    await session.execute(
                        text(
                            """
                            UPDATE screening_results
                            SET
                                llm_status = 'failed',
                                llm_verdict = NULL,
                                llm_flags = CAST(:flags AS jsonb),
                                llm_checked_at = now()
                            WHERE
                                scan_run_id = :scan_run_id
                                AND technical_passed = true
                                AND llm_status = 'queued'
                            """
                        ),
                        {
                            "scan_run_id": scan_run_id,
                            "flags": failure_flags,
                        },
                    )
                    await session.execute(
                        text(
                            """
                            INSERT INTO system_events (
                                component,
                                severity,
                                event_type,
                                correlation_id,
                                payload
                            )
                            VALUES (
                                'screener',
                                'warning',
                                'fundamental_pass_enqueue_failed',
                                :scan_run_id,
                                CAST(:payload AS jsonb)
                            )
                            """
                        ),
                        {
                            "scan_run_id": scan_run_id,
                            "payload": json.dumps(
                                {"error": str(enqueue_error)[:500]},
                                separators=(",", ":"),
                            ),
                        },
                    )
                    await session.commit()

        logger.info("Scan run %s completed successfully.", scan_run_id)

    except Exception as run_err:
        logger.exception("Scan run %s failed: %s", scan_run_id, run_err)
        # Update scan run status to 'failed'
        async with async_session() as session:
            fail_query = text("""
                UPDATE scan_runs
                SET status = 'failed', completed_at = now(), error_message = :error
                WHERE id = :scan_run_id
            """)
            await session.execute(fail_query, {
                "scan_run_id": scan_run_id,
                "error": str(run_err)
            })
            await session.commit()
