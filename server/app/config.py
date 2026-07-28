from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


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

    # Token refresh: run daily before market open (default 08:50 IST).
    # Fyers access tokens expire ~midnight IST; refresh early so workers
    # have a valid token by 09:15 market open.
    token_refresh_enabled: bool = True
    token_refresh_hour: int = Field(default=8, ge=0, le=23)
    token_refresh_minute: int = Field(default=50, ge=0, le=59)

    # Reconciliation: compare DB vs Fyers during market hours (IST).
    reconciliation_enabled: bool = True

    # Live placement requires both settings. Keeping the arming flag separate
    # prevents an accidental EXECUTION_MODE change from moving money.
    execution_mode: Literal["paper", "live"] = "paper"
    live_order_placement_enabled: bool = False
    fyers_async_orders_url: str = "https://api-t1.fyers.in/api/v3/orders/async"
    fyers_api_base_url: str = "https://api-t1.fyers.in/api/v3"
    fyers_order_timeout_seconds: float = Field(default=10.0, gt=0, le=30)
    order_ops_limit: int = Field(default=10, ge=1, le=10)
    order_gateway_reconnect_seconds: int = Field(default=5, ge=1, le=60)

    # P7 fundamentals are annotations over technical survivors only. Upstox
    # is read-only here and is never used for prices, sockets, or orders.
    p7_fundamental_pass_enabled: bool = False
    upstox_analytics_token: str = ""
    upstox_fundamentals_base_url: str = "https://api.upstox.com/v2"
    openrouter_api_key: str = ""
    openrouter_api_url: str = "https://openrouter.ai/api/v1/chat/completions"
    openrouter_model: str = "xiaomi/mimo-v2.5-pro"
    openrouter_prompt_version: str = "sepa_fundamentals_v1"
    openrouter_http_referer: str = ""
    openrouter_app_title: str = "SwingTraderVCP"
    fundamentals_snapshot_ttl_hours: int = Field(default=24, ge=1, le=168)
    fundamentals_max_concurrency: int = Field(default=5, ge=1, le=5)
    fundamentals_http_timeout_seconds: float = Field(default=20.0, gt=0, le=60)
    fundamentals_http_max_attempts: int = Field(default=3, ge=1, le=5)
    openrouter_max_tokens: int = Field(default=1600, ge=256, le=4096)
    openrouter_temperature: float = Field(default=0.1, ge=0, le=1)

    @field_validator("database_url", mode="before")
    @classmethod
    def convert_postgres_scheme(cls, v: str) -> str:
        if isinstance(v, str) and v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    model_config = {"env_file": ".env", "extra": "ignore"}



settings = Settings()
