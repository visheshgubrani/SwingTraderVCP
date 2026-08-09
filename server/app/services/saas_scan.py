"""Swyingify global daily Minervini Standard scan orchestration."""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.database import async_session
from app.services.historical_fetcher import latest_completed_eod_date
from app.services.screening_config import merge_template_config
from app.services.screener import run_technical_scan

logger = logging.getLogger(__name__)

FAMILY = "minervini"
CODE = "standard"


async def _load_active_template() -> tuple[UUID, dict[str, Any]] | None:
    async with async_session() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT id, config
                    FROM scan_templates
                    WHERE family = :family
                      AND code = :code
                      AND is_active = true
                    ORDER BY version DESC
                    LIMIT 1
                    """
                ),
                {"family": FAMILY, "code": CODE},
            )
        ).one_or_none()
    if row is None:
        return None
    return row.id, dict(row.config or {})


async def _existing_succeeded_run(
    template_id: UUID,
    as_of_date: datetime.date,
) -> UUID | None:
    async with async_session() as session:
        existing = (
            await session.execute(
                text(
                    """
                    SELECT id
                    FROM scan_runs
                    WHERE template_id = :template_id
                      AND visibility = 'global'
                      AND as_of_date = :as_of_date
                      AND status = 'succeeded'
                    LIMIT 1
                    """
                ),
                {"template_id": template_id, "as_of_date": as_of_date},
            )
        ).scalar_one_or_none()
    return existing


async def _existing_inflight_run(
    template_id: UUID,
    as_of_date: datetime.date,
) -> UUID | None:
    async with async_session() as session:
        existing = (
            await session.execute(
                text(
                    """
                    SELECT id
                    FROM scan_runs
                    WHERE template_id = :template_id
                      AND visibility = 'global'
                      AND as_of_date = :as_of_date
                      AND status IN ('queued', 'running')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"template_id": template_id, "as_of_date": as_of_date},
            )
        ).scalar_one_or_none()
    return existing


async def run_saas_global_standard_scan(
    ctx: dict[str, Any],
    as_of_date: str | None = None,
    triggered_by: str = "eod_chain",
) -> dict[str, Any]:
    """
    Create (or reuse) a global Minervini Standard scan for the EOD date and
    run the shared technical screener. Idempotent per (template, as_of_date).
    """
    target = (
        datetime.date.fromisoformat(as_of_date)
        if as_of_date
        else latest_completed_eod_date()
    )
    template = await _load_active_template()
    if template is None:
        logger.error("No active Minervini Standard scan_templates row; aborting SaaS scan")
        return {"status": "failed", "error": "template_missing"}

    template_id, raw_config = template
    succeeded = await _existing_succeeded_run(template_id, target)
    if succeeded is not None:
        logger.info(
            "SaaS Standard scan already succeeded for %s (run=%s); skipping",
            target.isoformat(),
            succeeded,
        )
        return {
            "status": "already_succeeded",
            "scan_run_id": str(succeeded),
            "as_of_date": target.isoformat(),
        }

    inflight = await _existing_inflight_run(template_id, target)
    if inflight is not None:
        logger.info(
            "SaaS Standard scan already in flight for %s (run=%s); reusing",
            target.isoformat(),
            inflight,
        )
        await run_technical_scan(ctx, str(inflight))
        return {
            "status": "reused_inflight",
            "scan_run_id": str(inflight),
            "as_of_date": target.isoformat(),
        }

    config = merge_template_config(raw_config)
    async with async_session() as session:
        insert = await session.execute(
            text(
                """
                INSERT INTO scan_runs (
                    universe_code,
                    status,
                    triggered_by,
                    technical_config,
                    template_id,
                    visibility,
                    as_of_date
                )
                VALUES (
                    'NIFTY500',
                    'queued',
                    :triggered_by,
                    CAST(:technical_config AS jsonb),
                    :template_id,
                    'global',
                    :as_of_date
                )
                RETURNING id
                """
            ),
            {
                "triggered_by": triggered_by,
                "technical_config": json.dumps(
                    config.model_dump(),
                    separators=(",", ":"),
                ),
                "template_id": template_id,
                "as_of_date": target,
            },
        )
        scan_run_id = insert.scalar_one()
        await session.commit()

    logger.info(
        "Created SaaS Standard scan_run %s for as_of_date=%s",
        scan_run_id,
        target.isoformat(),
    )
    await run_technical_scan(ctx, str(scan_run_id))
    return {
        "status": "completed",
        "scan_run_id": str(scan_run_id),
        "as_of_date": target.isoformat(),
    }
