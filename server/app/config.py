from typing import Literal, Self
from urllib.parse import quote
from decimal import Decimal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings


def build_asyncpg_database_url(
    *,
    user: str,
    password: str,
    host: str,
    port: int,
    database: str,
) -> str:
    """Build a SQLAlchemy asyncpg URL with a safely encoded password.

    SQLAlchemy's URL parser treats the first ``@`` as the end of userinfo, so a
    password containing ``@`` (e.g. ``MyP@ss``) would be misread as the host and
    fail DNS inside Compose.
    """
    return (
        f"postgresql+asyncpg://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{database}"
    )


def build_redis_url(
    *,
    host: str,
    port: int,
    password: str | None = None,
    db: int = 0,
) -> str:
    """Build a redis:// URL with a safely encoded password."""
    if password:
        return f"redis://:{quote(password, safe='')}@{host}:{port}/{db}"
    return f"redis://{host}:{port}/{db}"


class Settings(BaseSettings):
    # Fail closed when a deployment forgets to declare its environment.
    app_environment: Literal["development", "test", "production"] = "production"
    database_url: str = "postgresql+asyncpg://algo:algo@localhost:5480/algo_trading"
    # When set (Compose prod), rebuild database_url from discrete parts so
    # passwords with @/#/? do not corrupt the hostname.
    postgres_host: str | None = None
    postgres_port: int = 5432
    postgres_user: str = "algo"
    postgres_password: str | None = None
    postgres_db: str = "algo_trading"
    redis_url: str = "redis://localhost:6380/0"
    # When set (Compose prod), rebuild redis_url from discrete parts so
    # passwords with @/#/? do not corrupt the hostname.
    redis_host: str | None = None
    redis_port: int = 6379
    redis_password: str | None = None
    redis_db: int = 0
    # redis-py 8 defaults socket_timeout=5s; arq does not override it. Tune for long jobs.
    redis_socket_timeout: float = Field(default=30.0, gt=0, le=300)
    redis_health_check_interval: float = Field(default=30.0, ge=0, le=300)
    redis_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    redis_max_connections: int = Field(default=32, ge=8, le=256)
    sync_queued_stale_seconds: int = Field(default=300, ge=60, le=3600)
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ]
    # Single-user authentication password for personal trading workstation.
    app_password: str = ""
    session_ttl_seconds: int = Field(default=604800, ge=300, le=2592000)
    session_cookie_name: str = "swing_session"
    session_cookie_secure: bool | None = None
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    session_cookie_domain: str | None = None

    # SQL query logging (SEC-002). Must NEVER be true in production.
    sql_echo: bool = False
    # Public OpenAPI / Swagger documentation hardening (SEC-010).
    enable_docs_in_production: bool = False

    # Shared secret for Next BFF → FastAPI when requesting historical SaaS scans.
    # Leave empty in local dev to keep history locked until configured.
    saas_internal_api_key: str = ""

    fyers_app_id: str = ""
    fyers_secret_key: str = ""
    # Dedicated symmetric encryption key for broker tokens in Postgres (SEC-005).
    # Required in production. Local/dev may fall back to fyers_secret_key.
    token_encryption_key: str = ""

    @property
    def token_encryption_passphrase(self) -> str:
        if self.token_encryption_key:
            return self.token_encryption_key
        return self.fyers_secret_key or "antigravity-dev-token-encryption-key"

    fyers_redirect_uri: str = "http://127.0.0.1:3000/callback"
    # Where the GET /auth/callback browser bounce should land after Fyers OAuth.
    # Defaults to the personal Vite app; set to the public client URL on the VPS.
    frontend_public_url: str = "http://localhost:5173"
    # Directory for fyers-apiv3 FileHandlers (fyersApi.log / fyersRequests.log).
    # Must be writable by the process user — use /tmp in containers.
    fyers_log_path: str = "/tmp"

    scheduler_timezone: str = "Asia/Kolkata"
    eod_sync_enabled: bool = True
    eod_sync_hour: int = Field(default=18, ge=0, le=23)
    eod_sync_minute: int = Field(default=30, ge=0, le=59)
    # Fallback enqueue for SaaS Standard if the EOD chain was skipped.
    saas_standard_scan_fallback_enabled: bool = True
    saas_standard_scan_fallback_hour: int = Field(default=19, ge=0, le=23)
    saas_standard_scan_fallback_minute: int = Field(default=0, ge=0, le=59)
    # A running personal scan with no corresponding ARQ job is only recovered
    # after this grace period, avoiding false recovery during worker transitions.
    personal_scan_running_stale_seconds: int = Field(default=3600, ge=300, le=21600)

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
    # Fake P10 paper seed in INR. Keep aligned with deployable_capital_override.
    paper_initial_capital: Decimal = Decimal("100000")
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
    nse_fundamental_risk_enabled: bool = True
    nse_corporate_filings_base_url: str = "https://www.nseindia.com"
    openrouter_api_key: str = ""
    openrouter_api_url: str = "https://openrouter.ai/api/v1/chat/completions"
    # These stay runtime-configurable through server/.env. The defaults are
    # conservative but deployment may choose another supported OpenRouter model.
    openrouter_model: str = "openai/gpt-5.6-luna-pro"
    openrouter_reasoning_effort: Literal["low", "medium", "high", "xhigh"] = "medium"
    openrouter_prompt_version: str = "fundamental_second_opinion_v1"
    openrouter_http_referer: str = ""
    openrouter_app_title: str = "SwingTraderVCP"
    fundamentals_snapshot_ttl_hours: int = Field(default=24, ge=1, le=168)
    # P7 intentionally performs one company at a time; this remains an env
    # setting so an operator can only make it stricter, never concurrent.
    fundamentals_max_concurrency: int = Field(default=1, ge=1, le=1)
    fundamentals_http_timeout_seconds: float = Field(default=20.0, gt=0, le=60)
    fundamentals_http_max_attempts: int = Field(default=3, ge=1, le=5)
    openrouter_max_tokens: int = Field(default=3200, ge=256, le=4096)
    fundamental_run_token_budget: int = Field(default=1_500_000, ge=1_000, le=10_000_000)
    fundamental_prompt_max_chars: int = Field(default=12_000, ge=1_000, le=12_000)
    openrouter_http_timeout_seconds: float = Field(default=60.0, gt=0, le=120)
    # GPT-5.6 reasoning endpoints do not support sampling temperature. Keep
    # this opt-in for model overrides that do support it.
    openrouter_temperature: float | None = Field(default=None, ge=0, le=1)

    # On-demand VCP vision validator: advisory chart-image second opinion,
    # disabled by default. Enabling requires the OpenRouter key above; the
    # model is independently configurable and must support image input plus
    # strict structured output.
    vcp_vision_enabled: bool = False
    vcp_vision_model: str = "google/gemini-3.7-flash"
    vcp_vision_reasoning_effort: Literal[
        "low", "medium", "high", "xhigh"
    ] = "medium"
    vcp_vision_prompt_version: Literal["vcp_visual_validator_v1"] = (
        "vcp_visual_validator_v1"
    )
    vcp_vision_schema_version: Literal["vcp_visual_validator_result_v1"] = (
        "vcp_visual_validator_result_v1"
    )
    vcp_vision_renderer_version: Literal[
        "lightweight_charts_v5_log_1280x720_v1"
    ] = "lightweight_charts_v5_log_1280x720_v1"
    vcp_vision_context_sessions: int = Field(default=252, ge=126, le=504)
    vcp_vision_detail_sessions: int = Field(default=126, ge=63, le=252)
    vcp_vision_max_image_bytes: int = Field(
        default=3 * 1024 * 1024,
        ge=1024,
        le=8 * 1024 * 1024,
    )
    # Gemini 3 thinking tokens count against OpenRouter's completion budget
    # (medium effort routinely uses ~4k reasoning tokens). The cap must leave
    # room for the strict JSON verdict after thinking finishes; 4096 was too
    # tight and truncated the structured response with finish_reason=length.
    vcp_vision_max_tokens: int = Field(default=16384, ge=512, le=32768)

    # P10 proposal worker. It deliberately reuses the locked VCP vision model
    # setting but owns a separate serial queue and operational budget.
    proposal_automation_enabled: bool = False
    proposal_queue_name: str = "arq:queue:p10-proposals"
    proposal_batch_budget_minutes: int = Field(default=45, ge=5, le=120)
    # Testing default is 10. Restore the locked production top-20 with
    # PROPOSAL_BATCH_LIMIT=20. Hard-capped at 20 in the worker/queue.
    proposal_batch_limit: int = Field(default=10, ge=1, le=20)
    proposal_attempt_timeout_seconds: float = Field(default=90.0, ge=10, le=120)
    proposal_max_attempts: int = Field(default=2, ge=1, le=2)
    # Populate exchange holidays as JSON in the environment, for example
    # ["2026-01-26","2026-03-03"]. Weekends are always excluded.
    nse_trading_holidays: list[str] = Field(default_factory=list)

    @field_validator("database_url", mode="before")
    @classmethod
    def convert_postgres_scheme(cls, v: str) -> str:
        if isinstance(v, str) and v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @model_validator(mode="after")
    def assemble_database_url_from_parts(self) -> Self:
        if self.vcp_vision_detail_sessions > self.vcp_vision_context_sessions:
            raise ValueError(
                "VCP_VISION_DETAIL_SESSIONS cannot exceed "
                "VCP_VISION_CONTEXT_SESSIONS"
            )
        if self.postgres_host:
            if self.postgres_password is None:
                raise ValueError(
                    "POSTGRES_PASSWORD is required when POSTGRES_HOST is set"
                )
            self.database_url = build_asyncpg_database_url(
                user=self.postgres_user,
                password=self.postgres_password,
                host=self.postgres_host,
                port=self.postgres_port,
                database=self.postgres_db,
            )
        if self.redis_host:
            if not self.redis_password:
                raise ValueError(
                    "REDIS_PASSWORD is required when REDIS_HOST is set"
                )
            self.redis_url = build_redis_url(
                host=self.redis_host,
                port=self.redis_port,
                password=self.redis_password,
                db=self.redis_db,
            )

        # Security validations
        if self.app_environment == "production":
            if self.sql_echo:
                raise ValueError("SQL_ECHO cannot be enabled in production (SEC-002)")
            if not self.app_password or len(self.app_password.strip()) < 12:
                raise ValueError(
                    "APP_PASSWORD must be set and at least 12 characters in production (SEC-001)"
                )
            if not self.token_encryption_key or not self.token_encryption_key.strip():
                raise ValueError(
                    "TOKEN_ENCRYPTION_KEY is required in production environment; "
                    "refusing to fall back to broker secret."
                )
        else:
            if not self.app_password:
                self.app_password = "dev_swing_password_2026"

        if self.session_cookie_secure is None:
            self.session_cookie_secure = self.app_environment == "production"

        return self

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
