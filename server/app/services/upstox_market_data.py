"""Read-only Upstox market-data adapter (Analytics Token, v3 REST).

The adapter is duck-typed like the Fyers SDK client used by the history call
sites: it accepts the same Fyers-shaped history payload and returns the same
Fyers-shaped ``{"s": "ok", "candles": [[epoch, o, h, l, c, v], ...]}``
response, so EOD sync, 5-minute reconciliation, and probes keep one code path.
Daily epochs follow the stored Fyers convention (UTC midnight of the NSE
trading date); intraday epochs are the bar-start instant.

Never places orders, never reads account/portfolio state, and never connects
an Upstox WebSocket. Every HTTP request is admitted through a shared Redis
budget (``fyers_history.paced_request``).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text

from app.config import settings
from app.domain.upstox_instrument_keys import encode_key, resolve_instrument_key
from app.services.fyers_history import (
    HistoryBudget,
    HistoryUnavailable,
    paced_request,
)

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

UPSTOX_AUTH_FAILED_KEY = "market_data:upstox:auth_failed"
UPSTOX_AUTH_FAILED_TTL_SECONDS = 6 * 60 * 60
_EVENT_COOLDOWN_PREFIX = "market_data:upstox:event_cooldown:"
LTP_MAX_KEYS = 500
INTRADAY_WINDOW_DAYS = 28
DAILY_CHUNK_DAYS = 3650


class UpstoxMarketDataAuthError(HistoryUnavailable):
    """The Analytics Token is missing, expired, or rejected (401/403)."""


def bulk_budget() -> HistoryBudget:
    return HistoryBudget(
        prefix="upstox:md:budget:bulk",
        label="Upstox history",
        spacing_ms=int(60000 / settings.upstox_bulk_requests_per_minute) + 1,
        window_cap=settings.upstox_bulk_requests_per_30min,
        window_ms=30 * 60 * 1000,
        block_ms=30 * 60 * 1000,
    )


def realtime_budget() -> HistoryBudget:
    return HistoryBudget(
        prefix="upstox:md:budget:realtime",
        label="Upstox LTP",
        spacing_ms=max(1, int(settings.upstox_ltp_poll_seconds * 1000) - 100),
        window_cap=1000,
        window_ms=30 * 60 * 1000,
        block_ms=5 * 60 * 1000,
    )


async def record_upstox_auth_failure(redis: Any, detail: str) -> None:
    """Flag the token as rejected and emit one cooldown-bounded critical event."""
    try:
        await redis.set(UPSTOX_AUTH_FAILED_KEY, detail[:200], ex=UPSTOX_AUTH_FAILED_TTL_SECONDS)
        if not await redis.set(
            _EVENT_COOLDOWN_PREFIX + "auth_rejected",
            "1",
            ex=settings.auth_event_cooldown_seconds,
            nx=True,
        ):
            return
    except Exception:
        logger.warning("Upstox auth-failure flag could not be written", exc_info=True)
    try:
        from app.database import async_session

        async with async_session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO system_events (component, severity, event_type, payload)
                    VALUES ('market_data', 'critical', 'upstox_market_data_auth_rejected',
                            CAST(:payload AS jsonb))
                    """
                ),
                {"payload": json.dumps({"detail": detail[:200]})},
            )
            await session.commit()
    except Exception:
        logger.warning("Upstox auth-failure event could not be persisted", exc_info=True)


async def clear_upstox_auth_failure(redis: Any) -> None:
    try:
        await redis.delete(UPSTOX_AUTH_FAILED_KEY)
    except Exception:
        logger.debug("Upstox auth flag clear failed", exc_info=True)


async def upstox_auth_failed(redis: Any) -> bool:
    try:
        return bool(await redis.get(UPSTOX_AUTH_FAILED_KEY))
    except Exception:
        return False


async def load_instrument_keys(
    session: Any, fyers_symbols: Iterable[str] | None = None
) -> dict[str, str]:
    """Map every resolvable fyers_symbol (optionally filtered) to its key."""
    params: dict[str, Any] = {}
    where = ""
    if fyers_symbols is not None:
        symbols = sorted(set(fyers_symbols))
        if not symbols:
            return {}
        where = "WHERE fyers_symbol = ANY(:symbols)"
        params["symbols"] = symbols
    rows = (
        await session.execute(
            text(f"SELECT fyers_symbol, isin, metadata FROM instruments {where}"),
            params,
        )
    ).all()
    keys: dict[str, str] = {}
    for row in rows:
        metadata = row.metadata if isinstance(row.metadata, dict) else None
        key = resolve_instrument_key(row.fyers_symbol, row.isin, metadata)
        if key:
            keys[row.fyers_symbol] = key
    return keys


