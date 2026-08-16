"""Single Fyers order-WebSocket gateway for order correlation and fills.

Run as a separate process:
    python -m app.workers.order_gateway
"""

import asyncio
import json
import logging
import signal
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis
from sqlalchemy import text

from app.config import settings
from app.database import async_session
from app.services.auth_service import AuthUnavailableError, get_valid_access_token
from app.services.execution_engine import ensure_execution_mode_armed
from app.services.order_gateway import process_order_message, process_trade_message
from app.services.paper_broker import (
    REDIS_PAPER_ORDER_CHANNEL,
    build_paper_fill_messages,
    load_unfilled_submitted_paper_intents,
    release_unaccepted_paper_claims,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("order_gateway")

_LOCK_KEY = "order_gateway:singleton"
_STATUS_KEY = "order_gateway:status"
_LOCK_TTL_SECONDS = 30
_LOCK_REFRESH_SECONDS = 10


async def run_order_gateway() -> None:
    ensure_execution_mode_armed()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    worker_id = str(uuid4())
    if not await redis.set(
        _LOCK_KEY,
        worker_id,
        nx=True,
        ex=_LOCK_TTL_SECONDS,
    ):
        logger.error("Another order gateway owns the singleton lock.")
        await redis.aclose()
        return

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, stop_event.set)

    if settings.execution_mode == "paper":
        await _run_paper_gateway(redis, worker_id, stop_event)
        return

    try:
        access_token = await get_valid_access_token(redis)
    except AuthUnavailableError as exc:
        await _emit_worker_event(
            "critical",
            "order_gateway_start_failed",
            {"reason": str(exc)},
        )
        await _release_lock(redis, worker_id)
        await redis.aclose()
        return

    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(
        maxsize=10_000
    )

    def enqueue(kind: str, message: Any) -> None:
        if not isinstance(message, dict):
            return

        def put() -> None:
            try:
                event_queue.put_nowait((kind, message))
            except asyncio.QueueFull:
                logger.critical("Order gateway queue is full; event was dropped.")
                stop_event.set()

        loop.call_soon_threadsafe(put)

    def on_error(error: Any) -> None:
        logger.error("Fyers order socket error: %s", error)
        message = str(error)

        def put_error() -> None:
            try:
                event_queue.put_nowait(("socket_error", {"message": message}))
            except asyncio.QueueFull:
                stop_event.set()
            if any(
                marker in message.lower()
                for marker in ("auth", "token", "unauthorized", "forbidden")
            ):
                stop_event.set()

        loop.call_soon_threadsafe(put_error)

    def on_close(message: Any) -> None:
        logger.warning("Fyers order socket closed: %s", message)

    from fyers_apiv3.FyersWebsocket.order_ws import FyersOrderSocket

    FyersOrderSocket._instance = None
    socket = FyersOrderSocket(
        access_token=f"{settings.fyers_app_id}:{access_token}",
        write_to_file=False,
        reconnect=True,
        reconnect_retry=50,
        on_orders=lambda message: enqueue("order", message),
        on_trades=lambda message: enqueue("trade", message),
        on_error=on_error,
        on_connect=lambda: logger.info("Fyers order socket connected"),
        on_close=on_close,
    )
    socket.connect()
    socket.subscribe(data_type="OnOrders,OnTrades")

    await _set_status(redis, "running", worker_id)
    await _emit_worker_event("info", "order_gateway_started", {})
    processor = asyncio.create_task(_process_events(event_queue, stop_event))
    heartbeat = asyncio.create_task(_heartbeat(redis, worker_id, stop_event))

    try:
        await stop_event.wait()
    finally:
        processor.cancel()
        heartbeat.cancel()
        for task in (processor, heartbeat):
            try:
                await task
            except asyncio.CancelledError:
                pass
        try:
            # The SDK closes the socket before touching its optional runner
            # thread; an AttributeError after closure is harmless here.
            await asyncio.to_thread(socket.close_connection)
        except Exception:
            logger.debug("Fyers socket close completed with SDK cleanup noise.")
        await _set_status(redis, "stopped", worker_id)
        await _emit_worker_event("info", "order_gateway_stopped", {})
        await _release_lock(redis, worker_id)
        await redis.aclose()


