from contextlib import asynccontextmanager
from typing import AsyncIterator
import logging

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from arq.connections import ArqRedis

from app.config import settings
from app.dependencies.auth import require_authenticated_user
from app.redis_pool import create_arq_pool, create_async_redis
from app.database import db_dep
from app.routers.auth import router as auth_router
from app.routers.automation import router as automation_router
from app.routers.historical import router as historical_router
from app.routers.instruments import router as instruments_router
from app.routers.journal import router as journal_router
from app.routers.saas_scans import router as saas_scans_router
from app.routers.screening import router as screening_router
from app.routers.system_controls import router as system_controls_router
from app.routers.trading import router as trading_router
from app.routers.vcp_vision import router as vcp_vision_router
from app.routers.watchlists import router as watchlists_router
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
    app.state.redis_async = await create_async_redis()
    await ws_manager.start(app.state.redis_async)

    # Verify core database tables are initialized (OPS-003)
    try:
        from app.database import async_session
        async with async_session() as session:
            result = await session.execute(
                text(
                    """
                    SELECT count(*)::integer 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                      AND table_name IN (
                        'instruments', 'market_candles', 'trade_proposals', 
                        'positions', 'order_intents', 'risk_policies', 
                        'broker_auth_tokens', 'p10_rollout_state'
                      )
                    """
                )
            )
            found_tables = result.scalar_one()
            if found_tables < 8:
                msg = f"Database schema is incomplete: found {found_tables}/8 core tables. Run database migrations before starting."
                if settings.app_environment == "production":
                    raise RuntimeError(msg)
                import logging
                logging.getLogger(__name__).warning(msg)
    except Exception as exc:
        if settings.app_environment == "production":
            raise
        import logging
        logging.getLogger(__name__).warning("Startup schema check failed: %s", exc)

    yield

    # Shutdown
    await ws_manager.stop()
    await app.state.redis_async.aclose()
    await _arq_pool.close()


# SEC-010: Disable Swagger / OpenAPI docs in production unless explicitly enabled
_is_prod = settings.app_environment == "production"
_enable_docs = settings.enable_docs_in_production or not _is_prod

app = FastAPI(
    title="SwingTraderVCP",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _enable_docs else None,
    redoc_url="/redoc" if _enable_docs else None,
    openapi_url="/openapi.json" if _enable_docs else None,
)

# INF-004: Standard HTTP security headers
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth router manages its own public vs protected endpoints (login, logout, session, url, callback)
app.include_router(auth_router, prefix="/api/v1")

# Protected personal trading money path & automation endpoints (SEC-001)
personal_auth_dep = [Depends(require_authenticated_user)]

app.include_router(automation_router, dependencies=personal_auth_dep)
app.include_router(historical_router, prefix="/api/v1", dependencies=personal_auth_dep)
app.include_router(instruments_router, prefix="/api/v1", dependencies=personal_auth_dep)
app.include_router(screening_router, prefix="/api/v1", dependencies=personal_auth_dep)
app.include_router(trading_router, prefix="/api/v1", dependencies=personal_auth_dep)
app.include_router(journal_router, prefix="/api/v1", dependencies=personal_auth_dep)
app.include_router(system_controls_router, prefix="/api/v1", dependencies=personal_auth_dep)
app.include_router(watchlists_router, prefix="/api/v1", dependencies=personal_auth_dep)
app.include_router(vcp_vision_router, prefix="/api/v1", dependencies=personal_auth_dep)

# SaaS public scans (uses signed HMAC internal assertion)
app.include_router(saas_scans_router)

# WebSocket handler (authenticates connection internally)
app.include_router(ws_router)


@app.get("/health")
async def health(request: Request, db: db_dep) -> dict:
    """Liveness probe (unauthenticated, non-sensitive)."""
    result = await db.execute(text("SELECT 1"))
    db_ok = result.scalar() == 1
    redis_ok = False
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            await redis.ping()
            redis_ok = True
        except Exception:
            logging.getLogger(__name__).warning("Redis health ping failed")
    if not db_ok or not redis_ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unavailable", "db": db_ok, "redis": redis_ok},
        )
    return {"status": "ok", "db": True, "redis": True}
