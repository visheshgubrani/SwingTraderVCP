"""arq P7 job: fetch and annotate fundamentals for persisted survivors only."""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.config import settings
from app.database import async_session
from app.services.fundamental_data import (
    FundamentalsAuthError,
    FundamentalsDataUnavailable,
    FundamentalsError,
    UpstoxFundamentalsClient,
    canonical_json_hash,
    normalize_fundamentals,
)
from app.services.fundamental_llm import (
    FundamentalLLMError,
    OpenRouterFundamentalClient,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Survivor:
    result_id: UUID
    scan_run_id: UUID
    instrument_id: UUID
    isin: str | None
    symbol: str
    company_name: str | None


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: UUID
    facts: dict[str, Any]
    fetched_at: datetime.datetime
    latest_annual_period: str | None
    latest_quarterly_period: str | None
    cache_hit: bool


def p7_run_config() -> dict[str, Any]:
    return {
        "enabled": settings.p7_fundamental_pass_enabled,
        "fundamentals_provider": "upstox",
        "statement_type": "consolidated",
        "snapshot_ttl_hours": settings.fundamentals_snapshot_ttl_hours,
        "model_provider": "openrouter",
        "model": settings.openrouter_model,
        "prompt_version": settings.openrouter_prompt_version,
        "reasoning_enabled": True,
        "reasoning_excluded": True,
        "response_schema": "fundamental_verdict_v1",
    }


async def _load_survivors(scan_run_id: str) -> list[Survivor]:
    async with async_session() as db:
        result = await db.execute(
            text(
                """
                SELECT
                    s.id AS result_id,
                    s.scan_run_id,
                    s.instrument_id,
                    i.isin,
                    i.symbol,
                    i.name AS company_name
                FROM screening_results s
                JOIN instruments i ON i.id = s.instrument_id
                WHERE
                    s.scan_run_id = :scan_run_id
                    AND s.technical_passed = true
                ORDER BY s.result_rank ASC NULLS LAST, s.created_at ASC
                """
            ),
            {"scan_run_id": scan_run_id},
        )
        return [
            Survivor(
                result_id=row.result_id,
                scan_run_id=row.scan_run_id,
                instrument_id=row.instrument_id,
                isin=row.isin,
                symbol=row.symbol,
                company_name=row.company_name,
            )
            for row in result.all()
        ]


async def _claim_survivor(result_id: UUID) -> bool:
    async with async_session() as db:
        result = await db.execute(
            text(
                """
                UPDATE screening_results
                SET
                    llm_status = 'running',
                    llm_verdict = NULL,
                    llm_flags = '{}'::jsonb,
                    llm_checked_at = NULL
                WHERE
                    id = :result_id
                    AND llm_status IN ('queued', 'running', 'failed')
                RETURNING id
                """
            ),
            {"result_id": result_id},
        )
        claimed = result.scalar_one_or_none() is not None
        await db.commit()
        return claimed


async def _cached_snapshot(survivor: Survivor) -> Snapshot | None:
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        hours=settings.fundamentals_snapshot_ttl_hours
    )
    async with async_session() as db:
        result = await db.execute(
            text(
                """
                SELECT
                    id,
                    normalized_facts,
                    fetched_at,
                    latest_annual_period,
                    latest_quarterly_period
                FROM fundamental_snapshots
                WHERE
                    instrument_id = :instrument_id
                    AND provider = 'upstox'
                    AND statement_type = 'consolidated'
                    AND fetched_at >= :cutoff
                ORDER BY fetched_at DESC
                LIMIT 1
                """
            ),
            {
                "instrument_id": survivor.instrument_id,
                "cutoff": cutoff,
            },
        )
        row = result.one_or_none()
        if row is None:
            return None
        return Snapshot(
            snapshot_id=row.id,
            facts=dict(row.normalized_facts or {}),
            fetched_at=row.fetched_at,
            latest_annual_period=row.latest_annual_period,
            latest_quarterly_period=row.latest_quarterly_period,
            cache_hit=True,
        )


