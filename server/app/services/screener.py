import asyncio
import datetime
import json
import logging
from typing import Dict, Any, List
import pandas as pd
from sqlalchemy import text
from app.database import async_session
from app.services.indicators import (
    compute_technical_indicators,
    compute_weighted_performance_score,
    compute_relative_strength_ratings,
    evaluate_minervini_criteria
)

logger = logging.getLogger(__name__)

async def run_technical_scan(ctx: Dict[str, Any], scan_run_id: str) -> None:
    """
    Orchestrates the technical screening scan over the active Nifty 500 universe.
    Runs as an arq worker job.
    """
    logger.info(f"Starting technical scan run: {scan_run_id}")
    
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
        logger.info(f"Loaded {len(instruments)} active Nifty 500 instruments.")

        # 3. Fetch candles for all instruments with windowing (trailing 450 calendar days ~ 310 trading days)
        today = datetime.date.today()
        window_start = today - datetime.timedelta(days=450)
        start_dt = datetime.datetime.combine(window_start, datetime.time.min, tzinfo=datetime.timezone.utc)
        
        logger.info(f"Querying daily candles since {window_start}...")
        async with async_session() as session:
            candles_query = text("""
                SELECT instrument_id, candle_start, close_price, volume
                FROM market_candles
                WHERE timeframe = '1d' AND candle_start >= :start_dt
                ORDER BY instrument_id, candle_start ASC
            """)
            candles_res = await session.execute(candles_query, {"start_dt": start_dt})
            all_candles = candles_res.all()

        logger.info(f"Loaded {len(all_candles)} candles. Grouping by instrument...")

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
                'close': float(c.close_price),
                'volume': int(c.volume)
            } for c in candles_list])
            
            try:
                # Compute indicators
                df_ind = compute_technical_indicators(df)
                
                # Compute performance score for cross-sectional ranking
                perf_score = compute_weighted_performance_score(df_ind)
                
                prepared_stocks.append({
                    "instrument_id": inst_id,
                    "inst": inst,
                    "df_ind": df_ind,
                    "perf_score": perf_score
                })
            except Exception as prep_err:
                logger.error(f"Error preparing indicators for {inst.symbol}: {str(prep_err)}")

        # Step 4b: Compute relative strength ratings across the entire prepared universe
        logger.info(f"Prepared {len(prepared_stocks)} stocks. Calculating Relative Strength (RS) ratings...")
        rs_ratings = compute_relative_strength_ratings(prepared_stocks)

        # Step 4c: Evaluate the Minervini Trend Template criteria for each stock
        survivors = []
        for s in prepared_stocks:
            inst_id = s["instrument_id"]
            inst = s["inst"]
            df_ind = s["df_ind"]
            rs_rating = rs_ratings.get(inst_id, 0)
            
            try:
                passed, metrics = evaluate_minervini_criteria(df_ind, rs_rating)
                
                if passed:
                    survivors.append({
                        "scan_run_id": scan_run_id,
                        "instrument_id": inst_id,
                        "close_price": metrics["close"],
                        "sma_50": metrics["sma_50"],
                        "sma_200": metrics["sma_200"],
                        "avg_volume_20": metrics["avg_volume_20"],
                        "pct_from_52w_high": metrics["pct_from_52w_high"],
                        "technical_metrics": json.dumps({
                            "sma_150": metrics["sma_150"],
                            "sma_200_yesterday": metrics["sma_200_yesterday"],
                            "sma_200_prev_22": metrics["sma_200_prev_22"],
                            "sma_200_prev_110": metrics["sma_200_prev_110"],
                            "high_52w": metrics["high_52w"],
                            "low_52w": metrics["low_52w"],
                            "rs_rating": rs_rating,
                            "perf_score": float(s["perf_score"]) if not pd.isna(s["perf_score"]) else None,
                            "criteria_matches": metrics["criteria_matches"]
                        })
                    })
            except Exception as eval_err:
                logger.error(f"Error evaluating Minervini criteria for {inst.symbol}: {str(eval_err)}")

        logger.info(f"Technical scan complete. {len(survivors)} / {len(instruments)} symbols passed.")

        # 5. Insert results in batch
        if survivors:
            async with async_session() as session:
                insert_query = text("""
                    INSERT INTO screening_results (
                        scan_run_id,
                        instrument_id,
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
            
        logger.info(f"Scan run {scan_run_id} completed successfully.")

    except Exception as run_err:
        logger.error(f"Scan run {scan_run_id} failed: {str(run_err)}")
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
