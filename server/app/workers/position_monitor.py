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

from sqlalchemy import text
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import async_session
from app.redis_pool import create_async_redis
from app.redis_pubsub import consume_pubsub
from app.services.distributed_lease import (
    acquire_distributed_lease,
    release_distributed_lease,
    renew_distributed_lease,
)
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

_LOCK_KEY = "position_monitor:singleton"
_STATUS_KEY = "position_monitor:status"
_LOCK_TTL_SECONDS = 30
_LOCK_REFRESH_SECONDS = 10

REDIS_CHANNEL_TICKS = "ticks"
REDIS_CHANNEL_CONTROLS = "system_controls"
REDIS_STATUS_KEY = _STATUS_KEY
REDIS_STATUS_TTL = _LOCK_TTL_SECONDS
RELOAD_SECONDS = 20

_shutdown = asyncio.Event()


async def _set_status(
    redis,
    status: str,
    worker_id: str | None = None,
    *,
    positions: int = 0,
) -> None:
    payload = {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "positions": positions,
    }
    if worker_id is not None:
        payload["worker_id"] = worker_id
    await redis.set(_STATUS_KEY, json.dumps(payload), ex=_LOCK_TTL_SECONDS)


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


MAX_TICK_AGE_SECONDS = 10.0


class PositionMonitorRuntime:
    def __init__(self, worker_id: str | None = None) -> None:
        self.worker_id = worker_id
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
            await _set_status(
                redis,
                "running",
                worker_id=self.worker_id,
                positions=len(positions),
            )
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

        try:
            ltp = Decimal(str(ltp_raw))
        except (ValueError, TypeError):
            return

        if ltp <= Decimal("0"):
            logger.warning(
                "Position monitor dropped non-positive tick: symbol=%s ltp=%s",
                symbol,
                ltp,
            )
            return

        received_at_str = tick.get("received_at")
        if not received_at_str:
            logger.warning(
                "Position monitor dropped tick with missing received_at: symbol=%s",
                symbol,
            )
            return

        try:
            received_at = datetime.fromisoformat(received_at_str)
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - received_at).total_seconds()
            if age_seconds > MAX_TICK_AGE_SECONDS or age_seconds < -5.0:
                logger.warning(
                    "Position monitor dropped stale/future tick: symbol=%s age=%.2fs",
                    symbol,
                    age_seconds,
                )
                return
        except (ValueError, TypeError):
            logger.warning(
                "Position monitor dropped tick with unparseable received_at: symbol=%s",
                symbol,
            )
            return

        positions = self.positions_by_symbol.get(symbol, [])
        if not positions:
            return
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
                        async with async_session() as submit_db:
                            try:
                                await submit_live_exit_intent(
                                    submit_db,
                                    redis,
                                    order_intent_id=exit_intent_id,
                                )
                            except Exception:
                                logger.exception(
                                    "Failed submitting exit for %s",
                                    exit_intent_id,
                                )
                        logger.info(
                            "Exit triggered for %s (%s) at LTP %s",
                            position.symbol,
                            position.id,
                            ltp,
                        )
                        await self.reload(redis)


async def _heartbeat_loop(redis, runtime: PositionMonitorRuntime, worker_id: str) -> None:
    while not _shutdown.is_set():
        refreshed = await renew_distributed_lease(
            redis,
            _LOCK_KEY,
            worker_id,
            _LOCK_TTL_SECONDS,
        )
        if not refreshed:
            logger.critical("Position monitor lost its singleton lease; stopping.")
            _shutdown.set()
            return
        await _set_status(
            redis,
            "running",
            worker_id=worker_id,
            positions=len(runtime.positions_by_id),
        )
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=_LOCK_REFRESH_SECONDS)
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
    async def handle_message(message: dict) -> None:
        try:
            tick = json.loads(message["data"])
        except (json.JSONDecodeError, TypeError):
            return
        await runtime.handle_tick(redis, tick)

    await consume_pubsub(
        redis,
        [REDIS_CHANNEL_TICKS],
        component="position_monitor",
        handler=handle_message,
        should_stop=_shutdown.is_set,
    )


async def _controls_loop(redis, runtime: PositionMonitorRuntime) -> None:
    async def handle_message(message: dict) -> None:
        await runtime.handle_kill_switch_message(message["data"])

    await consume_pubsub(
        redis,
        [REDIS_CHANNEL_CONTROLS],
        component="position_monitor",
        handler=handle_message,
        should_stop=_shutdown.is_set,
    )


async def run_position_monitor() -> None:
    redis = await create_async_redis()
    worker_id = str(uuid4())

    if not await acquire_distributed_lease(
        redis,
        _LOCK_KEY,
        worker_id,
        _LOCK_TTL_SECONDS,
    ):
        logger.error("Another position monitor owns the singleton lease.")
        await redis.aclose()
        return

    runtime = PositionMonitorRuntime(worker_id=worker_id)

    await _set_status(redis, "running", worker_id=worker_id, positions=0)
    await _emit_system_event("position_monitor_started", {"worker_id": worker_id})
    await runtime.reload(redis)

    heartbeat_task = asyncio.create_task(_heartbeat_loop(redis, runtime, worker_id))
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
        await _set_status(redis, "stopped", worker_id=worker_id)
        await _emit_system_event("position_monitor_stopped", {"worker_id": worker_id})
        await release_distributed_lease(redis, _LOCK_KEY, worker_id)
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
