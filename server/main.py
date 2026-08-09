from contextlib import asynccontextmanager
from typing import AsyncIterator
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
import redis.asyncio as aioredis
from arq.connections import ArqRedis

from app.config import settings
from app.redis_pool import create_arq_pool
from app.database import db_dep
from app.routers.auth import router as auth_router
from app.routers.historical import router as historical_router
from app.routers.journal import router as journal_router
from app.routers.saas_scans import router as saas_scans_router
from app.routers.screening import router as screening_router
from app.routers.system_controls import router as system_controls_router
from app.routers.trading import router as trading_router
from app.routers.ws import router as ws_router, manager as ws_manager

# Module-level reference — set during lifespan, usable by background workers.
# API endpoints should use get_redis() dependency instead.
_arq_pool: ArqRedis | None = None


def get_redis_pool() -> ArqRedis:
    """Return the shared arq/Redis pool. For use by background workers only."""
    if _arq_pool is None:
        raise RuntimeError("Redis pool not initialised — app lifespan not running")
    return _arq_pool


async def get_redis(request: Request) -> AsyncIterator[ArqRedis]:
    """FastAPI dependency — yields Redis pool from app.state."""
    yield request.app.state.redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _arq_pool
    _arq_pool = await create_arq_pool()
    app.state.redis = _arq_pool

    # Separate async Redis connection for WS manager (pub/sub needs dedicated conn)
    app.state.redis_async = aioredis.from_url(settings.redis_url, decode_responses=True)
    await ws_manager.start(app.state.redis_async)

    yield

    # Shutdown
    await ws_manager.stop()
    await app.state.redis_async.aclose()
    await _arq_pool.close()

app = FastAPI(title="Algo Trading", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(historical_router, prefix="/api/v1")
app.include_router(screening_router, prefix="/api/v1")
app.include_router(trading_router, prefix="/api/v1")
app.include_router(journal_router, prefix="/api/v1")
app.include_router(system_controls_router, prefix="/api/v1")
app.include_router(saas_scans_router)
app.include_router(ws_router)


@app.get("/health")
async def health(db: db_dep) -> dict:
    result = await db.execute(text("SELECT 1"))
    return {"status": "ok", "db": result.scalar() == 1}
