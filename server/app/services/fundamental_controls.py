"""Persistent controls for the non-money P7 pipeline."""

from __future__ import annotations

import json
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ControlName = Literal["processing", "ai"]
CONTROL_KEYS: dict[ControlName, str] = {
    "processing": "fundamentals_processing_paused",
    "ai": "fundamentals_ai_paused",
}


async def get_fundamental_controls(db: AsyncSession) -> dict[str, dict]:
    result = await db.execute(
        text(
            """
            SELECT control_key, enabled, reason, changed_by, changed_at
            FROM system_controls
            WHERE control_key IN ('fundamentals_processing_paused', 'fundamentals_ai_paused')
            """
        )
    )
    rows = {row["control_key"]: dict(row) for row in result.mappings().all()}
    missing = set(CONTROL_KEYS.values()) - set(rows)
    if missing:
        raise RuntimeError(f"Fundamental controls are missing: {', '.join(sorted(missing))}")
    return {
        name: {**rows[key], "paused": bool(rows[key]["enabled"])}
        for name, key in CONTROL_KEYS.items()
    }


async def is_fundamental_control_paused(db: AsyncSession, control: ControlName) -> bool:
    result = await db.execute(
        text("SELECT enabled FROM system_controls WHERE control_key = :control_key"),
        {"control_key": CONTROL_KEYS[control]},
    )
    enabled = result.scalar_one_or_none()
    if enabled is None:
        raise RuntimeError(f"Fundamental {control} control row is missing")
    return bool(enabled)


async def set_fundamental_control(
    db: AsyncSession,
    *,
    control: ControlName,
    paused: bool,
    reason: str,
) -> dict:
    key = CONTROL_KEYS[control]
    result = await db.execute(
        text(
            """
            UPDATE system_controls
            SET enabled = :paused, reason = :reason, changed_by = 'human_ui', changed_at = now()
            WHERE control_key = :control_key
            RETURNING control_key, enabled, reason, changed_by, changed_at
            """
        ),
        {"control_key": key, "paused": paused, "reason": reason},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise RuntimeError(f"Fundamental {control} control row is missing")
    state = {**dict(row), "paused": bool(row["enabled"])}
    await db.execute(
        text(
            """
            INSERT INTO system_events (component, severity, event_type, payload)
            VALUES ('fundamental_controls', :severity, :event_type, CAST(:payload AS jsonb))
            """
        ),
        {
            "severity": "warning" if paused else "info",
            "event_type": f"fundamentals_{control}_{'paused' if paused else 'resumed'}",
            "payload": json.dumps({"control": control, "paused": paused, "reason": reason}),
        },
    )
    return state


async def publish_fundamental_control(redis, *, control: ControlName, state: dict) -> bool:
    receivers = await redis.publish(
        "system_controls",
        json.dumps({"control": f"fundamentals_{control}", **state}, default=str),
    )
    return receivers >= 0
