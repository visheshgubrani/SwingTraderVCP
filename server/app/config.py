from pydantic_settings import BaseSettings
from pydantic import Field, field_validator


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://algo:algo@localhost:5480/algo_trading"
    redis_url: str = "redis://localhost:6380/0"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:3000", "http://localhost:3000"]

    fyers_app_id: str = ""
    fyers_secret_key: str = ""
    fyers_redirect_uri: str = "http://127.0.0.1:3000/callback"

    scheduler_timezone: str = "Asia/Kolkata"
    eod_sync_enabled: bool = True
    eod_sync_hour: int = Field(default=18, ge=0, le=23)
    eod_sync_minute: int = Field(default=30, ge=0, le=59)

    @field_validator("database_url", mode="before")
    @classmethod
    def convert_postgres_scheme(cls, v: str) -> str:
        if isinstance(v, str) and v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    model_config = {"env_file": ".env", "extra": "ignore"}



settings = Settings()
