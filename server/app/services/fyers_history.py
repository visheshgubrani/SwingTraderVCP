"""Account-wide History API pacing and bounded rate-limit recovery.

Redis owns admission/cooldown state; PostgreSQL remains the candle record.
All history consumers must use this wrapper, including read-only probes.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import settings

PREFIX = "fyers:history:budget"
# Redis time prevents clock skew between API and worker processes. Only an
# admitted request consumes quota; retries are admitted and counted separately.
ADMIT = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local blocked = tonumber(redis.call('HGET', KEYS[1], 'blocked') or '0')
if blocked > now then return {3, blocked} end
if blocked > 0 then
  redis.call('HDEL', KEYS[1], 'blocked', 'cooldown', 'probe', 'probe_until')
end
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now - 86400000)
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[2]) then
  local oldest = redis.call('ZRANGE', KEYS[2], 0, 0, 'WITHSCORES')
  return {4, tonumber(oldest[2]) + 86400000}
end
local cooldown = tonumber(redis.call('HGET', KEYS[1], 'cooldown') or '0')
if cooldown > now then return {2, cooldown} end
local probe_until = tonumber(redis.call('HGET', KEYS[1], 'probe_until') or '0')
if probe_until > now then return {2, probe_until} end
-- A crashed/timed-out recovery probe must not authorize more probes.
if probe_until > 0 then
  redis.call('HSET', KEYS[1], 'blocked', now + 86400000)
  return {3, now + 86400000}
end
local next_at = tonumber(redis.call('HGET', KEYS[1], 'next') or '0')
if next_at > now then return {1, next_at} end
if cooldown > 0 then
  redis.call('HSET', KEYS[1], 'probe', ARGV[3], 'probe_until', now + 120000)
end
redis.call('HSET', KEYS[1], 'next', now + tonumber(ARGV[1]))
redis.call('ZADD', KEYS[2], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], 172800000)
redis.call('PEXPIRE', KEYS[2], 172800000)
return {0, now}
"""
RESULT = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local probe = redis.call('HGET', KEYS[1], 'probe') == ARGV[1]
if ARGV[2] == '1' then
  if probe or ARGV[3] == '1' then
    redis.call('HSET', KEYS[1], 'blocked', now + 86400000)
  else
    redis.call('HSET', KEYS[1], 'cooldown', now + 65000)
  end
elseif probe then
  redis.call('HDEL', KEYS[1], 'cooldown', 'probe', 'probe_until')
end
redis.call('PEXPIRE', KEYS[1], 172800000)
return 1
"""


class HistoryUnavailable(RuntimeError):
    def __init__(self, message: str, retry_at: str | None = None):
        super().__init__(message)
        self.retry_at = retry_at


class HistoryCancelled(RuntimeError):
    pass


def timestamp(milliseconds: int) -> str:
    return dt.datetime.fromtimestamp(milliseconds / 1000, dt.timezone.utc).isoformat()


def rate_restriction(response: dict[str, Any]) -> tuple[bool, bool]:
    code = str(response.get("code", ""))
    message = str(response.get("message") or "").lower()
    limited = code in {"429", "-429", "-360"} or (
        response.get("s") != "ok"
        and any(
            term in message
            for term in (
                "rate limit",
                "too many requests",
                "daily limit",
                "daily quota",
            )
        )
    )
    hard = limited and any(
        term in message
        for term in (
            "daily",
            "per day",
            "day limit",
            "blocked",
            "remainder of",
            "rest of",
            "24 hour",
        )
    )
    return limited, hard


async def fetch_history(
    redis: Any,
    fyers: Any,
    payload: dict[str, Any],
    *,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
    on_wait: Callable[[str | None, str | None], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Pace every attempt; allow one shared recovery probe after a restriction."""
    previous_wait = None
    while True:
        if heartbeat:
            try:
                await heartbeat()
            except (HistoryCancelled, HistoryUnavailable):
                raise
            except Exception as exc:
                raise HistoryUnavailable(
                    "Fyers history paused: progress heartbeat failed."
                ) from exc
        request_id = uuid.uuid4().hex
        try:
            result = await redis.eval(
                ADMIT,
                2,
                PREFIX,
                PREFIX + ":requests",
                int(60000 / settings.fyers_data_requests_per_minute) + 1,
                settings.fyers_data_requests_per_day,
                request_id,
            )
            state, until = map(int, result)
        except Exception as exc:
            raise HistoryUnavailable(
                "Fyers history paused: Redis request budget is unavailable."
            ) from exc
        if state in {3, 4}:
            reason = (
                "Fyers history is restricted for 24 hours."
                if state == 3
                else "Fyers history rolling 24-hour request quota is exhausted."
            )
            raise HistoryUnavailable(reason, timestamp(until))
        if state:
            reason = "rate_limit" if state == 2 else "request_pacing"
            wait = (reason, timestamp(until))
            if on_wait and wait != previous_wait:
                await on_wait(*wait)
            previous_wait = wait
            # Short waits keep cancellation, lease renewal and status responsive.
            now = dt.datetime.now(dt.timezone.utc).timestamp() * 1000
            await asyncio.sleep(min(1.0, max(0.01, (until - now) / 1000)))
            continue
        if on_wait and previous_wait:
            await on_wait(None, None)
        async with asyncio.timeout(30):
            response = await fyers.history(data=payload)
        limited, hard = rate_restriction(response)
        try:
            await redis.eval(RESULT, 1, PREFIX, request_id, int(limited), int(hard))
        except Exception as exc:
            raise HistoryUnavailable(
                "Fyers history paused: Redis request budget is unavailable."
            ) from exc
        if not limited:
            return response
