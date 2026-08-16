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
    attach_rs_line_metrics,
    build_equal_weight_index_closes,
    compute_technical_indicators,
    compute_relative_strength_ratings,
    compute_weighted_performance_score,
)
from app.services.screening_config import TechnicalScreeningConfig
from app.services.screening_ranker import (
    apply_fundamental_industry_cap,
    rank_and_cap_shortlist,
)
from app.services.scan_readiness import (
    evaluate_scan_readiness,
    scan_readiness_error,
)
from app.services.technical_scoring import evaluate_technical_setup
from app.services.proposal_queue import enqueue_proposal_batch
from app.services.market_context import load_sector_context_for_industries
from app.domain.p9_market_context import contextual_selection_order

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


async def load_rs_benchmark_closes(
    *,
    start_dt: datetime.datetime,
    fyers_symbol: str,
    prepared_frames: list[pd.DataFrame],
) -> tuple[pd.Series, str, str]:
    """
    Load Nifty 500 index closes for RS-line; fall back to equal-weight synthetic.

    Returns (close_series, source, symbol_label).
    """
    async with async_session() as session:
        result = await session.execute(
            text(
                """
                SELECT c.candle_start, c.close_price
                FROM market_candles c
                JOIN instruments i ON i.id = c.instrument_id
                WHERE i.fyers_symbol = :fyers_symbol
                  AND c.timeframe = '1d'
                  AND c.candle_start >= :start_dt
                ORDER BY c.candle_start ASC
                """
            ),
            {"fyers_symbol": fyers_symbol, "start_dt": start_dt},
        )
        rows = result.all()

    if len(rows) >= 60:
        closes = pd.Series(
            {
                candle_trading_date(row.candle_start): float(row.close_price)
                for row in rows
            },
            dtype=float,
        )
        closes.index = pd.to_datetime(closes.index)
        return closes, "fyers_index", fyers_symbol

    logger.warning(
        "RS benchmark %s has insufficient candles (%s); using equal-weight synthetic.",
        fyers_symbol,
        len(rows),
    )
    synthetic = build_equal_weight_index_closes(prepared_frames)
    if synthetic.empty:
        raise ValueError(
            f"Unable to build RS benchmark: no {fyers_symbol} candles and "
            "synthetic equal-weight index is empty. Register the index in "
            "instruments and re-run historical sync (see migration 009 / schema seeds)."
        )
    return synthetic, "synthetic_equal_weight", "SYNTHETIC:NIFTY500-EW"