async def _create_snapshot(
    survivor: Survivor,
    client: UpstoxFundamentalsClient,
) -> Snapshot:
    if not survivor.isin:
        raise FundamentalsDataUnavailable("Instrument has no ISIN")

    raw_bundle = await client.fetch_company_bundle(
        survivor.isin,
        statement_type="consolidated",
    )
    facts = normalize_fundamentals(
        raw_bundle,
        isin=survivor.isin,
        symbol=survivor.symbol,
        company_name=survivor.company_name,
        statement_type="consolidated",
    )
    content_hash = canonical_json_hash(raw_bundle)
    periods = facts.get("periods")
    periods = periods if isinstance(periods, dict) else {}
    fetched_at = datetime.datetime.now(datetime.timezone.utc)

    async with async_session() as db:
        result = await db.execute(
            text(
                """
                INSERT INTO fundamental_snapshots (
                    instrument_id,
                    provider,
                    statement_type,
                    fetched_at,
                    latest_annual_period,
                    latest_quarterly_period,
                    raw_payload,
                    normalized_facts,
                    content_hash
                )
                VALUES (
                    :instrument_id,
                    'upstox',
                    'consolidated',
                    :fetched_at,
                    :latest_annual_period,
                    :latest_quarterly_period,
                    CAST(:raw_payload AS jsonb),
                    CAST(:normalized_facts AS jsonb),
                    :content_hash
                )
                RETURNING id
                """
            ),
            {
                "instrument_id": survivor.instrument_id,
                "fetched_at": fetched_at,
                "latest_annual_period": periods.get("latest_annual"),
                "latest_quarterly_period": periods.get("latest_quarterly"),
                "raw_payload": json.dumps(raw_bundle, separators=(",", ":")),
                "normalized_facts": json.dumps(facts, separators=(",", ":")),
                "content_hash": content_hash,
            },
        )
        snapshot_id = result.scalar_one()
        await db.commit()

    return Snapshot(
        snapshot_id=snapshot_id,
        facts=facts,
        fetched_at=fetched_at,
        latest_annual_period=periods.get("latest_annual"),
        latest_quarterly_period=periods.get("latest_quarterly"),
        cache_hit=False,
    )


async def _get_snapshot(
    survivor: Survivor,
    client: UpstoxFundamentalsClient,
) -> Snapshot:
    cached = await _cached_snapshot(survivor)
    if cached is not None:
        return cached
    return await _create_snapshot(survivor, client)


async def _mark_skipped_missing_isin(survivor: Survivor) -> str:
    flags = {
        "schema_version": "fundamental_verdict_v1",
        "summary": "Fundamental annotation skipped because this instrument has no ISIN.",
        "criteria": [],
        "red_flags": [],
        "missing_data": ["isin"],
        "provenance": {
            "fundamentals_provider": "upstox",
            "statement_type": "consolidated",
        },
    }
    async with async_session() as db:
        await db.execute(
            text(
                """
                UPDATE screening_results
                SET
                    llm_status = 'skipped',
                    llm_verdict = 'uncertain',
                    llm_flags = CAST(:flags AS jsonb),
                    llm_checked_at = now()
                WHERE id = :result_id
                """
            ),
            {
                "result_id": survivor.result_id,
                "flags": json.dumps(flags, separators=(",", ":")),
            },
        )
        await db.commit()
    return "skipped"


async def _mark_succeeded(
    survivor: Survivor,
    *,
    snapshot: Snapshot,
    llm_result: Any,
    llm_client: OpenRouterFundamentalClient,
) -> str:
    verdict = llm_result.verdict
    flags = {
        "schema_version": "fundamental_verdict_v1",
        "summary": verdict.summary,
        "criteria": [
            criterion.model_dump(mode="json") for criterion in verdict.criteria
        ],
        "red_flags": verdict.red_flags,
        "missing_data": sorted(
            set(
                [
                    *verdict.missing_data,
                    *snapshot.facts.get("missing_data", []),
                ]
            )
        ),
        "provenance": {
            "fundamentals_provider": "upstox",
            "statement_type": "consolidated",
            "snapshot_id": str(snapshot.snapshot_id),
            "snapshot_fetched_at": snapshot.fetched_at.isoformat(),
            "latest_annual_period": snapshot.latest_annual_period,
            "latest_quarterly_period": snapshot.latest_quarterly_period,
            "snapshot_cache_hit": snapshot.cache_hit,
        },
        "model": {
            "provider": "openrouter",
            "name": llm_client.model,
            "prompt_version": llm_client.prompt_version,
            "request_id": llm_result.request_id,
            "input_hash": llm_result.input_hash,
            "usage": llm_result.usage,
            "reasoning_excluded": True,
        },
    }
    async with async_session() as db:
        await db.execute(
            text(
                """
                UPDATE screening_results
                SET
                    llm_status = 'succeeded',
                    llm_verdict = :verdict,
                    llm_flags = CAST(:flags AS jsonb),
                    llm_checked_at = now(),
                    fundamental_snapshot_id = :snapshot_id
                WHERE id = :result_id
                """
            ),
            {
                "result_id": survivor.result_id,
                "snapshot_id": snapshot.snapshot_id,
                "verdict": verdict.verdict,
                "flags": json.dumps(flags, separators=(",", ":")),
            },
        )
        await db.commit()
    return "succeeded"


