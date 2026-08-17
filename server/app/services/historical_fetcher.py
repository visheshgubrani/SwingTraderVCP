import asyncio
import datetime
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from arq.connections import ArqRedis
from fyers_apiv3 import fyersModel
from sqlalchemy import text

from app.config import settings
from app.database import async_session
from app.services.auth_service import AuthUnavailableError, get_valid_access_token
from app.services.distributed_lease import (
    acquire_distributed_lease,
    release_distributed_lease,
    renew_distributed_lease,
)
from app.services.scan_readiness import load_scan_readiness
from app.services.screening_config import TechnicalScreeningConfig

logger = logging.getLogger(__name__)

SYNC_STATUS_KEY = "historical:eod_sync:status"
SYNC_LOCK_KEY = "historical:eod_sync:lock"
SYNC_CANCEL_KEY = "historical:eod_sync:cancel"
SYNC_LOCK_SECONDS = 60 * 60
SYNC_STATUS_TTL_SECONDS = 60 * 60 * 2
INDIA_TZ = ZoneInfo("Asia/Kolkata")
EOD_AVAILABLE_AFTER = datetime.time(hour=18, minute=0)


@dataclass
class SyncProgress:
    run_id: str = ""
    state: str = "idle"
    triggered_by: str | None = None
    is_running: bool = False
    total_symbols: int = 0
    current_index: int = 0
    current_symbol: str = ""
    successful_symbols: int = 0
    skipped_symbols: int = 0
    candles_upserted: int = 0
    error_count: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    target_date: str | None = None
    started_at: str | None = None
    enqueued_at: str | None = None
    completed_at: str | None = None
    personal_scan_run_id: str | None = None

    def log(self, message: str) -> None:
        timestamp = datetime.datetime.now(INDIA_TZ).strftime("%H:%M:%S")
        self.logs.append(f"[{timestamp}] {message}")
        self.logs = self.logs[-300:]

    def add_error(self, symbol: str, error_message: str) -> None:
        self.error_count += 1
        if len(self.errors) < 100:
            self.errors.append(
                {
                    "symbol": symbol,
                    "error": error_message,
                    "timestamp": datetime.datetime.now(INDIA_TZ).isoformat(),
                }
            )
        self.log(f"ERROR ({symbol}): {error_message}")

    def finish(self, state: str, message: str) -> None:
        self.state = state
        self.is_running = False
        self.completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.current_symbol = ""
        self.log(message)


def empty_sync_status() -> dict[str, Any]:
    return asdict(SyncProgress())


async def get_sync_status(redis: ArqRedis) -> dict[str, Any]:
    raw_status = await redis.get(SYNC_STATUS_KEY)
    if not raw_status:
        return empty_sync_status()
    if isinstance(raw_status, bytes):
        raw_status = raw_status.decode()
    try:
        return json.loads(raw_status)
    except (TypeError, json.JSONDecodeError):
        logger.warning("Ignoring malformed historical sync status in Redis")
        return empty_sync_status()


async def save_sync_status(redis: ArqRedis, progress: SyncProgress) -> None:
    await redis.set(
        SYNC_STATUS_KEY,
        json.dumps(asdict(progress)),
        ex=SYNC_STATUS_TTL_SECONDS,
    )