async def run_technical_scan(ctx: Dict[str, Any], scan_run_id: str) -> None:
    """
    Orchestrates the technical screening scan over the active Nifty 500 universe.
    Runs as an arq worker job.
    """
    logger.info("Starting technical scan run: %s", scan_run_id)
    
    # Atomically claim queued work. Duplicate ARQ deliveries become no-ops.
    async with async_session() as session:
        claim_result = await session.execute(text("""
            UPDATE scan_runs
            SET
                status = 'running',
                started_at = now(),
                completed_at = NULL,
                error_message = NULL
            WHERE id = :scan_run_id AND status = 'queued'
            RETURNING technical_config, as_of_date, visibility
        """), {"scan_run_id": scan_run_id})
        claimed_run = claim_result.one_or_none()
        await session.commit()

    if claimed_run is None:
        logger.info(
            "Ignoring technical scan delivery for %s because it is not queued.",
            scan_run_id,
        )
        return

    try:
        # The claim returns the immutable run snapshot used for this execution.
        config = TechnicalScreeningConfig.model_validate(
            claimed_run.technical_config or {}
        )

        # 2. Fetch active universe members
        async with async_session() as session:
            instruments_query = text("""
                SELECT i.id, i.symbol, i.fyers_symbol, i.name,
                       i.metadata ->> 'industry' AS industry
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
        reference_limit = claimed_run.as_of_date or datetime.date.today()
        window_start = reference_limit - datetime.timedelta(days=450)
        start_dt = datetime.datetime.combine(window_start, datetime.time.min, tzinfo=datetime.timezone.utc)
        end_dt = datetime.datetime.combine(
            reference_limit + datetime.timedelta(days=1),
            datetime.time.min,
            tzinfo=datetime.timezone.utc,
        )
        
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
                WHERE timeframe = '1d'
                  AND candle_start >= :start_dt
                  AND candle_start < :end_dt
                ORDER BY instrument_id, candle_start ASC
            """)
            candles_res = await session.execute(
                candles_query,
                {"start_dt": start_dt, "end_dt": end_dt},
            )
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
        if reference_eod_date is None:
            raise ValueError("No Nifty 500 EOD candles are available to scan.")
        if (
            claimed_run.as_of_date is not None
            and reference_eod_date != claimed_run.as_of_date
        ):
            raise ValueError(
                "Candle coverage does not match the scan's persisted EOD date "
                f"({reference_eod_date} != {claimed_run.as_of_date})."
            )
        readiness = evaluate_scan_readiness(
            (
                (
                    len(candles_by_inst[inst_id]),
                    latest_dates_by_inst.get(inst_id),
                )
                for inst_id in candles_by_inst
            ),
            reference_eod_date=reference_eod_date,
            minimum_history_days=config.minimum_history_days,
        )
        if not readiness.scanner_ready:
            raise ValueError(scan_readiness_error(readiness))
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

        if len(prepared_stocks) < readiness.required_scoreable_instruments:
            raise ValueError(
                "Indicator preparation reduced the scoreable Nifty 500 universe "
                f"to {len(prepared_stocks)}/{readiness.active_instruments}; "
                f"{readiness.required_scoreable_instruments} required (95%)."
            )

        index_closes: pd.Series | None = None
        rs_benchmark_source = "not_required"
        rs_benchmark_symbol = config.rs_benchmark_symbol
        if config.pipeline_version == "vcp_score_v3":
            index_closes, rs_benchmark_source, rs_benchmark_symbol = (
                await load_rs_benchmark_closes(
                    start_dt=start_dt,
                    fyers_symbol=config.rs_benchmark_symbol,
                    prepared_frames=[s["df_ind"] for s in prepared_stocks],
                )
            )
            for prepared in prepared_stocks:
                prepared["df_ind"] = attach_rs_line_metrics(
                    prepared["df_ind"],
                    index_closes,
                    lookback_days=config.rs_line_lookback_days,
                )

        # Step 4b: Compute relative strength ratings across the entire prepared universe
        logger.info(
            "Prepared %s stocks. Calculating Relative Strength (RS) ratings "
            "(pipeline=%s rs_benchmark=%s source=%s)...",
            len(prepared_stocks),
            config.pipeline_version,
            rs_benchmark_symbol,
            rs_benchmark_source,
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
                    "industry": inst.industry,
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
                        "up_down_volume_ratio": (
                            float(raw_inputs["up_down_volume_ratio"])
                            if raw_inputs.get("up_down_volume_ratio") is not None
                            and not pd.isna(raw_inputs.get("up_down_volume_ratio"))
                            else (
                                float(df_ind.iloc[-1]["up_down_volume_ratio"])
                                if "up_down_volume_ratio" in df_ind.columns
                                and not pd.isna(df_ind.iloc[-1]["up_down_volume_ratio"])
                                else None
                            )
                        ),
                        "pocket_pivot_age": raw_inputs.get("pocket_pivot_age"),
                        "rs_line": (
                            float(df_ind.iloc[-1]["rs_line"])
                            if "rs_line" in df_ind.columns
                            and not pd.isna(df_ind.iloc[-1]["rs_line"])
                            else None
                        ),
                        "rs_line_high_52w": (
                            float(df_ind.iloc[-1]["rs_line_high_52w"])
                            if "rs_line_high_52w" in df_ind.columns
                            and not pd.isna(df_ind.iloc[-1]["rs_line_high_52w"])
                            else None
                        ),
                        "rs_line_pct_off_high": (
                            float(raw_inputs["rs_line_pct_off_high"])
                            if raw_inputs.get("rs_line_pct_off_high") is not None
                            and not pd.isna(raw_inputs.get("rs_line_pct_off_high"))
                            else None
                        ),
                        "rs_benchmark_symbol": rs_benchmark_symbol,
                        "rs_benchmark_source": rs_benchmark_source,
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
        context_mode = "unavailable"
        sector_context: dict[str, dict[str, Any]] = {}
        if claimed_run.visibility == "personal":
            async with async_session() as session:
                context_mode, sector_context = await load_sector_context_for_industries(
                    session,
                    reference_eod_date,
                    {str(item.get("industry") or "") for item in survivors},
                )
        for survivor in survivors:
            context = sector_context.get(str(survivor.get("industry") or ""), {})
            survivor["sector_code"] = context.get("sector_code")
            survivor["sector_tier"] = context.get("sector_tier", "unavailable")
            survivor["sector_gate_tier"] = context.get(
                "sector_gate_tier", "unavailable"
            )
            survivor["sector_rs_rating"] = context.get("sector_rs_rating")
            survivor["sector_strength_result_id"] = context.get(
                "sector_strength_result_id"
            )

        contextual = contextual_selection_order(survivors)
        contextual_by_instrument = {
            item["instrument_id"]: item for item in contextual
        }
        counterfactual = apply_fundamental_industry_cap(
            contextual,
            limit=config.fundamental_limit,
            industry_cap=config.fundamental_industry_cap,
            enabled=settings.p7_fundamental_pass_enabled,
        )
        counterfactual_by_instrument = {
            item["instrument_id"]: item for item in counterfactual
        }
        actual_order = (
            contextual
            if claimed_run.visibility == "personal" and context_mode == "enforced"
            else survivors
        )
        survivors = apply_fundamental_industry_cap(
            actual_order,
            limit=config.fundamental_limit,
            industry_cap=config.fundamental_industry_cap,
            enabled=settings.p7_fundamental_pass_enabled,
        )
        # Persistence and public technical ordering remain byte-for-byte based
        # on result_rank, regardless of the separate contextual selection order.
        survivors.sort(key=lambda item: int(item["result_rank"]))
        missing_industries = [
            {
                "scan_run_id": scan_run_id,
                "instrument_id": survivor["instrument_id"],
                "symbol": survivor["symbol"],
                "industry_key": survivor["industry_key"],
            }
            for survivor in survivors
            if survivor["industry_key"].startswith("unknown:")
        ]
        for survivor in survivors:
            contextual_item = contextual_by_instrument[survivor["instrument_id"]]
            would_item = counterfactual_by_instrument[survivor["instrument_id"]]
            survivor["contextual_selection_rank"] = contextual_item[
                "contextual_selection_rank"
            ]
            survivor["technical_metrics"]["fundamental_selected"] = (
                survivor["fundamental_selected"]
            )
            survivor["technical_metrics"]["industry"] = survivor["industry"]
            survivor["technical_metrics"]["industry_key"] = survivor["industry_key"]
            survivor["technical_metrics"]["fundamental_selection_rank"] = (
                survivor["fundamental_selection_rank"]
            )
            survivor["technical_metrics"]["fundamental_cap_exclusion_reason"] = (
                survivor["fundamental_cap_exclusion_reason"]
            )
            survivor["technical_metrics"]["market_context_mode"] = context_mode
            survivor["technical_metrics"]["sector_code"] = survivor["sector_code"]
            survivor["technical_metrics"]["sector_tier"] = survivor["sector_tier"]
            survivor["technical_metrics"]["sector_gate_tier"] = survivor[
                "sector_gate_tier"
            ]
            survivor["technical_metrics"]["sector_rs_rating"] = survivor[
                "sector_rs_rating"
            ]
            survivor["technical_metrics"]["contextual_selection_rank"] = survivor[
                "contextual_selection_rank"
            ]
            survivor["technical_metrics"]["p9_would_fundamental_select"] = would_item[
                "fundamental_selected"
            ]
            survivor["technical_metrics"]["p9_would_exclusion_reason"] = would_item[
                "fundamental_cap_exclusion_reason"
            ]
            survivor["technical_metrics"] = json.dumps(survivor["technical_metrics"])
            survivor.pop("rs_rating")
            survivor.pop("symbol")
            survivor.pop("industry")
            survivor.pop("industry_key")
            survivor.pop("fundamental_selected")
            survivor.pop("fundamental_selection_rank")
            survivor.pop("fundamental_cap_exclusion_reason")
            survivor.pop("sector_code")
            survivor.pop("sector_tier")
            survivor.pop("sector_gate_tier")
            survivor.pop("sector_rs_rating")

        logger.info(
            "Technical score eligibility rejections=%s. Ranked setups retained=%s "
            "(cap=%s, fundamental limit=%s).",
            dict(rejection_counts),
            len(survivors),
            config.shortlist_limit,
            config.fundamental_limit,
        )

        # Persist the complete result set and terminal run status atomically.
        async with async_session() as session:
            await session.execute(
                text("DELETE FROM screening_results WHERE scan_run_id = :scan_run_id"),
                {"scan_run_id": scan_run_id},
            )
            if survivors:
                insert_query = text("""
                    INSERT INTO screening_results (
                        scan_run_id,
                        instrument_id,
                        result_rank,
                        technical_passed,
                        technical_score,
                        sector_strength_result_id,
                        contextual_selection_rank,
                        close_price,
                        sma_50,
                        sma_200,
                        avg_volume_20,
                        pct_from_52w_high,
                        technical_metrics,
                        llm_status,
                        fundamental_status,
                        ai_status
                    )
                    VALUES (
                        :scan_run_id,
                        :instrument_id,
                        :result_rank,
                        true,
                        :technical_score,
                        :sector_strength_result_id,
                        :contextual_selection_rank,
                        :close_price,
                        :sma_50,
                        :sma_200,
                        :avg_volume_20,
                        :pct_from_52w_high,
                        CAST(:technical_metrics AS jsonb),
                        :llm_status,
                        CASE WHEN :llm_status = 'queued' THEN 'queued' ELSE 'not_requested' END,
                        CASE WHEN :llm_status = 'queued' THEN 'queued' ELSE 'not_requested' END
                    )
                """)
                await session.execute(insert_query, survivors)
            if missing_industries:
                await session.execute(
                    text(
                        """
                        INSERT INTO system_events (
                            component, severity, event_type, correlation_id,
                            instrument_id, payload
                        )
                        VALUES (
                            'screener', 'warning', 'missing_industry_metadata',
                            :scan_run_id, :instrument_id,
                            jsonb_build_object(
                                'symbol', :symbol,
                                'industry_key', :industry_key
                            )
                        )
                        """
                    ),
                    missing_industries,
                )
            success_query = text("""
                UPDATE scan_runs
                SET
                    status = 'succeeded',
                    completed_at = now(),
                    llm_config = CAST(:llm_config AS jsonb)
                WHERE id = :scan_run_id AND status = 'running'
            """)
            success_result = await session.execute(
                success_query,
                {
                    "scan_run_id": scan_run_id,
                    "llm_config": json.dumps(
                        p7_run_config(
                            technical_rank_limit=config.fundamental_limit,
                            industry_cap=config.fundamental_industry_cap,
                        )
                    ),
                },
            )
            if success_result.rowcount != 1:
                raise RuntimeError(
                    "Scan run left the running state before results were committed."
                )
            await session.commit()
            logger.info("Saved screening results to database.")

        # P7 is intentionally a separate background job. Enqueue failures are
        # recorded on the annotations and never turn a valid technical scan
        # into a failed scan.
        proposal_should_enqueue_now = not (
            settings.p7_fundamental_pass_enabled and config.fundamental_limit > 0
        )
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
                proposal_should_enqueue_now = True
                logger.exception(
                    "Could not enqueue P7 for scan %s",
                    scan_run_id,
                )
                failure_flags = json.dumps(
                    {
                        "schema_version": "fundamental_result_v4",
                        "summary": (
                            "Fundamental analysis could not be queued; "
                            "manual review remains available."
                        ),
                        "criteria": [],
                        "red_flags": [],
                        "missing_data": [],
                        "fundamental_error": {
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
                                fundamental_status = 'failed',
                                ai_status = 'skipped',
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

        if survivors and proposal_should_enqueue_now and settings.proposal_automation_enabled:
            redis = ctx.get("redis")
            if redis is None:
                logger.error("P10 proposal batch was not queued: arq Redis context unavailable")
            else:
                try:
                    await enqueue_proposal_batch(redis, str(scan_run_id))
                except Exception:
                    logger.exception("Could not enqueue P10 proposal batch for scan %s", scan_run_id)

        logger.info("Scan run %s completed successfully.", scan_run_id)

    except Exception as run_err:
        logger.exception("Scan run %s failed: %s", scan_run_id, run_err)
        # Update scan run status to 'failed'
        async with async_session() as session:
            fail_query = text("""
                UPDATE scan_runs
                SET status = 'failed', completed_at = now(), error_message = :error
                WHERE id = :scan_run_id AND status = 'running'
            """)
            await session.execute(fail_query, {
                "scan_run_id": scan_run_id,
                "error": str(run_err)
            })
            await session.commit()
