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

# Add parent to path so we can import app.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import async_session
from app.domain.market_regime import BENCHMARK_SYMBOL
from app.services.auth_service import get_valid_access_token, AuthUnavailableError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("tick_worker")

# Redis keys / channels
REDIS_CHANNEL_TICKS = "ticks"
REDIS_CHANNEL_SUBS = "tick_subs"
REDIS_LTP_PREFIX = "ltp:"
REDIS_LTP_TTL = 60  # seconds — refreshed on every tick
REDIS_WORKER_STATUS_KEY = "tick_worker:status"
REDIS_WORKER_SYMBOLS_KEY = "tick_worker:symbols"

# Shutdown flag
_shutdown = threading.Event()


def _on_message_factory(publish_queue: asyncio.Queue):
    """Return an on_message callback that enqueues ticks for async Redis publish."""

    def on_message(message: dict):
        try:
            # SDK delivers parsed dict: {ltp, symbol, vol_traded_today, ...}
            if not isinstance(message, dict):
                return
            symbol = message.get("symbol")
            ltp = message.get("ltp")
            if not symbol or ltp is None:
                return

            tick = {
                "symbol": symbol,
                "ltp": float(ltp),
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
            # Non-blocking put into the queue consumed by the async loop
            publish_queue.put_nowait(tick)
        except Exception as e:
            logger.error("on_message error: %s", e)

    return on_message


def _on_connect_factory(connected_event: threading.Event):
    def on_connect():
        logger.info("Fyers WS connected")
        connected_event.set()

    return on_connect


def _on_error_factory():
    def on_error(error):
        logger.error("Fyers WS error: %s", error)

    return on_error


def _on_close_factory():
    def on_close():
        logger.warning("Fyers WS closed")

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


async def _set_worker_status(redis, status: str, symbols: list[str] | None = None):
    """Write worker heartbeat/status to Redis."""
    payload = {
        "status": status,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if symbols is not None:
        payload["symbol_count"] = len(symbols)
    await redis.set(REDIS_WORKER_STATUS_KEY, json.dumps(payload), ex=30)
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
        ltp = tick["ltp"]

        # 1. Update hot LTP cache
        try:
            await redis.set(
                f"{REDIS_LTP_PREFIX}{symbol}",
                json.dumps(tick),
                ex=REDIS_LTP_TTL,
            )
        except Exception as e:
            logger.error("Redis LTP cache error: %s", e)

        # 2. Publish to ticks channel (for position monitor + browser WS)
        try:
            await redis.publish(REDIS_CHANNEL_TICKS, json.dumps(tick))
        except Exception as e:
            logger.error("Redis publish error: %s", e)

        # 3. Process tick through 5-minute bar aggregator
        try:
            await bar_aggregator.process_tick(tick)
        except Exception as e:
            logger.error("Bar aggregator error: %s", e)


async def _subscription_listener(
    redis: aioredis.Redis,
    subscribe_cb,
    unsubscribe_cb,
    current_symbols: set[str],
):
    """Listen for subscription change commands on Redis channel `tick_subs`."""
    pubsub = redis.pubsub()
    await pubsub.subscribe(REDIS_CHANNEL_SUBS)

    try:
        while not _shutdown.is_set():
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message and message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    action = data.get("action")
                    symbols = data.get("symbols", [])

                    if action == "subscribe" and symbols:
                        new_syms = set(symbols) - current_symbols
                        if new_syms:
                            subscribe_cb(list(new_syms))
                            current_symbols.update(new_syms)
                            logger.info("Subscribed to %d new symbols", len(new_syms))

                    elif action == "unsubscribe" and symbols:
                        remove_syms = set(symbols) & current_symbols
                        if remove_syms:
                            unsubscribe_cb(list(remove_syms))
                            current_symbols -= remove_syms
                            logger.info("Unsubscribed from %d symbols", len(remove_syms))

                    elif action == "replace":
                        # Full replacement of subscription set
                        new_set = set(symbols)
                        to_add = new_set - current_symbols
                        to_remove = current_symbols - new_set
                        if to_remove:
                            unsubscribe_cb(list(to_remove))
                        if to_add:
                            subscribe_cb(list(to_add))
                        current_symbols.clear()
                        current_symbols.update(new_set)
                        logger.info(
                            "Replaced subscriptions: +%d / -%d, total=%d",
                            len(to_add), len(to_remove), len(current_symbols),
                        )
                except Exception as e:
                    logger.error("Subscription command error: %s", e)
    finally:
        await pubsub.unsubscribe(REDIS_CHANNEL_SUBS)
        await pubsub.close()


async def _heartbeat_loop(redis: aioredis.Redis, symbols: list[str]):
    """Periodically update worker status in Redis."""
    while not _shutdown.is_set():
        await _set_worker_status(redis, "running", symbols)
        await asyncio.sleep(10)


async def run_tick_worker():
    """Main async entry point for the tick ingestion worker."""

    # --- Redis connection ---
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    # Check if another tick worker is already running
    existing = await redis.get(REDIS_WORKER_STATUS_KEY)
    if existing:
        try:
            data = json.loads(existing)
            ts = datetime.datetime.fromisoformat(data["timestamp"])
            age = (datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds()
            if age < 30 and data.get("status") == "running":
                logger.error(
                    "Another tick worker appears to be running (heartbeat %ds ago). "
                    "Stop it first or wait for heartbeat to expire.", int(age)
                )
                await redis.aclose()
                return
        except Exception:
            pass

    logger.info("Starting tick ingestion worker")

    # --- Get access token ---
    try:
        access_token = await get_valid_access_token(redis)
    except AuthUnavailableError as e:
        logger.error("Cannot start: %s", e)
        await _emit_system_event(redis, "critical", "tick_worker_start_failed", {"reason": str(e)})
        await redis.aclose()
        return

    # --- Load initial subscription set ---
    async with async_session() as db:
        symbols = await _load_subscription_symbols(db)

    if not symbols:
        logger.warning("No symbols to subscribe (no open positions or watchlist items)")
        # Still start — symbols can be added dynamically via tick_subs channel
        symbols = []

    logger.info("Initial subscription set: %d symbols", len(symbols))

    # --- Queue for sync→async bridge ---
    publish_queue: asyncio.Queue = asyncio.Queue(maxsize=10000)

    # --- Threading events ---
    connected_event = threading.Event()

    # --- Create FyersDataSocket (SDK, runs in its own thread) ---
    # Import here to avoid import-time side effects
    from fyers_apiv3.FyersWebsocket.data_ws import FyersDataSocket

    # Reset singleton if re-running (e.g. in tests)
    FyersDataSocket._instance = None

    ws = FyersDataSocket(
        access_token=access_token,
        write_to_file=False,
        litemode=False,
        reconnect=True,
        on_message=_on_message_factory(publish_queue),
        on_connect=_on_connect_factory(connected_event),
        on_error=_on_error_factory(),
        on_close=_on_close_factory(),
        reconnect_retry=50,
    )

    # --- Start SDK connection (blocks in thread) ---
    ws.connect()

    # Wait for connection
    if not connected_event.wait(timeout=15):
        logger.error("Fyers WS did not connect within 15s")
        await _emit_system_event(redis, "error", "tick_worker_connect_timeout", {})
        await redis.aclose()
        return

    # Subscribe to initial symbols
    current_symbols: set[str] = set()
    if symbols:
        ws.subscribe(symbols=symbols, data_type="SymbolUpdate")
        current_symbols.update(symbols)
        logger.info("Subscribed to %d symbols", len(symbols))

    await _set_worker_status(redis, "running", symbols)
    await _emit_system_event(redis, "info", "tick_worker_started", {
        "symbol_count": len(symbols),
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
    heartbeat_task = asyncio.create_task(_heartbeat_loop(redis, list(current_symbols)))

    # Wait for shutdown signal
    try:
        while not _shutdown.is_set():
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down tick worker")
        _shutdown.set()

        # Cancel tasks
        for task in [publish_task, sub_task, heartbeat_task]:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Close Fyers WS
        try:
            ws.close()
        except Exception:
            pass

        # Emit shutdown event
        await _set_worker_status(redis, "stopped")
        await _emit_system_event(redis, "info", "tick_worker_stopped", {})

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
