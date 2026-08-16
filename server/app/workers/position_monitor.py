"""
Position monitor worker — software SL/target/trailing enforcement.

Run as a standalone process:
    python -m app.workers.position_monitor

Subscribes to Redis LTP ticks and evaluates open positions continuously.
Never opens Fyers sockets — only reads Redis pub/sub and calls the execution
engine for exits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from decimal import Decimal

import redis.asyncio as aioredis
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import async_session
from app.services.execution_engine import submit_live_exit_intent
from app.services.position_monitor import (
    MonitoredPosition,
    load_monitored_positions,
    process_position_tick,
    sync_tick_subscriptions,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("position_monitor_worker")

REDIS_CHANNEL_TICKS = "ticks"
REDIS_CHANNEL_CONTROLS = "system_controls"
REDIS_STATUS_KEY = "position_monitor:status"
REDIS_STATUS_TTL = 30
RELOAD_SECONDS = 20

_shutdown = asyncio.Event()


async def _set_status(redis, status: str, *, positions: int = 0) -> None:
    payload = {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "positions": positions,
    }
    await redis.set(REDIS_STATUS_KEY, json.dumps(payload), ex=REDIS_STATUS_TTL)


async def _emit_system_event(event_type: str, payload: dict) -> None:
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
                        'position_monitor',
                        'info',
                        :event_type,
                        CAST(:payload AS jsonb)
                    )
                    """
                ),
                {
                    "event_type": event_type,
                    "payload": json.dumps(payload),
                },
            )
            await db.commit()
    except Exception:
        logger.exception("Failed to persist position monitor system event")


async def _load_kill_switch(db) -> bool:
    result = await db.execute(
        text(
            """
            SELECT enabled
            FROM system_controls
            WHERE control_key = 'global_kill_switch'
            """
        )
    )
    row = result.mappings().one_or_none()
    return bool(row and row["enabled"])


class PositionMonitorRuntime:
    def __init__(self) -> None:
        self.positions_by_symbol: dict[str, list[MonitoredPosition]] = {}
        self.positions_by_id: dict[str, MonitoredPosition] = {}
        self.kill_switch_engaged = False
        self._reload_lock = asyncio.Lock()
        self._tick_lock = asyncio.Lock()

    async def reload(self, redis) -> None:
        async with self._reload_lock:
            async with async_session() as db:
                positions = await load_monitored_positions(db)
                self.kill_switch_engaged = await _load_kill_switch(db)

            self.positions_by_symbol = {}
            self.positions_by_id = {}
            for position in positions:
                self.positions_by_id[str(position.id)] = position
                self.positions_by_symbol.setdefault(position.symbol, []).append(
                    position
                )
            await sync_tick_subscriptions(redis, positions)
            await _set_status(redis, "running", positions=len(positions))
            logger.info(
                "Loaded %d monitored positions (kill switch=%s)",
                len(positions),
                self.kill_switch_engaged,
            )

    async def handle_kill_switch_message(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if payload.get("control_key") != "global_kill_switch":
            return
        self.kill_switch_engaged = bool(payload.get("enabled"))
        logger.warning(
            "Kill switch update received: engaged=%s",
            self.kill_switch_engaged,
        )

    async def handle_tick(self, redis, tick: dict) -> None:
        symbol = tick.get("symbol")
        ltp_raw = tick.get("ltp")
        if not symbol or ltp_raw is None:
            return

        positions = self.positions_by_symbol.get(symbol, [])
        if not positions:
            return

        ltp = Decimal(str(ltp_raw))
        async with self._tick_lock:
            for position in list(positions):
                if position.state not in {"open", "trailing_active"}:
                    continue
                async with async_session() as db:
                    try:
                        exit_intent_id = await process_position_tick(
                            db,
                            position=position,
                            ltp=ltp,
                            kill_switch_engaged=self.kill_switch_engaged,
                        )
                        await db.commit()
                    except Exception:
                        await db.rollback()
                        logger.exception(
                            "Failed processing tick for position %s",
                            position.id,
                        )
                        continue

                    if exit_intent_id is not None:
                        if settings.execution_mode == "live":
                            async with async_session() as submit_db:
                                try:
                                    await submit_live_exit_intent(
                                        submit_db,
                                        redis,
                                        order_intent_id=exit_intent_id,
                                    )
                                except Exception:
                                    logger.exception(
                                        "Failed submitting live exit for %s",
                                        exit_intent_id,
                                    )
                        logger.info(
                            "Exit triggered for %s (%s) at LTP %s",
                            position.symbol,
                            position.id,
                            ltp,
                        )
                        await self.reload(redis)


async def _heartbeat_loop(redis, runtime: PositionMonitorRuntime) -> None:
    while not _shutdown.is_set():
        await _set_status(
            redis,
            "running",
            positions=len(runtime.positions_by_id),
        )
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass


async def _reload_loop(redis, runtime: PositionMonitorRuntime) -> None:
    while not _shutdown.is_set():
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=RELOAD_SECONDS)
            break
        except asyncio.TimeoutError:
            pass
        if _shutdown.is_set():
            break
        await runtime.reload(redis)


async def _ticks_loop(redis, runtime: PositionMonitorRuntime) -> None:
    pubsub = redis.pubsub()
    await pubsub.subscribe(REDIS_CHANNEL_TICKS)
    try:
        while not _shutdown.is_set():
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=1.0,
            )
            if not message or message["type"] != "message":
                continue
            try:
                tick = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            await runtime.handle_tick(redis, tick)
    finally:
        await pubsub.unsubscribe(REDIS_CHANNEL_TICKS)
        await pubsub.close()


async def _controls_loop(redis, runtime: PositionMonitorRuntime) -> None:
    pubsub = redis.pubsub()
    await pubsub.subscribe(REDIS_CHANNEL_CONTROLS)
    try:
        while not _shutdown.is_set():
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=1.0,
            )
            if not message or message["type"] != "message":
                continue
            await runtime.handle_kill_switch_message(message["data"])
    finally:
        await pubsub.unsubscribe(REDIS_CHANNEL_CONTROLS)
        await pubsub.close()


async def run_position_monitor() -> None:
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    runtime = PositionMonitorRuntime()

    await _emit_system_event("position_monitor_started", {})
    await runtime.reload(redis)

    heartbeat_task = asyncio.create_task(_heartbeat_loop(redis, runtime))
    reload_task = asyncio.create_task(_reload_loop(redis, runtime))
    ticks_task = asyncio.create_task(_ticks_loop(redis, runtime))
    controls_task = asyncio.create_task(_controls_loop(redis, runtime))

    try:
        await _shutdown.wait()
    finally:
        for task in (heartbeat_task, reload_task, ticks_task, controls_task):
            task.cancel()
        for task in (heartbeat_task, reload_task, ticks_task, controls_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        await _set_status(redis, "stopped")
        await _emit_system_event("position_monitor_stopped", {})
        await redis.aclose()


def main() -> None:
    def handle_signal(signum, _frame) -> None:
        logger.info("Received signal %s, shutting down...", signum)
        _shutdown.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    asyncio.run(run_position_monitor())


if __name__ == "__main__":
    main()
