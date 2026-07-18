from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
import urllib.parse
from arq import create_pool
from arq.connections import RedisSettings

from app.config import settings
from app.database import db_dep
from app.routers.auth import router as auth_router
from app.routers.historical import router as historical_router
from app.routers.screening import router as screening_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup Redis Connection Pool for arq enqueuing
    url = urllib.parse.urlparse(settings.redis_url)
    redis_host = url.hostname or '127.0.0.1'
    redis_port = url.port or 6379
    redis_db = int(url.path.lstrip('/')) if url.path else 0
    
    app.state.redis = await create_pool(
        RedisSettings(host=redis_host, port=redis_port, database=redis_db)
    )
    yield
    # Close Redis pool on shutdown
    await app.state.redis.close()

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


@app.get("/health")
async def health(db: db_dep) -> dict:
    result = await db.execute(text("SELECT 1"))
    return {"status": "ok", "db": result.scalar() == 1}

