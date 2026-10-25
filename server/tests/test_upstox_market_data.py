"""Upstox market-data adapter: keys, history shape, LTP mapping, auth, ticks."""

import datetime as dt
import json
import pathlib
import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import httpx

from app.domain import upstox_instrument_keys as keys
from app.services import fyers_history as h
from app.services import upstox_market_data as um
from app.workers import tick_worker as tw

IST = ZoneInfo("Asia/Kolkata")
SERVER = pathlib.Path(__file__).resolve().parents[1]


def budget_redis() -> SimpleNamespace:
    """Redis double that always admits and records results."""

    async def eval_(script, *args):
        return [0, 0] if script is h.ADMIT else 1

    return SimpleNamespace(
        eval=AsyncMock(side_effect=eval_),
        get=AsyncMock(return_value=None),
        set=AsyncMock(return_value=True),
        delete=AsyncMock(),
    )


def client_with(handler, *, now: dt.datetime, key_map=None) -> um.UpstoxMarketDataClient:
    return um.UpstoxMarketDataClient(
        "token",
        key_map or {"NSE:RELIANCE-EQ": "NSE_EQ|INE002A01018"},
        transport=httpx.MockTransport(handler),
        clock=lambda: now,
    )


class InstrumentKeyTests(unittest.TestCase):
    def test_every_index_literal_in_code_and_schema_is_mapped(self):
        pattern = re.compile(r"NSE:[A-Z0-9&-]+-INDEX")
        found: set[str] = set()
        for root in ("app", "db"):
            for path in (SERVER / root).rglob("*"):
                if path.suffix in {".py", ".sql"}:
                    found |= set(pattern.findall(path.read_text(errors="ignore")))
        self.assertTrue(found)
        self.assertEqual(found - set(keys.UPSTOX_INDEX_KEYS), set())

    def test_equity_key_from_isin_and_override_precedence(self):
        self.assertEqual(
            keys.resolve_instrument_key("NSE:RELIANCE-EQ", "INE002A01018"),
            "NSE_EQ|INE002A01018",
        )
        self.assertIsNone(keys.resolve_instrument_key("NSE:X-EQ", "bad"))
        self.assertIsNone(keys.resolve_instrument_key("NSE:UNKNOWN-INDEX", None))
        self.assertEqual(
            keys.resolve_instrument_key(
                "NSE:RELIANCE-EQ", "INE002A01018", {"upstox_instrument_key": "NSE_EQ|OVERRIDE1"}
            ),
            "NSE_EQ|OVERRIDE1",
        )
        self.assertEqual(keys.encode_key("NSE_INDEX|Nifty 50"), "NSE_INDEX%7CNifty%2050")


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_candles_use_stored_utc_midnight_convention_and_sort(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(unquote(request.url.path))
            return httpx.Response(200, json={"status": "success", "data": {"candles": [
                ["2026-10-23T00:00:00+05:30", 1184.0, 1201.9, 1163.1, 1190.0, 14905641, 0],
                ["2026-10-22T00:00:00+05:30", 1206.2, 1208.0, 1184.5, 1184.5, 14957042, 0],
            ]}})

        client = client_with(handler, now=dt.datetime(2026, 10, 24, 10, tzinfo=IST))
        response = await h.fetch_history(budget_redis(), client, {
            "symbol": "NSE:RELIANCE-EQ", "resolution": "D",
            "range_from": "2026-10-20", "range_to": "2026-10-23",
        })
        await client.close()
        self.assertEqual(response["s"], "ok")
        stamps = [c[0] for c in response["candles"]]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(
            dt.datetime.fromtimestamp(stamps[-1], dt.timezone.utc),
            dt.datetime(2026, 10, 23, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(response["candles"][-1][5], 14905641)
        self.assertEqual(seen, ["/v3/historical-candle/NSE_EQ|INE002A01018/days/1/2026-10-23/2026-10-20"])

    async def test_today_daily_candle_falls_back_to_intraday_after_close(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "/intraday/" in request.url.path:
                return httpx.Response(200, json={"status": "success", "data": {"candles": [
                    ["2026-10-26T00:00:00+05:30", 1, 2, 0.5, 1.5, 99, 0]]}})
            return httpx.Response(200, json={"status": "success", "data": {"candles": []}})

        client = client_with(handler, now=dt.datetime(2026, 10, 26, 18, 30, tzinfo=IST))
        response = await h.fetch_history(budget_redis(), client, {
            "symbol": "NSE:RELIANCE-EQ", "resolution": "D",
            "range_from": "2026-10-26", "range_to": "2026-10-26",
        })
        await client.close()
        self.assertEqual(len(response["candles"]), 1)
        self.assertEqual(response["candles"][0][5], 99)

    async def test_five_minute_splits_windows_merges_today_and_keeps_bar_start(self):
        paths = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = unquote(request.url.path)
            paths.append(path)
            if "/intraday/" in path:
                candles = [["2026-10-26T09:20:00+05:30", 3, 3, 3, 3, 30, 0],
                           ["2026-10-26T09:15:00+05:30", 2, 2, 2, 2, 20, 0]]
            elif path.endswith("2026-10-25/2026-10-04"):
                candles = [["2026-10-23T15:25:00+05:30", 1, 1, 1, 1, 10, 0]]
            else:
                candles = []
            return httpx.Response(200, json={"status": "success", "data": {"candles": candles}})

        client = client_with(handler, now=dt.datetime(2026, 10, 26, 9, 31, tzinfo=IST))
        response = await h.fetch_history(budget_redis(), client, {
            "symbol": "NSE:RELIANCE-EQ", "resolution": "5",
            "range_from": "2026-09-06", "range_to": "2026-10-26",
        })
        await client.close()
        self.assertEqual(len(paths), 3)  # two ≤28-day windows + intraday
        self.assertIn("/v3/historical-candle/NSE_EQ|INE002A01018/minutes/5/2026-10-03/2026-09-06", paths)
        starts = [dt.datetime.fromtimestamp(c[0], IST) for c in response["candles"]]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual(starts[-1], dt.datetime(2026, 10, 26, 9, 20, tzinfo=IST))

    async def test_missing_key_is_a_per_symbol_error(self):
        client = client_with(lambda r: httpx.Response(500), now=dt.datetime.now(IST))
        response = await h.fetch_history(budget_redis(), client, {
            "symbol": "NSE:NOPE-EQ", "resolution": "D",
            "range_from": "2026-10-01", "range_to": "2026-10-02",
        })
        await client.close()
        self.assertNotEqual(response["s"], "ok")
        self.assertIn("No Upstox instrument key", response["message"])

    async def test_auth_rejection_raises_once_and_flags_redis(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(401, json={"status": "error"})

        redis = budget_redis()
        client = client_with(handler, now=dt.datetime.now(IST))
        with patch.object(um, "record_upstox_auth_failure", new=AsyncMock()) as record:
            with self.assertRaises(um.UpstoxMarketDataAuthError):
                await h.fetch_history(redis, client, {
                    "symbol": "NSE:RELIANCE-EQ", "resolution": "D",
                    "range_from": "2026-10-01", "range_to": "2026-10-02",
                })
        await client.close()
        self.assertEqual(len(calls), 1)
        record.assert_awaited_once()

    async def test_rate_limit_maps_to_budget_cooldown_then_succeeds(self):
        responses = iter([
            httpx.Response(429),
            httpx.Response(200, json={"status": "success", "data": {"candles": []}}),
        ])
        results = []

        async def eval_(script, *args):
            if script is h.RESULT:
                results.append(args)
                return 1
            return [0, 0]

        redis = budget_redis()
        redis.eval = AsyncMock(side_effect=eval_)
        client = client_with(lambda r: next(responses), now=dt.datetime(2026, 10, 24, tzinfo=IST))
        response = await h.fetch_history(redis, client, {
            "symbol": "NSE:RELIANCE-EQ", "resolution": "D",
            "range_from": "2026-10-01", "range_to": "2026-10-02",
        })
        await client.close()
        self.assertEqual(response, {"s": "ok", "candles": []})
        self.assertEqual(results[0][3], 1)  # first result reported as limited
        self.assertEqual(results[0][1], "upstox:md:budget:bulk")


class LtpTests(unittest.IsolatedAsyncioTestCase):
    async def test_maps_back_by_instrument_token_and_bisects_bad_keys(self):
        key_map = {
            "NSE:RELIANCE-EQ": "NSE_EQ|INE002A01018",
            "NSE:NIFTY50-INDEX": "NSE_INDEX|Nifty 50",
            "NSE:BAD-EQ": "NSE_EQ|INE000000000",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            requested = request.url.params["instrument_key"].split(",")
            if "NSE_EQ|INE000000000" in requested:
                return httpx.Response(400, json={"status": "error", "errors": [{"message": "Invalid key"}]})
            data = {}
            if "NSE_EQ|INE002A01018" in requested:
                data["NSE_EQ:RELIANCE"] = {"last_price": 1190.0, "instrument_token": "NSE_EQ|INE002A01018",
                                           "ltq": 1, "volume": 14905641, "cp": 1184.5}
            if "NSE_INDEX|Nifty 50" in requested:
                data["NSE_INDEX:Nifty 50"] = {"last_price": 22600.0, "instrument_token": "NSE_INDEX|Nifty 50",
                                              "ltq": 0, "volume": 0, "cp": 22687.65}
            return httpx.Response(200, json={"status": "success", "data": data})

        client = client_with(handler, now=dt.datetime.now(IST), key_map=key_map)
        quotes = await client.fetch_ltp(budget_redis(), list(key_map))
        await client.close()
        self.assertEqual(set(quotes), {"NSE:RELIANCE-EQ", "NSE:NIFTY50-INDEX"})
        self.assertEqual(quotes["NSE:RELIANCE-EQ"]["volume"], 14905641)


class TickShapeTests(unittest.TestCase):
    def test_upstox_tick_matches_fyers_tick_fields(self):
        received = dt.datetime(2026, 10, 26, 4, 0, tzinfo=dt.timezone.utc)
        tick = tw.build_upstox_tick(
            "NSE:RELIANCE-EQ",
            {"last_price": 1190.0, "volume": 1000, "cp": 1184.5, "ltq": 1},
            received,
        )
        fyers_fields = {
            "symbol", "ltp", "volume", "bid", "ask", "open", "high", "low",
            "prev_close", "change", "change_pct", "timestamp", "received_at",
        }
        self.assertEqual(set(tick), fyers_fields)
        self.assertEqual(tick["ltp"], 1190.0)
        self.assertEqual(tick["volume"], 1000)
        self.assertAlmostEqual(tick["change_pct"], round(5.5 / 1184.5 * 100, 4))
        self.assertEqual(dt.datetime.fromisoformat(tick["received_at"]), received)
        self.assertIsNone(tw.build_upstox_tick("X", {"last_price": 0}, received))
        self.assertIsNone(
            tw.build_upstox_tick("X", {"last_price": 5, "cp": 0}, received)["change_pct"]
        )

    def test_auth_error_markers_are_word_bounded(self):
        self.assertFalse(tw.is_fyers_ws_auth_error("invalid utf-8 start byte"))
        self.assertFalse(tw.is_fyers_ws_auth_error("connection reset at 2026-10-15"))
        self.assertTrue(tw.is_fyers_ws_auth_error({"code": -8, "message": "x"}))
        self.assertTrue(tw.is_fyers_ws_auth_error("Unauthorized"))
        self.assertTrue(tw.is_fyers_ws_auth_error("code: -15"))

    def test_poll_window_excludes_weekends_holidays_and_off_hours(self):
        with patch.object(tw.settings, "nse_trading_holidays", ["2026-11-10"]):
            self.assertTrue(tw.in_upstox_poll_window(dt.datetime(2026, 10, 26, 10, 0, tzinfo=IST)))
            self.assertFalse(tw.in_upstox_poll_window(dt.datetime(2026, 10, 26, 8, 59, tzinfo=IST)))
            self.assertFalse(tw.in_upstox_poll_window(dt.datetime(2026, 10, 26, 15, 36, tzinfo=IST)))
            self.assertFalse(tw.in_upstox_poll_window(dt.datetime(2026, 10, 25, 10, 0, tzinfo=IST)))
            self.assertFalse(tw.in_upstox_poll_window(dt.datetime(2026, 11, 10, 10, 0, tzinfo=IST)))


class PaperPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_paper_upstox_skips_fyers_session_entirely(self):
        from app.workers import entry_supervisor as es

        sentinel = object()
        with (
            patch.object(es.settings, "execution_mode", "paper"),
            patch.object(es.settings, "market_data_provider", "upstox"),
            patch.object(es, "ensure_session_ready", new=AsyncMock(side_effect=AssertionError)) as fyers_gate,
            patch.object(es, "ensure_upstox_market_data_ready", new=AsyncMock()) as upstox_gate,
            patch.object(es, "fetch_paper_preflight", new=AsyncMock(return_value=sentinel)),
            patch.object(es, "async_session") as session,
        ):
            session.return_value.__aenter__.return_value = object()
            result = await es._fetch_broker_preflight(budget_redis())
        self.assertIs(result, sentinel)
        fyers_gate.assert_not_awaited()
        upstox_gate.assert_awaited_once()

    async def test_live_still_verifies_fyers_session(self):
        from app.workers import entry_supervisor as es

        with (
            patch.object(es.settings, "execution_mode", "live"),
            patch.object(es.settings, "market_data_provider", "upstox"),
            patch.object(es, "ensure_session_ready", new=AsyncMock(side_effect=RuntimeError("stop"))) as fyers_gate,
        ):
            with self.assertRaises(RuntimeError):
                await es._fetch_broker_preflight(budget_redis())
        self.assertTrue(fyers_gate.await_args.kwargs["verify"])

    async def test_upstox_readiness_fails_closed_without_token(self):
        from app.services import auth_readiness
        from app.services.auth_service import AuthUnavailableError

        with patch.object(auth_readiness, "logger"), patch(
            "app.config.settings.upstox_analytics_token", ""
        ):
            with self.assertRaises(AuthUnavailableError) as ctx:
                await auth_readiness.ensure_upstox_market_data_ready(budget_redis())
        self.assertIn("authentication", str(ctx.exception))


class GuardTests(unittest.TestCase):
    def test_token_days_left_prefers_env_then_jwt_exp(self):
        import base64

        from app.services.token_refresh import upstox_token_days_left

        today = dt.date(2026, 10, 25)
        self.assertEqual(upstox_token_days_left("x", "2026-11-04", today), 10)
        exp = int(dt.datetime(2026, 11, 1, 12, tzinfo=IST).timestamp())
        body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
        self.assertEqual(upstox_token_days_left(f"h.{body}.s", None, today), 7)
        self.assertIsNone(upstox_token_days_left("opaque", None, today))


if __name__ == "__main__":
    unittest.main()