async def _mark_failed(
    survivor: Survivor,
    *,
    error: Exception,
    snapshot: Snapshot | None,
) -> str:
    error_name = type(error).__name__
    flags = {
        "schema_version": "fundamental_verdict_v1",
        "summary": "Fundamental annotation failed; manual review remains available.",
        "criteria": [],
        "red_flags": [],
        "missing_data": [],
        "error": {
            "type": error_name,
            "message": str(error)[:500],
        },
        "provenance": {
            "fundamentals_provider": "upstox",
            "statement_type": "consolidated",
            "snapshot_id": (
                str(snapshot.snapshot_id) if snapshot is not None else None
            ),
        },
    }
    async with async_session() as db:
        await db.execute(
            text(
                """
                UPDATE screening_results
                SET
                    llm_status = 'failed',
                    llm_verdict = NULL,
                    llm_flags = CAST(:flags AS jsonb),
                    llm_checked_at = now(),
                    fundamental_snapshot_id = :snapshot_id
                WHERE id = :result_id
                """
            ),
            {
                "result_id": survivor.result_id,
                "snapshot_id": (
                    snapshot.snapshot_id if snapshot is not None else None
                ),
                "flags": json.dumps(flags, separators=(",", ":")),
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO system_events (
                    component,
                    severity,
                    event_type,
                    correlation_id,
                    instrument_id,
                    payload
                )
                VALUES (
                    'fundamental_pass',
                    :severity,
                    :event_type,
                    :scan_run_id,
                    :instrument_id,
                    CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "severity": (
                    "error" if isinstance(error, FundamentalsAuthError) else "warning"
                ),
                "event_type": (
                    "fundamentals_auth_failed"
                    if isinstance(error, FundamentalsAuthError)
                    else "fundamental_annotation_failed"
                ),
                "scan_run_id": survivor.scan_run_id,
                "instrument_id": survivor.instrument_id,
                "payload": json.dumps(
                    {
                        "screening_result_id": str(survivor.result_id),
                        "symbol": survivor.symbol,
                        "error_type": error_name,
                        "error": str(error)[:500],
                    },
                    separators=(",", ":"),
                ),
            },
        )
        await db.commit()
    return "failed"


async def _process_survivor(
    survivor: Survivor,
    *,
    semaphore: asyncio.Semaphore,
    fundamentals_client: UpstoxFundamentalsClient,
    llm_client: OpenRouterFundamentalClient,
) -> str:
    async with semaphore:
        if not await _claim_survivor(survivor.result_id):
            return "already_terminal"
        if not survivor.isin:
            return await _mark_skipped_missing_isin(survivor)

        snapshot: Snapshot | None = None
        try:
            snapshot = await _get_snapshot(survivor, fundamentals_client)
            llm_result = await llm_client.analyze(snapshot.facts)
            return await _mark_succeeded(
                survivor,
                snapshot=snapshot,
                llm_result=llm_result,
                llm_client=llm_client,
            )
        except (
            FundamentalsError,
            FundamentalLLMError,
            ValueError,
            TypeError,
        ) as exc:
            logger.warning(
                "P7 annotation failed for %s (%s): %s",
                survivor.symbol,
                survivor.result_id,
                exc,
            )
            return await _mark_failed(
                survivor,
                error=exc,
                snapshot=snapshot,
            )


async def _start_job_run(scan_run_id: str, triggered_by: str) -> UUID:
    async with async_session() as db:
        result = await db.execute(
            text(
                """
                INSERT INTO job_runs (
                    job_type,
                    job_key,
                    triggered_by,
                    status,
                    started_at,
                    input_payload
                )
                VALUES (
                    'fundamental_pass',
                    :job_key,
                    :triggered_by,
                    'running',
                    now(),
                    CAST(:input_payload AS jsonb)
                )
                RETURNING id
                """
            ),
            {
                "job_key": f"fundamental_pass:{scan_run_id}",
                "triggered_by": triggered_by,
                "input_payload": json.dumps(
                    {"scan_run_id": scan_run_id, **p7_run_config()},
                    separators=(",", ":"),
                ),
            },
        )
        run_id = result.scalar_one()
        await db.commit()
        return run_id


async def _finish_job_run(
    job_run_id: UUID,
    *,
    status: str,
    result_payload: dict[str, Any],
    error: str | None = None,
) -> None:
    async with async_session() as db:
        await db.execute(
            text(
                """
                UPDATE job_runs
                SET
                    status = :status,
                    completed_at = now(),
                    result_payload = CAST(:result_payload AS jsonb),
                    error_message = :error
                WHERE id = :job_run_id
                """
            ),
            {
                "job_run_id": job_run_id,
                "status": status,
                "result_payload": json.dumps(
                    result_payload,
                    separators=(",", ":"),
                ),
                "error": error,
            },
        )
        await db.commit()


async def _fail_unfinished(scan_run_id: str, message: str) -> None:
    flags = {
        "schema_version": "fundamental_verdict_v1",
        "summary": "Fundamental annotation could not start; manual review remains available.",
        "criteria": [],
        "red_flags": [],
        "missing_data": [],
        "error": {
            "type": "P7ConfigurationError",
            "message": message,
        },
    }
    async with async_session() as db:
        await db.execute(
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
                    AND llm_status IN ('queued', 'running', 'failed')
                """
            ),
            {
                "scan_run_id": scan_run_id,
                "flags": json.dumps(flags, separators=(",", ":")),
            },
        )
        await db.commit()


async def run_fundamental_pass(
    ctx: dict[str, Any],
    scan_run_id: str,
) -> dict[str, Any]:
    """Annotate only the technical survivors already persisted for one scan."""

    triggered_by = str(ctx.get("job_id") or "arq")
    job_run_id = await _start_job_run(scan_run_id, triggered_by)

    if not settings.p7_fundamental_pass_enabled:
        message = "P7_FUNDAMENTAL_PASS_ENABLED is false"
        await _fail_unfinished(scan_run_id, message)
        await _finish_job_run(
            job_run_id,
            status="failed",
            result_payload={"scan_run_id": scan_run_id, "total": 0},
            error=message,
        )
        return {"status": "failed", "error": message}

    missing_secrets = []
    if not settings.upstox_analytics_token:
        missing_secrets.append("UPSTOX_ANALYTICS_TOKEN")
    if not settings.openrouter_api_key:
        missing_secrets.append("OPENROUTER_API_KEY")
    if missing_secrets:
        message = f"Missing required P7 configuration: {', '.join(missing_secrets)}"
        await _fail_unfinished(scan_run_id, message)
        await _finish_job_run(
            job_run_id,
            status="failed",
            result_payload={"scan_run_id": scan_run_id, "total": 0},
            error=message,
        )
        return {"status": "failed", "error": message}

    try:
        survivors = await _load_survivors(scan_run_id)
        fundamentals_client = UpstoxFundamentalsClient(
            analytics_token=settings.upstox_analytics_token,
            base_url=settings.upstox_fundamentals_base_url,
            timeout_seconds=settings.fundamentals_http_timeout_seconds,
            max_attempts=settings.fundamentals_http_max_attempts,
        )
        llm_client = OpenRouterFundamentalClient(
            api_key=settings.openrouter_api_key,
            api_url=settings.openrouter_api_url,
            model=settings.openrouter_model,
            prompt_version=settings.openrouter_prompt_version,
            app_title=settings.openrouter_app_title,
            http_referer=settings.openrouter_http_referer,
            timeout_seconds=settings.fundamentals_http_timeout_seconds,
            max_attempts=settings.fundamentals_http_max_attempts,
            max_tokens=settings.openrouter_max_tokens,
            temperature=settings.openrouter_temperature,
        )
        semaphore = asyncio.Semaphore(settings.fundamentals_max_concurrency)
        outcomes = await asyncio.gather(
            *[
                _process_survivor(
                    survivor,
                    semaphore=semaphore,
                    fundamentals_client=fundamentals_client,
                    llm_client=llm_client,
                )
                for survivor in survivors
            ]
        )
        counts = {
            outcome: outcomes.count(outcome)
            for outcome in sorted(set(outcomes))
        }
        result_payload = {
            "scan_run_id": scan_run_id,
            "total": len(survivors),
            "outcomes": counts,
        }
        await _finish_job_run(
            job_run_id,
            status="succeeded",
            result_payload=result_payload,
        )
        return {"status": "succeeded", **result_payload}
    except Exception as exc:
        logger.exception("P7 fundamental pass crashed for scan %s", scan_run_id)
        await _fail_unfinished(scan_run_id, str(exc)[:500])
        await _finish_job_run(
            job_run_id,
            status="failed",
            result_payload={"scan_run_id": scan_run_id},
            error=str(exc)[:500],
        )
        return {"status": "failed", "error": str(exc)}