async def _run_paper_gateway(redis, worker_id: str, stop_event: asyncio.Event) -> None:
    await _set_status(redis, "running", worker_id)
    await _emit_worker_event("info", "order_gateway_started", {"mode": "paper"})
    await _recover_paper_fills()
    heartbeat = asyncio.create_task(_heartbeat(redis, worker_id, stop_event))
    pubsub = redis.pubsub()
    await pubsub.subscribe(REDIS_PAPER_ORDER_CHANNEL)
    try:
        while not stop_event.is_set():
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=1.0,
            )
            if not message or message["type"] != "message":
                continue
            try:
                payload = json.loads(message["data"])
                kind = payload["kind"]
                event = payload["message"]
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
            try:
                async with async_session() as db:
                    if kind == "order":
                        await process_order_message(db, event)
                    elif kind == "trade":
                        await process_trade_message(db, event)
                    await db.commit()
            except Exception:
                logger.exception("Failed to persist paper %s update", kind)
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
        await pubsub.unsubscribe(REDIS_PAPER_ORDER_CHANNEL)
        await pubsub.close()
        await _set_status(redis, "stopped", worker_id)
        await _emit_worker_event("info", "order_gateway_stopped", {"mode": "paper"})
        await _release_lock(redis, worker_id)
        await redis.aclose()


async def _recover_paper_fills() -> None:
    async with async_session() as db:
        await release_unaccepted_paper_claims(db)
        rows = await load_unfilled_submitted_paper_intents(db)
        for row in rows:
            await db.execute(
                text(
                    """
                    UPDATE order_intents
                    SET
                        status = CASE
                            WHEN status = 'submission_pending' THEN 'submitted'
                            ELSE status
                        END,
                        fyers_async_id = COALESCE(fyers_async_id, :fyers_async_id),
                        fyers_order_id = COALESCE(fyers_order_id, :fyers_order_id)
                    WHERE id = :order_intent_id
                    """
                ),
                {
                    "order_intent_id": row["id"],
                    "fyers_async_id": row["fyers_async_id"],
                    "fyers_order_id": row["fyers_order_id"],
                },
            )
            result = build_paper_fill_messages(
                snapshot={
                    "id": row["id"],
                    "quantity": row["quantity"],
                    "symbol": row["symbol"],
                    "side": row["side"],
                },
                fyers_async_id=row["fyers_async_id"],
                fyers_order_id=row["fyers_order_id"],
                trade_number=row["trade_number"],
                fill_price=row["traded_price"],
            )
            await process_order_message(db, result.order_message)
            await process_trade_message(db, result.trade_message)
        await db.commit()


async def _process_events(
    queue: asyncio.Queue[tuple[str, dict[str, Any]]],
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            kind, message = await asyncio.wait_for(queue.get(), timeout=1)
        except asyncio.TimeoutError:
            continue
        try:
            async with async_session() as db:
                if kind == "order":
                    await process_order_message(db, message)
                elif kind == "trade":
                    await process_trade_message(db, message)
                else:
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
                                'order_gateway',
                                'error',
                                'order_gateway_socket_error',
                                CAST(:payload AS jsonb)
                            )
                            """
                        ),
                        {"payload": json.dumps(message)},
                    )
                await db.commit()
        except Exception:
            logger.exception("Failed to persist Fyers %s update", kind)
            await _emit_worker_event(
                "error",
                "order_gateway_event_failed",
                {"kind": kind},
            )
        finally:
            queue.task_done()


async def _heartbeat(
    redis,
    worker_id: str,
    stop_event: asyncio.Event,
) -> None:
    refresh_script = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
  return 1
end
return 0
"""
    while not stop_event.is_set():
        refreshed = await redis.eval(
            refresh_script,
            1,
            _LOCK_KEY,
            worker_id,
            _LOCK_TTL_SECONDS,
        )
        if int(refreshed) != 1:
            logger.critical("Order gateway lost its singleton lock; stopping.")
            stop_event.set()
            return
        await _set_status(redis, "running", worker_id)
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=_LOCK_REFRESH_SECONDS,
            )
        except asyncio.TimeoutError:
            pass


async def _set_status(redis, status: str, worker_id: str) -> None:
    await redis.set(
        _STATUS_KEY,
        json.dumps(
            {
                "status": status,
                "worker_id": worker_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ),
        ex=_LOCK_TTL_SECONDS,
    )


async def _release_lock(redis, worker_id: str) -> None:
    release_script = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""
    await redis.eval(release_script, 1, _LOCK_KEY, worker_id)


async def _emit_worker_event(
    severity: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
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
                        'order_gateway',
                        :severity,
                        :event_type,
                        CAST(:payload AS jsonb)
                    )
                    """
                ),
                {
                    "severity": severity,
                    "event_type": event_type,
                    "payload": json.dumps(payload),
                },
            )
            await db.commit()
    except Exception:
        logger.exception("Failed to persist order-gateway system event")


def main() -> None:
    asyncio.run(run_order_gateway())


if __name__ == "__main__":
    main()
