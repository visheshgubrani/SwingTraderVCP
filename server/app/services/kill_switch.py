import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.trading import KillSwitchView


class KillSwitchUnavailableError(RuntimeError):
    pass


async def get_kill_switch(db: AsyncSession) -> KillSwitchView:
    result = await db.execute(
        text(
            """
            SELECT control_key, enabled, reason, changed_by, changed_at
            FROM system_controls
            WHERE control_key = 'global_kill_switch'
            """
        )
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise KillSwitchUnavailableError(
            "Global kill switch row is missing; execution remains fail-closed."
        )
    return KillSwitchView.model_validate(dict(row))


async def update_kill_switch(
    db: AsyncSession,
    *,
    enabled: bool,
    reason: str,
) -> KillSwitchView:
    result = await db.execute(
        text(
            """
            UPDATE system_controls
            SET
                enabled = :enabled,
                reason = :reason,
                changed_by = 'human_ui',
                changed_at = now()
            WHERE control_key = 'global_kill_switch'
            RETURNING control_key, enabled, reason, changed_by, changed_at
            """
        ),
        {"enabled": enabled, "reason": reason},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise KillSwitchUnavailableError(
            "Global kill switch row is missing; refusing to create it implicitly."
        )

    await db.execute(
        text(
            """
            INSERT INTO system_events (
                component,
                severity,
                event_type,
                payload
            )
            VALUES (
                'kill_switch_service',
                :severity,
                :event_type,
                CAST(:payload AS jsonb)
            )
            """
        ),
        {
            "severity": "warning" if enabled else "info",
            "event_type": (
                "global_kill_switch_engaged"
                if enabled
                else "global_kill_switch_disengaged"
            ),
            "payload": json.dumps(
                {
                    "enabled": enabled,
                    "reason": reason,
                    "changed_by": "human_ui",
                }
            ),
        },
    )
    return KillSwitchView.model_validate(dict(row))


async def publish_kill_switch(redis, state: KillSwitchView) -> bool:
    message = state.model_dump(mode="json")
    receivers = await redis.publish("system_controls", json.dumps(message))
    return receivers >= 0

