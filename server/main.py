from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.database import db_dep

app = FastAPI(title="Algo Trading", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health(db: db_dep) -> dict:
    result = await db.execute(text("SELECT 1"))
    return {"status": "ok", "db": result.scalar() == 1}
