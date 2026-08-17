"""
Tick ingestion worker — the ONLY component that opens the Fyers market-data WebSocket.

Run as a standalone process:
    python -m app.workers.tick_worker

Flow:
    1. Obtain valid access token from auth_service (Redis-cached)
    2. Connect FyersDataSocket (SDK, binary protocol, threaded)
    3. Subscribe to initial symbol set (open positions ∪ watchlist)
    4. On each tick: publish to Redis channel `ticks`, update LTP cache
    5. Listen for subscription changes via Redis channel `tick_subs`
    6. On auth failure: emit system_event, pause, retry with fresh token

Single Fyers market-data WS per AGENTS.md §10.1.
"""

import asyncio
import datetime
import json
import logging
import os
import signal
import sys
import threading
import time
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import uuid4

# Add parent to path so we can import app.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import async_session
from app.domain.market_regime import BENCHMARK_SYMBOL
from app.services.auth_service import get_valid_access_token, AuthUnavailableError
from app.redis_pool import create_async_redis
from app.redis_pubsub import consume_pubsub
from app.services.distributed_lease import (
    acquire_distributed_lease,
    release_distributed_lease,
    renew_distributed_lease,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("tick_worker")

# Redis keys / channels
_LOCK_KEY = "tick_worker:singleton"
_STATUS_KEY = "tick_worker:status"
_LOCK_TTL_SECONDS = 30
_LOCK_REFRESH_SECONDS = 10

REDIS_CHANNEL_TICKS = "ticks"
REDIS_CHANNEL_SUBS = "tick_subs"
REDIS_LTP_PREFIX = "ltp:"
REDIS_LTP_TTL = 60  # seconds — refreshed on every tick
REDIS_WORKER_STATUS_KEY = _STATUS_KEY
REDIS_WORKER_SYMBOLS_KEY = "tick_worker:symbols"

# Shutdown flag
_shutdown = threading.Event()


class TickWorkerState:
    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        self.status = "connecting"  # connecting, ready, degraded, stopped
        self.is_connected = False
        self.symbols: set[str] = set()


def _on_message_factory(
    publish_queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
    state: TickWorkerState,
):
    """Return a thread-safe on_message callback that enqueues ticks for async Redis publish."""

    def on_message(message: dict):
        try:
            # SDK delivers parsed dict: {ltp, symbol, vol_traded_today, ...}
            if not isinstance(message, dict):
                return
            symbol = message.get("symbol")
            ltp_raw = message.get("ltp")
            if not symbol or ltp_raw is None:
                return

            ltp = float(ltp_raw)
            if ltp <= 0:
                logger.warning("Dropped non-positive tick LTP: symbol=%s ltp=%s", symbol, ltp)
                return

            tick = {
                "symbol": symbol,
                "ltp": ltp,
                "volume": message.get("vol_traded_today"),
                "bid": message.get("bid_price"),
                "ask": message.get("ask_price"),
                "open": message.get("open_price"),
                "high": message.get("high_price"),
                "low": message.get("low_price"),
                "prev_close": message.get("prev_close_price"),
                "change": message.get("ch"),
                "change_pct": message.get("chp"),
                "timestamp": message.get("last_traded_time") or message.get("exch_feed_time"),
                "received_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }

            def put() -> None:
                try:
                    publish_queue.put_nowait(tick)
                except asyncio.QueueFull:
                    logger.critical("Tick queue is full; event was dropped.")
                    state.status = "degraded"

            loop.call_soon_threadsafe(put)
        except Exception as e:
            logger.error("on_message error: %s", e)

    return on_message


def _on_connect_factory(state: TickWorkerState, connected_event: threading.Event):
    def on_connect():
        logger.info("Fyers WS connected")
        state.is_connected = True
        state.status = "ready"
        connected_event.set()

    return on_connect


def _on_error_factory(state: TickWorkerState):
    def on_error(error):
        logger.error("Fyers WS error: %s", error)
        state.status = "degraded"
        msg = str(error).lower()
        if any(marker in msg for marker in ("auth", "token", "unauthorized", "forbidden")):
            _shutdown.set()

    return on_error


def _on_close_factory(state: TickWorkerState):
    def on_close(message=None):
        logger.warning("Fyers WS closed: %s", message)
        state.is_connected = False
        state.status = "degraded"

    return on_close


from app.services.bar_aggregator import FiveMinuteBarAggregator


async def _load_subscription_symbols(db: AsyncSession) -> list[str]:
    result = await db.execute(
        text("""
            SELECT DISTINCT i.fyers_symbol
            FROM instruments i
            WHERE i.id IN (
                -- open / non-closed positions
                SELECT instrument_id FROM positions
                WHERE state NOT IN ('closed', 'cancelled')
                UNION
                -- active watchlist items
                SELECT wi.instrument_id FROM watchlist_items wi
                JOIN watchlists w ON w.id = wi.watchlist_id
                WHERE w.is_active = true AND wi.removed_at IS NULL
                UNION
                -- armed entry legs / approved trade proposals
                SELECT tp.instrument_id FROM trade_proposals tp
                JOIN entry_legs el ON el.proposal_id = tp.id
                WHERE el.status = 'armed'
            )
            AND i.fyers_symbol IS NOT NULL
        """)
    )
    rows = result.fetchall()
    symbols = [r[0] for r in rows]
    if BENCHMARK_SYMBOL not in symbols:
        symbols.append(BENCHMARK_SYMBOL)
    return symbols


async def _emit_system_event(redis, severity: str, event_type: str, payload: dict):
    """Publish a system event (best-effort, also logs locally)."""
    try:
        async with async_session() as db:
            await db.execute(
                text("""
                    INSERT INTO system_events (component, severity, event_type, payload)
                    VALUES ('tick_ingestion', :severity, :event_type, :payload)
                """),
                {
                    "severity": severity,
                    "event_type": event_type,
                    "payload": json.dumps(payload),
                },
            )
            await db.commit()
    except Exception as e:
        logger.error("Failed to emit system_event: %s", e)


async def _set_worker_status(
    redis,
    status: str,
    worker_id: str | None = None,
    symbols: list[str] | None = None,
):
    """Write worker heartbeat/status to Redis."""
    payload = {
        "status": status,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if worker_id is not None:
        payload["worker_id"] = worker_id
    if symbols is not None:
        payload["symbol_count"] = len(symbols)
    await redis.set(REDIS_WORKER_STATUS_KEY, json.dumps(payload), ex=_LOCK_TTL_SECONDS)
    if symbols is not None:
        await redis.set(REDIS_WORKER_SYMBOLS_KEY, json.dumps(symbols), ex=300)


async def _publish_loop(
    publish_queue: asyncio.Queue,
    redis: aioredis.Redis,
):
    """Consume ticks from the sync queue, update LTP cache, aggregate 5m bars, and publish to Redis."""
    bar_aggregator = FiveMinuteBarAggregator(redis)

    while not _shutdown.is_set():
        try:
            tick = await asyncio.wait_for(publish_queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue

        symbol = tick["symbol"]
        payload = json.dumps(tick)
        try:
            pipe = redis.pipeline(transaction=False)
            pipe.set(
                f"{REDIS_LTP_PREFIX}{symbol}",
                payload,
                ex=REDIS_LTP_TTL,
            )
            pipe.publish(REDIS_CHANNEL_TICKS, payload)
            await pipe.execute()
        except Exception as e:
            logger.error("Redis LTP cache/publish error: %s", e)

        # 3. Process tick through 5-minute bar aggregator
        try:
            await bar_aggregator.process_tick(tick)
        except Exception as e:
            logger.error("Bar aggregator error: %s", e)


def plan_tick_subscription_change(
    action: str,
    requested: set[str],
    current: set[str],
    mandatory: set[str],
) -> tuple[set[str], set[str], set[str]]:
    """Return (to_add, to_remove, new_current).

    Browser chart sessions may ask the tick worker to drop symbols. Open
    positions, armed legs, the active watchlist, and the Nifty benchmark
    must stay on the single Fyers market socket regardless.
    """
    requested = {symbol for symbol in requested if symbol}
    if action == "subscribe":
        to_add = requested - current
        return to_add, set(), current | to_add
    if action == "unsubscribe":
        to_remove = (requested & current) - mandatory
        return set(), to_remove, current - to_remove
    if action == "replace":
        target = requested | mandatory
        to_add = target - current
        to_remove = current - target
        return to_add, to_remove, target
    return set(), set(), current


async def _mandatory_tick_symbols() -> set[str] | None:
    """Load DB-mandatory symbols. None means the load failed; refuse removals."""
    try:
        async with async_session() as db:
            return set(await _load_subscription_symbols(db))
    except Exception:
        logger.exception(
            "Failed to load mandatory tick symbols; refusing unsubscribe/replace"
        )
        return None


async def _subscription_listener(
    redis: aioredis.Redis,
    subscribe_cb,
    unsubscribe_cb,
    current_symbols: set[str],
):
    """Listen for subscription change commands on Redis channel `tick_subs`."""

    async def handle_message(message: dict[str, Any]) -> None:
        try:
            data = json.loads(message["data"])
            action = data.get("action")
            symbols = data.get("symbols", [])
            if action not in {"subscribe", "unsubscribe", "replace"}:
                return
            if action != "replace" and not symbols:
                return

            mandatory = await _mandatory_tick_symbols()
            if mandatory is None:
                return

            to_add, to_remove, new_current = plan_tick_subscription_change(
                action,
                set(symbols if isinstance(symbols, list) else []),
                current_symbols,
                mandatory,
            )
            if to_remove:
                unsubscribe_cb(list(to_remove))
            if to_add:
                subscribe_cb(list(to_add))
            current_symbols.clear()
            current_symbols.update(new_current)
            if to_add or to_remove:
                logger.info(
                    "Tick subscriptions %s: +%d / -%d, total=%d (mandatory=%d)",
                    action,
                    len(to_add),
                    len(to_remove),
                    len(current_symbols),
                    len(mandatory),
                )
        except Exception as e:
            logger.error("Subscription command error: %s", e)

    await consume_pubsub(
        redis,
        [REDIS_CHANNEL_SUBS],
        component="tick_worker",
        handler=handle_message,
        should_stop=_shutdown.is_set,
    )


async def _heartbeat_loop(redis: aioredis.Redis, state: TickWorkerState):
    """Periodically renew lease and update worker status in Redis."""
    while not _shutdown.is_set():
        refreshed = await renew_distributed_lease(
            redis,
            _LOCK_KEY,
            state.worker_id,
            _LOCK_TTL_SECONDS,
        )
        if not refreshed:
            logger.critical("Tick worker lost its singleton lock; stopping.")
            _shutdown.set()
            return
        await _set_worker_status(
            redis,
            state.status,
            worker_id=state.worker_id,
            symbols=list(state.symbols),
        )
        try:
            await asyncio.sleep(_LOCK_REFRESH_SECONDS)
        except asyncio.CancelledError:
            break


async def run_tick_worker():
    """Main async entry point for the tick ingestion worker."""

    # --- Redis connection ---
    redis = await create_async_redis()
    worker_id = str(uuid4())

    # Atomically acquire singleton lease
    if not await acquire_distributed_lease(
        redis,
        _LOCK_KEY,
        worker_id,
        _LOCK_TTL_SECONDS,
    ):
        logger.error("Another tick worker owns the singleton lock.")
        await redis.aclose()
        return

    state = TickWorkerState(worker_id)
    await _set_worker_status(redis, "connecting", worker_id=worker_id, symbols=[])

    logger.info("Starting tick ingestion worker (worker_id=%s)", worker_id)

    # Start heartbeat immediately after acquiring lease to protect against auth/connect timeouts
    heartbeat_task = asyncio.create_task(_heartbeat_loop(redis, state))
    loop = asyncio.get_running_loop()
    publish_task = None
    sub_task = None
    ws = None

    try:
        # --- Get access token ---
        try:
            access_token = await get_valid_access_token(redis)
        except AuthUnavailableError as e:
            logger.error("Cannot start: %s", e)
            await _emit_system_event(redis, "critical", "tick_worker_start_failed", {"reason": str(e)})
            return

        # --- Load initial subscription set ---
        async with async_session() as db:
            symbols = await _load_subscription_symbols(db)

        if not symbols:
            logger.warning("No symbols to subscribe (no open positions or watchlist items)")
            symbols = []

        logger.info("Initial subscription set: %d symbols", len(symbols))

        # --- Queue for sync→async bridge ---
        publish_queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        connected_event = threading.Event()

        # --- Create FyersDataSocket (SDK, runs in its own thread) ---
        from fyers_apiv3.FyersWebsocket.data_ws import FyersDataSocket

        FyersDataSocket._instance = None

        ws = FyersDataSocket(
            access_token=access_token,
            write_to_file=False,
            litemode=False,
            reconnect=True,
            on_message=_on_message_factory(publish_queue, loop, state),
            on_connect=_on_connect_factory(state, connected_event),
            on_error=_on_error_factory(state),
            on_close=_on_close_factory(state),
            reconnect_retry=50,
        )

        # --- Start SDK connection (blocks in thread) ---
        ws.connect()

        # Wait for connection
        if not connected_event.wait(timeout=15):
            logger.error("Fyers WS did not connect within 15s")
            await _emit_system_event(redis, "error", "tick_worker_connect_timeout", {})
            return

        # Subscribe to initial symbols
        current_symbols: set[str] = set()
        if symbols:
            ws.subscribe(symbols=symbols, data_type="SymbolUpdate")
            current_symbols.update(symbols)
            logger.info("Subscribed to %d symbols", len(symbols))

        state.symbols = current_symbols
        state.status = "ready"

        await _set_worker_status(redis, "ready", worker_id=worker_id, symbols=list(current_symbols))
        await _emit_system_event(redis, "info", "tick_worker_started", {
            "symbol_count": len(symbols),
            "worker_id": worker_id,
        })

        # --- Run async tasks ---
        publish_task = asyncio.create_task(_publish_loop(publish_queue, redis))
        sub_task = asyncio.create_task(
            _subscription_listener(
                redis,
                subscribe_cb=lambda syms: ws.subscribe(symbols=syms, data_type="SymbolUpdate"),
                unsubscribe_cb=lambda syms: ws.unsubscribe(symbols=syms, data_type="SymbolUpdate"),
                current_symbols=current_symbols,
            )
        )

        # Wait for shutdown signal
        while not _shutdown.is_set():
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down tick worker")
        _shutdown.set()

        # Cancel tasks
        for task in [publish_task, sub_task, heartbeat_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Close Fyers WS
        if ws:
            try:
                ws.close()
            except Exception:
                pass

        # Emit shutdown event
        state.status = "stopped"
        await _set_worker_status(redis, "stopped", worker_id=worker_id)
        await _emit_system_event(redis, "info", "tick_worker_stopped", {"worker_id": worker_id})
        await release_distributed_lease(redis, _LOCK_KEY, worker_id)
        await redis.aclose()


def main():
    """Entry point: python -m app.workers.tick_worker"""

    def handle_signal(signum, frame):
        logger.info("Received signal %s, shutting down...", signum)
        _shutdown.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    asyncio.run(run_tick_worker())


if __name__ == "__main__":
    main()
