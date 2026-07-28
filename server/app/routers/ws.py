"""
Browser-facing WebSocket handler.

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

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])


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

    async def connect(self, ws: WebSocket):
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

    def disconnect(self, session_id: int):
        self._clients.pop(session_id, None)
        logger.info("Browser WS disconnected (total=%d)", len(self._clients))

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
            symbols = set(data.get("symbols", []))
            if not symbols:
                await session.ws.send_json({"type": "error", "message": "No symbols"})
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

            # Also tell tick worker to subscribe if not already
            await self._redis.publish(
                "tick_subs",
                json.dumps({"action": "subscribe", "symbols": list(symbols)}),
            )
            await session.ws.send_json({"type": "subscribed", "symbols": list(symbols)})

        elif action == "unsubscribe":
            symbols = set(data.get("symbols", []))
            session.symbols -= symbols
            await session.ws.send_json({"type": "unsubscribed", "symbols": list(symbols)})

        elif action == "ping":
            await session.ws.send_json({"type": "pong"})

        else:
            await session.ws.send_json({"type": "error", "message": f"Unknown action: {action}"})

    async def _redis_subscriber(self):
        """Subscribe to Redis `ticks` channel and fan out to matching browser clients."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe("ticks")

        try:
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if not message or message["type"] != "message":
                    continue

                try:
                    tick = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                symbol = tick.get("symbol")
                if not symbol:
                    continue

                # Fan out to clients subscribed to this symbol
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

        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe("ticks")
            await pubsub.close()


# Global singleton
manager = WSManager()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    session_id = id(ws)
    try:
        while True:
            raw = await ws.receive_text()
            await manager.handle_message(session_id, raw)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("WS session error: %s", e)
    finally:
        manager.disconnect(session_id)
