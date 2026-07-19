import datetime
import json
import logging
from typing import Any, Dict, List

import pandas as pd
from sqlalchemy import text

from app.database import async_session
from app.services.indicators import (
    compute_technical_indicators,
    compute_relative_strength_ratings,
    compute_weighted_performance_score,
    evaluate_minervini_criteria,
    evaluate_vcp_shortlist_criteria,
)
from app.services.screening_config import TechnicalScreeningConfig
from app.services.screening_ranker import rank_and_cap_shortlist

logger = logging.getLogger(__name__)

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

        # 4. Perform calculations and evaluate criteria
        # Step 4a: Prepare DataFrame and calculate technical indicators + performance score for each stock
        prepared_stocks = []
        for inst_id, candles_list in candles_by_inst.items():
            if len(candles_list) < 252:
                # Skip instruments without enough history for 52-week lookback
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

        # Step 4c: Apply ordered gates and retain the full audit trail for each hit.
        survivors = []
        gate_counts = {
            "liquidity": 0,
            "stage_2": 0,
            "pivot": 0,
            "squeeze": 0,
            "volume_dry_up": 0,
        }
        for s in prepared_stocks:
            inst_id = s["instrument_id"]
            inst = s["inst"]
            df_ind = s["df_ind"]
            rs_rating = rs_ratings.get(inst_id, 0)
            
            try:
                _, shortlist_metrics = evaluate_vcp_shortlist_criteria(df_ind, config)
                shortlist_checks = shortlist_metrics.get("criteria_matches", {})
                if not shortlist_checks.get("liquidity_adtv_above_threshold", False):
                    continue
                gate_counts["liquidity"] += 1

                stage_2_passed, stage_2_metrics = evaluate_minervini_criteria(
                    df_ind,
                    rs_rating,
                )
                if not stage_2_passed:
                    continue
                gate_counts["stage_2"] += 1

                if not shortlist_checks.get("pivot_distance_within_threshold", False):
                    continue
                gate_counts["pivot"] += 1

                if not shortlist_checks.get("squeeze_combo", False):
                    continue
                gate_counts["squeeze"] += 1

                if not shortlist_checks.get("volume_dry_up", False):
                    continue
                gate_counts["volume_dry_up"] += 1

                survivors.append({
                    "scan_run_id": scan_run_id,
                    "instrument_id": inst_id,
                    "close_price": stage_2_metrics["close"],
                    "sma_50": stage_2_metrics["sma_50"],
                    "sma_200": stage_2_metrics["sma_200"],
                    "avg_volume_20": stage_2_metrics["avg_volume_20"],
                    "pct_from_52w_high": stage_2_metrics["pct_from_52w_high"],
                    "rs_rating": rs_rating,
                    "technical_metrics": {
                        "sma_150": stage_2_metrics["sma_150"],
                        "sma_200_yesterday": stage_2_metrics["sma_200_yesterday"],
                        "sma_200_prev_22": stage_2_metrics["sma_200_prev_22"],
                        "sma_200_prev_110": stage_2_metrics["sma_200_prev_110"],
                        "high_52w": stage_2_metrics["high_52w"],
                        "low_52w": stage_2_metrics["low_52w"],
                        "rs_rating": rs_rating,
                        "perf_score": float(s["perf_score"]) if not pd.isna(s["perf_score"]) else None,
                        "adtv_crore": shortlist_metrics["adtv_crore"],
                        "atr_10": float(df_ind.iloc[-1]["atr_10"]),
                        "atr_50": float(df_ind.iloc[-1]["atr_50"]),
                        "atr_ratio": shortlist_metrics["atr_ratio"],
                        "atr_ratio_3m_low": shortlist_metrics["atr_ratio_3m_low"],
                        "bb_width": shortlist_metrics["bb_width"],
                        "bb_width_20th_pct": shortlist_metrics["bb_width_20th_pct"],
                        "avg_volume_10": shortlist_metrics["avg_volume_10"],
                        "avg_volume_50": shortlist_metrics["avg_volume_50"],
                        "volume_dry_up_ratio": shortlist_metrics["volume_dry_up_ratio"],
                        "criteria_matches": {
                            **stage_2_metrics["criteria_matches"],
                            **shortlist_checks,
                        },
                    },
                })
            except Exception as eval_err:
                logger.exception("Error evaluating shortlist criteria for %s: %s", inst.symbol, eval_err)

        # Step 4d: Highest RS first; deterministic pivot-distance tie-break; cap the shortlist.
        survivors = rank_and_cap_shortlist(survivors, config.shortlist_limit)
        for survivor in survivors:
            survivor["technical_metrics"] = json.dumps(survivor["technical_metrics"])
            survivor.pop("rs_rating")

        logger.info(
            "Gate counts: liquidity=%s, stage_2=%s, pivot=%s, squeeze=%s, "
            "volume_dry_up=%s. Shortlist retained=%s (cap=%s).",
            gate_counts["liquidity"],
            gate_counts["stage_2"],
            gate_counts["pivot"],
            gate_counts["squeeze"],
            gate_counts["volume_dry_up"],
            len(survivors),
            config.shortlist_limit,
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
                        close_price,
                        sma_50,
                        sma_200,
                        avg_volume_20,
                        pct_from_52w_high,
                        technical_metrics
                    )
                    VALUES (
                        :scan_run_id,
                        :instrument_id,
                        :result_rank,
                        true,
                        :close_price,
                        :sma_50,
                        :sma_200,
                        :avg_volume_20,
                        :pct_from_52w_high,
                        CAST(:technical_metrics AS jsonb)
                    )
                """)
                await session.execute(insert_query, survivors)
                await session.commit()
                logger.info("Saved screening results to database.")

        # 6. Update scan run status to 'succeeded'
        async with async_session() as session:
            success_query = text("""
                UPDATE scan_runs
                SET status = 'succeeded', completed_at = now()
                WHERE id = :scan_run_id
            """)
            await session.execute(success_query, {"scan_run_id": scan_run_id})
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
