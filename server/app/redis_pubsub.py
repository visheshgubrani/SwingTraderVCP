"""Reconnecting Redis pub/sub helper for long-lived worker and API loops."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from sqlalchemy import text

from app.database import async_session

logger = logging.getLogger(__name__)

MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]
StopCheck = Callable[[], bool]

_INITIAL_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 30.0
_FAILURES_BEFORE_CRITICAL_EVENT = 3
_STOP_POLL_SECONDS = 0.25


def _is_stopped(should_stop: StopCheck | None) -> bool:
    return bool(should_stop and should_stop())


async def _backoff_sleep(should_stop: StopCheck | None, seconds: float) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        if _is_stopped(should_stop):
            return
        await asyncio.sleep(min(_STOP_POLL_SECONDS, max(0.0, deadline - loop.time())))


async def emit_pubsub_disconnect_event(
    component: str,
    channels: Sequence[str],
    error: str,
) -> None:
    """Persist a critical event when Redis pub/sub stays down. Never logs the URL."""
    try:
        async with async_session() as db:
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
                        :component,
                        'critical',
                        'redis_pubsub_disconnected',
                        CAST(:payload AS jsonb)
                    )
                    """
                ),
                {
                    "component": component,
                    "payload": json.dumps(
                        {
                            "channels": list(channels),
                            "error": error[:500],
                        }
                    ),
                },
            )
            await db.commit()
    except Exception:
        logger.exception(
            "Failed to persist redis pubsub disconnect event for %s", component
        )


async def consume_pubsub(
    redis: Any,
    channels: Sequence[str],
    *,
    component: str,
    handler: MessageHandler,
    should_stop: StopCheck | None = None,
    timeout: float = 1.0,
) -> None:
    """Subscribe, dispatch messages, and resubscribe after connection loss.

    Cancels cleanly. Does not treat ``get_message`` timeouts as failures.
    After several consecutive reconnect failures, emits one critical
    ``system_events`` row until a subscribe succeeds again.
    """
    if not channels:
        raise ValueError("consume_pubsub requires at least one channel")

    backoff = _INITIAL_BACKOFF_SECONDS
    consecutive_failures = 0
    emitted_critical = False

    while not _is_stopped(should_stop):
        pubsub = None
        try:
            pubsub = redis.pubsub()
            await pubsub.subscribe(*channels)
            if consecutive_failures:
                logger.info(
                    "Redis pubsub resubscribed for %s channels=%s",
                    component,
                    list(channels),
                )
            consecutive_failures = 0
            emitted_critical = False
            backoff = _INITIAL_BACKOFF_SECONDS

            while not _is_stopped(should_stop):
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=timeout,
                )
                if not message or message.get("type") != "message":
                    continue
                try:
                    await handler(message)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Redis pubsub handler failed for %s", component
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            consecutive_failures += 1
            logger.exception(
                "Redis pubsub disconnected for %s (failure %d)",
                component,
                consecutive_failures,
            )
            if (
                consecutive_failures >= _FAILURES_BEFORE_CRITICAL_EVENT
                and not emitted_critical
            ):
                await emit_pubsub_disconnect_event(
                    component, channels, f"{type(exc).__name__}: {exc}"
                )
                emitted_critical = True
            if _is_stopped(should_stop):
                break
            await _backoff_sleep(should_stop, backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(*channels)
                except Exception:
                    logger.debug(
                        "Redis pubsub unsubscribe failed for %s",
                        component,
                        exc_info=True,
                    )
                try:
                    close = getattr(pubsub, "aclose", None) or getattr(
                        pubsub, "close", None
                    )
                    if close is not None:
                        await close()
                except Exception:
                    logger.debug(
                        "Redis pubsub close failed for %s",
                        component,
                        exc_info=True,
                    )