def _parse_utc_timestamp(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


async def sync_status_blocks_enqueue(
    redis: ArqRedis,
    status: dict[str, Any],
    *,
    now: datetime.datetime | None = None,
) -> bool:
    """Return True when a new sync should be rejected as already active."""
    state = status.get("state")
    if state == "running":
        lock = await redis.get(SYNC_LOCK_KEY)
        return lock is not None

    if state == "queued":
        current = now or datetime.datetime.now(datetime.timezone.utc)
        enqueued_at = _parse_utc_timestamp(
            status.get("enqueued_at") or status.get("started_at")
        )
        if enqueued_at is None:
            return False
        age_seconds = (current - enqueued_at).total_seconds()
        return age_seconds < settings.sync_queued_stale_seconds

    return False


def build_date_chunks(
    start_date: datetime.date,
    end_date: datetime.date,
) -> list[tuple[datetime.date, datetime.date]]:
    """Split an inclusive date range into Fyers-safe chunks of at most 365 days."""
    chunks: list[tuple[datetime.date, datetime.date]] = []
    current_start = start_date
    while current_start <= end_date:
        current_end = min(current_start + datetime.timedelta(days=364), end_date)
        chunks.append((current_start, current_end))
        current_start = current_end + datetime.timedelta(days=1)
    return chunks


def next_sync_date(
    latest_candle_date: datetime.date | None,
    today: datetime.date,
    backfill_years: int,
) -> datetime.date:
    if latest_candle_date is not None:
        return latest_candle_date + datetime.timedelta(days=1)
    return today - datetime.timedelta(days=365 * backfill_years)


def sync_date_ranges(
    *,
    earliest_candle_date: datetime.date | None,
    latest_candle_date: datetime.date | None,
    target_date: datetime.date,
    backfill_years: int,
    repair_history: bool,
) -> list[tuple[datetime.date, datetime.date]]:
    """Plan missing prefix/suffix ranges without rewriting stored candles."""
    backfill_start = target_date - datetime.timedelta(days=365 * backfill_years)
    if earliest_candle_date is None or latest_candle_date is None:
        return [(backfill_start, target_date)]

    ranges: list[tuple[datetime.date, datetime.date]] = []
    if repair_history and earliest_candle_date > backfill_start:
        ranges.append(
            (backfill_start, earliest_candle_date - datetime.timedelta(days=1))
        )
    if latest_candle_date < target_date:
        ranges.append(
            (latest_candle_date + datetime.timedelta(days=1), target_date)
        )
    return ranges


def history_response_has_no_data(response: dict[str, Any]) -> bool:
    """Recognize Fyers' non-error response shape for pre-listing date ranges."""
    message = str(response.get("message") or "").lower()
    return response.get("s") == "no_data" or "no data" in message


def latest_completed_eod_date(
    now: datetime.datetime | None = None,
) -> datetime.date:
    """Return the latest date for which a daily candle can reasonably be final."""
    current = now or datetime.datetime.now(INDIA_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=INDIA_TZ)
    else:
        current = current.astimezone(INDIA_TZ)

    candidate = current.date()
    if current.timetz().replace(tzinfo=None) < EOD_AVAILABLE_AFTER:
        candidate -= datetime.timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= datetime.timedelta(days=1)
    return candidate


def sync_status_is_current(
    status: dict[str, Any],
    expected_target_date: datetime.date,
) -> bool:
    """Return whether a completed sync covered the current EOD target."""
    if status.get("target_date") != expected_target_date.isoformat():
        return False

    state = status.get("state")
    if state == "succeeded":
        return True
    if state != "partial":
        return False

    total_symbols = int(status.get("total_symbols") or 0)
    if total_symbols <= 0:
        return False
    tolerated_failures = max(1, total_symbols // 100)
    successful_symbols = int(status.get("successful_symbols") or 0)
    error_count = int(status.get("error_count") or 0)
    return (
        successful_symbols >= total_symbols - tolerated_failures
        and error_count <= tolerated_failures
    )


async def enqueue_eod_scans(
    redis: ArqRedis,
    progress: SyncProgress,
    target_date: datetime.date,
) -> None:
    """Queue P9→personal processing and the independent SaaS refresh."""
    try:
        job = await redis.enqueue_job(
            "run_market_context",
            target_date.isoformat(),
            True,
        )
        if job is None:
            raise RuntimeError("Redis did not accept the P9 market-context job")
        progress.log(
            f"P9 market context queued for {target_date.isoformat()}; "
            "the job will enqueue the personal scan after persistence."
        )
    except Exception as exc:
        logger.exception(
            "Failed to enqueue P9/personal chain after EOD sync for %s",
            target_date.isoformat(),
        )
        progress.log(f"ERROR (P9/personal chain): {type(exc).__name__}: {exc}")

    try:
        await redis.enqueue_job(
            "run_saas_global_standard_scan",
            target_date.isoformat(),
            "eod_chain",
            _job_id=f"saas-standard:{target_date.isoformat()}",
        )
        logger.info(
            "Enqueued SaaS Standard scan for as_of_date=%s after EOD sync",
            target_date.isoformat(),
        )
    except Exception:
        logger.exception(
            "Failed to enqueue SaaS Standard scan after EOD sync for %s",
            target_date.isoformat(),
        )


def _token_is_expired(expires_at: datetime.datetime) -> bool:
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
    return expires_at <= datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)


async def run_historical_sync(
    ctx: dict[str, Any],
    triggered_by: str = "scheduled",
    backfill_years: int = 1,
    run_id: str | None = None,
    repair_history: bool = False,
) -> dict[str, Any]:
    """Incrementally fetch daily Nifty 500 candles as an arq worker job."""
    redis: ArqRedis = ctx["redis"]
    effective_run_id = run_id or str(ctx.get("job_id", "scheduled"))
    target_date = latest_completed_eod_date()
    lock_owner = effective_run_id
    lock_acquired = await acquire_distributed_lease(
        redis,
        SYNC_LOCK_KEY,
        lock_owner,
        SYNC_LOCK_SECONDS,
    )
    if not lock_acquired:
        logger.info("Skipping historical sync because another sync owns the lock")
        return {"status": "already_running"}

    progress = SyncProgress(
        run_id=effective_run_id,
        state="running",
        triggered_by=triggered_by,
        is_running=True,
        target_date=target_date.isoformat(),
        started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
    sync_kind = "history-repair" if repair_history else "incremental"
    progress.log(f"Started {triggered_by} {sync_kind} EOD sync.")
    await save_sync_status(redis, progress)

    fyers = None
    try:
        if await redis.get(SYNC_CANCEL_KEY):
            progress.finish("cancelled", "Sync cancelled before it started.")
            await save_sync_status(redis, progress)
            return {"status": "cancelled"}

        try:
            access_token = await get_valid_access_token(redis)
        except AuthUnavailableError:
            progress.finish(
                "authentication_required",
                "Fyers authentication is required before EOD data can be synced.",
            )
            await save_sync_status(redis, progress)
            return {"status": "authentication_required"}

        async with async_session() as session:
            instruments_result = await session.execute(
                text(
                    """
                    SELECT
                        i.id,
                        i.symbol,
                        i.fyers_symbol,
                        (MIN(c.candle_start) AT TIME ZONE 'Asia/Kolkata')::date
                            AS earliest_candle_date,
                        (MAX(c.candle_start) AT TIME ZONE 'Asia/Kolkata')::date
                            AS latest_candle_date
                    FROM instruments i
                    LEFT JOIN universe_memberships m
                        ON m.instrument_id = i.id
                        AND m.universe_code = 'NIFTY500'
                        AND m.member_to IS NULL
                    LEFT JOIN market_candles c
                        ON c.instrument_id = i.id AND c.timeframe = '1d'
                    WHERE i.active = true
                      AND (
                            m.instrument_id IS NOT NULL
                            OR i.metadata ->> 'role' IN (
                                'benchmark',
                                'rs_benchmark',
                                'p9_trend_benchmark',
                                'p9_sector_index'
                            )
                          )
                    GROUP BY i.id, i.symbol, i.fyers_symbol
                    ORDER BY i.symbol ASC
                    """
                )
            )
            instruments = instruments_result.all()

        if not instruments:
            progress.finish("failed", "No active Nifty 500 instruments were found.")
            await save_sync_status(redis, progress)
            return {"status": "failed"}

        progress.total_symbols = len(instruments)
        await save_sync_status(redis, progress)

        fyers = fyersModel.FyersModel(
            is_async=True,
            client_id=settings.fyers_app_id,
            token=access_token,
            log_path=settings.fyers_log_path,
        )

        upsert_query = text(
            """
            INSERT INTO market_candles (
                instrument_id, timeframe, candle_start, open_price, high_price,
                low_price, close_price, volume, source, raw_payload
            )
            VALUES (
                :instrument_id, '1d', :candle_start, :open_price, :high_price,
                :low_price, :close_price, :volume, 'fyers', CAST(:raw_payload AS jsonb)
            )
            ON CONFLICT (instrument_id, timeframe, candle_start) DO UPDATE SET
                open_price = EXCLUDED.open_price,
                high_price = EXCLUDED.high_price,
                low_price = EXCLUDED.low_price,
                close_price = EXCLUDED.close_price,
                volume = EXCLUDED.volume,
                fetched_at = now(),
                raw_payload = EXCLUDED.raw_payload
            """
        )

        for index, instrument in enumerate(instruments, start=1):
            if await redis.get(SYNC_CANCEL_KEY):
                progress.finish("cancelled", "Sync cancelled by the user.")
                await save_sync_status(redis, progress)
                return {"status": "cancelled"}

            progress.current_index = index
            progress.current_symbol = instrument.symbol
            date_ranges = sync_date_ranges(
                earliest_candle_date=instrument.earliest_candle_date,
                latest_candle_date=instrument.latest_candle_date,
                target_date=target_date,
                backfill_years=backfill_years,
                repair_history=repair_history,
            )

            if not date_ranges:
                progress.skipped_symbols += 1
                progress.successful_symbols += 1
                progress.log(
                    f"{instrument.symbol} already has the requested EOD coverage."
                )
                await save_sync_status(redis, progress)
                continue

            symbol_candles = 0
            symbol_failed = False
            chunks = [
                (chunk_start, chunk_end, is_prefix)
                for range_start, range_end in date_ranges
                for is_prefix in [
                    instrument.earliest_candle_date is not None
                    and range_end < instrument.earliest_candle_date
                ]
                for chunk_start, chunk_end in build_date_chunks(
                    range_start,
                    range_end,
                )
            ]
            range_summary = ", ".join(
                f"{range_start.isoformat()}..{range_end.isoformat()}"
                for range_start, range_end in date_ranges
            )
            progress.log(f"Syncing {instrument.symbol}: {range_summary}.")

            for chunk_start, chunk_end, is_prefix in chunks:
                payload = {
                    "symbol": instrument.fyers_symbol,
                    "resolution": "D",
                    "date_format": "1",
                    "range_from": chunk_start.isoformat(),
                    "range_to": chunk_end.isoformat(),
                    "cont_flag": "1",
                }
                try:
                    response = await fyers.history(data=payload)
                    if response.get("s") != "ok":
                        if is_prefix and history_response_has_no_data(response):
                            progress.log(
                                f"{instrument.symbol}: no Fyers candles before "
                                f"{instrument.earliest_candle_date}; treating it "
                                "as listing-limited history."
                            )
                        else:
                            symbol_failed = True
                            progress.add_error(
                                instrument.symbol,
                                response.get(
                                    "message",
                                    "Fyers history API returned an error.",
                                ),
                            )
                    else:
                        candles = response.get("candles", [])
                        if candles:
                            candle_rows = [
                                {
                                    "instrument_id": instrument.id,
                                    "candle_start": datetime.datetime.fromtimestamp(
                                        int(candle[0]),
                                        tz=datetime.timezone.utc,
                                    ),
                                    "open_price": float(candle[1]),
                                    "high_price": float(candle[2]),
                                    "low_price": float(candle[3]),
                                    "close_price": float(candle[4]),
                                    "volume": int(candle[5]),
                                    "raw_payload": json.dumps({"c": candle}),
                                }
                                for candle in candles
                            ]
                            async with async_session() as session:
                                await session.execute(upsert_query, candle_rows)
                                await session.commit()
                            symbol_candles += len(candle_rows)
                except Exception as exc:
                    symbol_failed = True
                    progress.add_error(
                        instrument.symbol,
                        f"{type(exc).__name__}: {exc}",
                    )

                await asyncio.sleep(0.35)

            progress.candles_upserted += symbol_candles
            if not symbol_failed:
                progress.successful_symbols += 1
            progress.log(f"{instrument.symbol}: saved {symbol_candles} new EOD candles.")
            renewed = await renew_distributed_lease(
                redis,
                SYNC_LOCK_KEY,
                lock_owner,
                SYNC_LOCK_SECONDS,
            )
            if not renewed:
                progress.finish(
                    "failed",
                    "Lost EOD sync lease; another run may have taken over.",
                )
                await save_sync_status(redis, progress)
                return {"status": "lock_lost"}
            await save_sync_status(redis, progress)

        final_state = "partial" if progress.error_count else "succeeded"
        progress.finish(
            final_state,
            f"Sync complete: {progress.candles_upserted} candles saved for "
            f"{progress.successful_symbols}/{progress.total_symbols} symbols.",
        )
        date_coverage_ready = sync_status_is_current(asdict(progress), target_date)
        async with async_session() as session:
            readiness = await load_scan_readiness(
                session,
                reference_eod_date=target_date,
                minimum_history_days=(
                    TechnicalScreeningConfig().minimum_history_days
                ),
            )
        scan_ready = date_coverage_ready and readiness.scanner_ready

        if scan_ready:
            await enqueue_eod_scans(redis, progress, target_date)
        else:
            progress.log(
                "Scanner jobs were not queued: "
                f"{readiness.scoreable_instruments}/"
                f"{readiness.active_instruments} instruments are scoreable; "
                f"{readiness.required_scoreable_instruments} required."
            )

        await save_sync_status(redis, progress)

        return {
            "status": final_state,
            "candles_upserted": progress.candles_upserted,
            "personal_scan_run_id": progress.personal_scan_run_id,
            "scanner_ready": scan_ready,
        }
    except Exception as exc:
        logger.exception("Historical EOD sync failed")
        progress.finish("failed", f"Sync failed: {exc}")
        await save_sync_status(redis, progress)
        raise
    finally:
        if fyers is not None:
            try:
                await fyers.close()
            except Exception:
                logger.debug("Failed to close Fyers client cleanly", exc_info=True)
        await redis.delete(SYNC_CANCEL_KEY)
        await release_distributed_lease(redis, SYNC_LOCK_KEY, lock_owner)