def _ist_today(now: dt.datetime | None = None) -> dt.date:
    current = now or dt.datetime.now(IST)
    return current.astimezone(IST).date()


def _parse_ts(value: Any) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed


def _daily_row(candle: list[Any]) -> list[Any]:
    session_date = _parse_ts(candle[0]).astimezone(IST).date()
    epoch = int(
        dt.datetime.combine(session_date, dt.time(), dt.timezone.utc).timestamp()
    )
    return [epoch, candle[1], candle[2], candle[3], candle[4], int(candle[5] or 0)]


def _intraday_row(candle: list[Any]) -> list[Any]:
    epoch = int(_parse_ts(candle[0]).timestamp())
    return [epoch, candle[1], candle[2], candle[3], candle[4], int(candle[5] or 0)]


class UpstoxMarketDataClient:
    """Read-only v3 historical/intraday/LTP client keyed by fyers_symbol."""

    daily_chunk_days = DAILY_CHUNK_DAYS
    provider = "upstox"

    def __init__(
        self,
        token: str,
        key_map: dict[str, str],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self._token = token
        self.key_map = dict(key_map)
        self._clock = clock or (lambda: dt.datetime.now(IST))
        self._http = httpx.AsyncClient(
            base_url=settings.upstox_api_base_url,
            timeout=settings.upstox_http_timeout_seconds,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """One HTTP GET → Fyers-shaped status dict. 401/403 raise, never retry."""
        last_error = "Upstox request failed"
        for attempt in range(2):
            try:
                response = await self._http.get(path, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"Upstox transport error: {type(exc).__name__}"
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            if response.status_code in {401, 403}:
                raise UpstoxMarketDataAuthError(
                    f"Upstox market data rejected the Analytics Token (HTTP {response.status_code})."
                )
            if response.status_code == 429:
                return {"s": "error", "code": "429", "message": "Upstox too many requests"}
            if response.status_code >= 500:
                last_error = f"Upstox HTTP {response.status_code}"
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            try:
                body = response.json()
            except ValueError:
                body = {}
            if response.status_code >= 400 or body.get("status") != "success":
                errors = body.get("errors") if isinstance(body, dict) else None
                message = (
                    (errors[0].get("message") if errors and isinstance(errors[0], dict) else None)
                    or f"Upstox HTTP {response.status_code}"
                )
                return {"s": "error", "code": str(response.status_code), "message": message}
            return {"s": "ok", "data": body.get("data") or {}}
        return {"s": "error", "code": "5xx", "message": last_error}

    async def _paced(
        self,
        redis: Any,
        path: str,
        *,
        budget: HistoryBudget,
        params: dict[str, Any] | None = None,
        heartbeat: Callable[[], Awaitable[None]] | None = None,
        on_wait: Callable[[str | None, str | None], Awaitable[None]] | None = None,
        max_wait_seconds: float | None = None,
    ) -> dict[str, Any]:
        try:
            response = await paced_request(
                redis,
                budget,
                lambda: self._request(path, params),
                heartbeat=heartbeat,
                on_wait=on_wait,
                max_wait_seconds=max_wait_seconds,
            )
        except UpstoxMarketDataAuthError as exc:
            await record_upstox_auth_failure(redis, str(exc))
            raise
        return response

    async def paced_history(
        self,
        redis: Any,
        payload: dict[str, Any],
        *,
        heartbeat: Callable[[], Awaitable[None]] | None = None,
        on_wait: Callable[[str | None, str | None], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        symbol = str(payload.get("symbol") or "")
        key = self.key_map.get(symbol)
        if not key:
            return {
                "s": "error",
                "message": f"No Upstox instrument key for {symbol} (missing ISIN or unmapped index).",
            }
        start = dt.date.fromisoformat(str(payload["range_from"]))
        end = dt.date.fromisoformat(str(payload["range_to"]))
        resolution = str(payload.get("resolution"))
        enc = encode_key(key)
        kwargs = {"budget": bulk_budget(), "heartbeat": heartbeat, "on_wait": on_wait}

        if resolution == "D":
            response = await self._paced(
                redis,
                f"/v3/historical-candle/{enc}/days/1/{end.isoformat()}/{start.isoformat()}",
                **kwargs,
            )
            if response["s"] != "ok":
                return response
            rows = {r[0]: r for r in map(_daily_row, response["data"].get("candles") or [])}
            now = self._clock()
            today = _ist_today(now)
            today_epoch = int(dt.datetime.combine(today, dt.time(), dt.timezone.utc).timestamp())
            # The historical endpoint can lag the close; after 16:00 IST the
            # intraday day-candle carries today's completed session.
            if (
                end >= today
                and today_epoch not in rows
                and now.astimezone(IST).time() >= dt.time(16, 0)
                and today.weekday() < 5
            ):
                intraday = await self._paced(
                    redis, f"/v3/historical-candle/intraday/{enc}/days/1", **kwargs
                )
                if intraday["s"] == "ok":
                    for candle in intraday["data"].get("candles") or []:
                        row = _daily_row(candle)
                        if row[0] == today_epoch:
                            rows[row[0]] = row
            return {"s": "ok", "candles": [rows[k] for k in sorted(rows)]}

        if resolution == "5":
            today = _ist_today(self._clock())
            rows: dict[int, list[Any]] = {}
            past_end = min(end, today - dt.timedelta(days=1))
            window_start = start
            while window_start <= past_end:
                window_end = min(window_start + dt.timedelta(days=INTRADAY_WINDOW_DAYS - 1), past_end)
                response = await self._paced(
                    redis,
                    f"/v3/historical-candle/{enc}/minutes/5/{window_end.isoformat()}/{window_start.isoformat()}",
                    **kwargs,
                )
                if response["s"] != "ok":
                    return response
                for candle in response["data"].get("candles") or []:
                    row = _intraday_row(candle)
                    rows[row[0]] = row
                window_start = window_end + dt.timedelta(days=1)
            if end >= today:
                response = await self._paced(
                    redis, f"/v3/historical-candle/intraday/{enc}/minutes/5", **kwargs
                )
                if response["s"] != "ok":
                    return response
                for candle in response["data"].get("candles") or []:
                    row = _intraday_row(candle)
                    rows[row[0]] = row
            return {"s": "ok", "candles": [rows[k] for k in sorted(rows)]}

        return {"s": "error", "message": f"Unsupported resolution {resolution!r} for Upstox."}

    async def fetch_ltp(
        self,
        redis: Any,
        symbols: Iterable[str],
        *,
        max_wait_seconds: float | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Return {fyers_symbol: {last_price, volume, cp, ltq}} for mapped symbols."""
        by_key = {self.key_map[s]: s for s in symbols if s in self.key_map}
        keys = sorted(by_key)
        quotes: dict[str, dict[str, Any]] = {}
        for i in range(0, len(keys), LTP_MAX_KEYS):
            await self._fetch_ltp_batch(
                redis, keys[i : i + LTP_MAX_KEYS], by_key, quotes, max_wait_seconds
            )
        return quotes

    async def _fetch_ltp_batch(
        self,
        redis: Any,
        keys: list[str],
        by_key: dict[str, str],
        quotes: dict[str, dict[str, Any]],
        max_wait_seconds: float | None,
    ) -> None:
        response = await self._paced(
            redis,
            "/v3/market-quote/ltp",
            budget=realtime_budget(),
            params={"instrument_key": ",".join(keys)},
            max_wait_seconds=max_wait_seconds,
        )
        if response["s"] != "ok":
            # One bad key 400s the whole batch: bisect to isolate it.
            if response.get("code") == "400" and len(keys) > 1:
                mid = len(keys) // 2
                await self._fetch_ltp_batch(redis, keys[:mid], by_key, quotes, max_wait_seconds)
                await self._fetch_ltp_batch(redis, keys[mid:], by_key, quotes, max_wait_seconds)
                return
            if response.get("code") == "400":
                logger.warning("Upstox LTP rejected key %s: %s", keys[0], response.get("message"))
                return
            raise HistoryUnavailable(str(response.get("message") or "Upstox LTP failed"))
        for value in (response["data"] or {}).values():
            if not isinstance(value, dict):
                continue
            # Response keys look like "NSE_EQ:RELIANCE"; map back by token.
            symbol = by_key.get(str(value.get("instrument_token") or ""))
            if symbol:
                quotes[symbol] = value


async def open_upstox_client(
    redis: Any, fyers_symbols: Iterable[str] | None = None
) -> UpstoxMarketDataClient:
    """Build a client with keys from Postgres; fail closed without a token."""
    token = settings.upstox_analytics_token.strip()
    if not token:
        raise UpstoxMarketDataAuthError("UPSTOX_ANALYTICS_TOKEN is not configured.")
    if await upstox_auth_failed(redis):
        raise UpstoxMarketDataAuthError(
            "Upstox market data authentication was rejected recently; "
            "update UPSTOX_ANALYTICS_TOKEN and restart."
        )
    from app.database import async_session

    async with async_session() as session:
        key_map = await load_instrument_keys(session, fyers_symbols)
    return UpstoxMarketDataClient(token, key_map)
