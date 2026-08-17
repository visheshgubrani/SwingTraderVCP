"""
Browser-facing WebSocket handler with session authentication (SEC-004).

Owns the server-side WS endpoint that browser clients connect to.
Subscribes to Redis `ticks` channel once (shared) and fans out LTP
updates to each connected browser session based on their subscribed symbols.

Does NOT open any Fyers sockets — that is the tick ingestion worker's job.

Protocol (JSON over WS):
    Client → Server:
        {"action": "subscribe", "symbols": ["NSE:SBIN-EQ", ...]}
        {"action": "unsubscribe", "symbols": ["NSE:SBIN-EQ", ...]}
        {"action": "ping"}

    Server → Client:
        {"type": "ltp", "symbol": "...", "ltp": 123.45, ...}
        {"type": "subscribed", "symbols": [...]}
        {"type": "unsubscribed", "symbols": [...]}
        {"type": "pong"}
        {"type": "error", "message": "..."}
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from starlette.websockets import WebSocketState

from app.config import settings
from app.services.session_service import get_user_session
from app.redis_pubsub import consume_pubsub

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])

MAX_SUBSCRIBE_SYMBOLS_PER_MSG = 100
MAX_TOTAL_SYMBOLS_PER_SESSION = 200
MAX_CONNECTED_CLIENTS = 100


@dataclass
class ClientSession:
    ws: WebSocket
    symbols: set[str] = field(default_factory=set)


class WSManager:
    """
    Manages browser WS sessions and Redis tick subscription.
    Singleton — created once per API process.
    """

    def __init__(self):
        self._clients: dict[int, ClientSession] = {}
        self._redis_sub_task: asyncio.Task | None = None
        self._redis = None
        self._started = False

    async def start(self, redis):
        """Call once during app lifespan to start the Redis subscriber."""
        if self._started:
            return
        self._redis = redis
        self._redis_sub_task = asyncio.create_task(self._redis_subscriber())
        self._started = True
        logger.info("WSManager started — listening for Redis ticks")

    async def stop(self):
        """Call during app shutdown."""
        if self._redis_sub_task:
            self._redis_sub_task.cancel()
            try:
                await self._redis_sub_task
            except asyncio.CancelledError:
                pass
        # Close all client connections
        for session in list(self._clients.values()):
            try:
                await session.ws.close()
            except Exception:
                pass
        self._clients.clear()
        self._started = False

    async def connect(self, ws: WebSocket) -> bool:
        if len(self._clients) >= MAX_CONNECTED_CLIENTS:
            logger.warning("Rejecting WS connection: max clients reached (%d)", MAX_CONNECTED_CLIENTS)
            await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Max connections exceeded")
            return False

        await ws.accept()
        session_id = id(ws)
        self._clients[session_id] = ClientSession(ws=ws)
        logger.info("Browser WS connected (total=%d)", len(self._clients))

        # Send current tick worker status
        try:
            status_raw = await self._redis.get("tick_worker:status")
            if status_raw:
                await ws.send_json({"type": "tick_worker_status", **json.loads(status_raw)})
        except Exception:
            pass
        return True

    async def disconnect(self, session_id: int):
        removed_session = self._clients.pop(session_id, None)
        logger.info("Browser WS disconnected (total=%d)", len(self._clients))

        # Demand cleanup: release symbols no longer needed by any browser session
        if removed_session and removed_session.symbols and self._redis:
            all_remaining_symbols = set().union(*(s.symbols for s in self._clients.values())) if self._clients else set()
            dropped_symbols = removed_session.symbols - all_remaining_symbols
            if dropped_symbols:
                try:
                    await self._redis.publish(
                        "tick_subs",
                        json.dumps({"action": "unsubscribe", "symbols": list(dropped_symbols)}),
                    )
                except Exception as e:
                    logger.warning("Failed to publish tick unsubscribe on disconnect: %s", e)

    async def handle_message(self, session_id: int, raw: str):
        """Process a message from a browser client."""
        session = self._clients.get(session_id)
        if not session:
            return

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await session.ws.send_json({"type": "error", "message": "Invalid JSON"})
            return

        action = data.get("action")

        if action == "subscribe":
            raw_symbols = data.get("symbols", [])
            if not isinstance(raw_symbols, list) or not raw_symbols:
                await session.ws.send_json({"type": "error", "message": "No symbols"})
                return

            if len(raw_symbols) > MAX_SUBSCRIBE_SYMBOLS_PER_MSG:
                await session.ws.send_json({
                    "type": "error",
                    "message": f"Too many symbols in subscription (max {MAX_SUBSCRIBE_SYMBOLS_PER_MSG})",
                })
                return

            symbols = {s for s in raw_symbols if isinstance(s, str) and s.strip()}
            new_symbols = symbols - session.symbols

            if len(session.symbols) + len(new_symbols) > MAX_TOTAL_SYMBOLS_PER_SESSION:
                await session.ws.send_json({
                    "type": "error",
                    "message": f"Session subscription limit exceeded (max {MAX_TOTAL_SYMBOLS_PER_SESSION} symbols)",
                })
                return

            session.symbols.update(symbols)

            # Fetch current LTP from cache for instant display
            for sym in symbols:
                cached = await self._redis.get(f"ltp:{sym}")
                if cached:
                    try:
                        await session.ws.send_json(json.loads(cached))
                    except Exception:
                        pass

            # Tell tick worker to subscribe
            await self._redis.publish(
                "tick_subs",
                json.dumps({"action": "subscribe", "symbols": list(symbols)}),
            )
            await session.ws.send_json({"type": "subscribed", "symbols": list(symbols)})

        elif action == "unsubscribe":
            raw_symbols = data.get("symbols", [])
            symbols = {s for s in raw_symbols if isinstance(s, str)}
            session.symbols -= symbols

            all_remaining = set().union(*(s.symbols for s in self._clients.values())) if self._clients else set()
            dropped_symbols = symbols - all_remaining
            if dropped_symbols and self._redis:
                try:
                    await self._redis.publish(
                        "tick_subs",
                        json.dumps({"action": "unsubscribe", "symbols": list(dropped_symbols)}),
                    )
                except Exception as e:
                    logger.warning("Failed to publish tick unsubscribe: %s", e)

            await session.ws.send_json({"type": "unsubscribed", "symbols": list(symbols)})

        elif action == "ping":
            await session.ws.send_json({"type": "pong"})

        else:
            await session.ws.send_json({"type": "error", "message": f"Unknown action: {action}"})

    async def _fanout_tick_message(self, message: dict[str, Any]) -> None:
        try:
            tick = json.loads(message["data"])
        except (json.JSONDecodeError, TypeError):
            return

        symbol = tick.get("symbol")
        if not symbol:
            return

        dead = []
        for session_id, session in self._clients.items():
            if symbol in session.symbols:
                try:
                    if session.ws.client_state == WebSocketState.CONNECTED:
                        await session.ws.send_json(tick)
                    else:
                        dead.append(session_id)
                except Exception:
                    dead.append(session_id)

        for sid in dead:
            self._clients.pop(sid, None)

    async def _redis_subscriber(self):
        """Subscribe to Redis `ticks` channel and fan out to matching browser clients."""
        try:
            await consume_pubsub(
                self._redis,
                ["ticks"],
                component="browser_ws",
                handler=self._fanout_tick_message,
            )
        except asyncio.CancelledError:
            return


# Global singleton
manager = WSManager()


def _is_origin_allowed(origin: str | None) -> bool:
    """Validate WS Origin header against allowed CORS origins."""
    if not origin:
        # In production, require an explicit valid Origin header
        if settings.app_environment == "production":
            return False
        return True  # Dev/test without origin header
    origin_clean = origin.rstrip("/")
    for allowed in settings.cors_origins:
        if origin_clean == allowed.rstrip("/"):
            return True
    return False


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    # Origin verification (SEC-004)
    origin = ws.headers.get("origin")
    if not _is_origin_allowed(origin):
        logger.warning("Rejected WS connection with disallowed/missing origin: %s", origin)
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Origin not allowed")
        return

    # Cookie-only session authentication check (SEC-004 / SEC-001)
    # (Token is extracted exclusively from HttpOnly cookie, never query params)
    session_id = ws.cookies.get(settings.session_cookie_name)
    redis = ws.app.state.redis
    session = await get_user_session(redis, session_id)
    if not session:
        logger.warning("Rejected unauthenticated WS connection")
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authentication required")
        return

    connected = await manager.connect(ws)
    if not connected:
        return

    client_id = id(ws)
    try:
        while True:
            raw = await ws.receive_text()
            await manager.handle_message(client_id, raw)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("WS session error: %s", e)
    finally:
        await manager.disconnect(client_id)
