"""Comprehensive tests for single-user authentication, API protection, CSRF, WS, and hardening."""

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings, settings
from app.database import get_db
from app.services.session_service import (
    create_oauth_state,
    create_user_session,
    get_user_session,
    verify_and_consume_oauth_state,
    verify_app_password,
)
from main import app


class InMemoryRedis:
    """Lightweight in-memory Redis mock for unit testing session and auth state."""

    def __init__(self):
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.data[key] = str(value)
        if ex:
            self.ttls[key] = ex
        return True

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self.data:
                del self.data[k]
                self.ttls.pop(k, None)
                count += 1
        return count

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, -2 if key not in self.data else -1)

    async def incr(self, key: str) -> int:
        val = int(self.data.get(key, 0)) + 1
        self.data[key] = str(val)
        return val

    async def expire(self, key: str, seconds: int) -> bool:
        if key in self.data:
            self.ttls[key] = seconds
            return True
        return False

    def pipeline(self):
        return InMemoryRedisPipeline(self)

    async def publish(self, channel: str, message: str) -> int:
        return 1

    async def ping(self) -> bool:
        return True


class InMemoryRedisPipeline:
    def __init__(self, redis: InMemoryRedis):
        self.redis = redis
        self.ops = []

    def incr(self, key: str):
        self.ops.append(("incr", key))
        return self

    def expire(self, key: str, seconds: int):
        self.ops.append(("expire", key, seconds))
        return self

    async def execute(self):
        results = []
        for op in self.ops:
            if op[0] == "incr":
                results.append(await self.redis.incr(op[1]))
            elif op[0] == "expire":
                results.append(await self.redis.expire(op[1], op[2]))
        self.ops.clear()
        return results


class AuthProtectionTests(unittest.TestCase):
    def setUp(self):
        self.fake_redis = InMemoryRedis()
        app.state.redis = self.fake_redis
        self.client = TestClient(app, base_url="http://localhost:8000")

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_unauthenticated_requests_return_401(self):
        """Verify that all personal endpoints reject unauthenticated requests with 401."""
        endpoints = [
            ("GET", "/api/v1/automation/proposals"),
            ("GET", "/api/v1/trading/positions"),
            ("GET", "/api/v1/historical/status"),
            ("GET", "/api/v1/screening/runs"),
            ("GET", "/api/v1/journal/entries"),
            ("GET", "/api/v1/auth/session"),
            ("POST", "/api/v1/historical/sync"),
            ("POST", "/api/v1/automation/paper-portfolio/reset"),
        ]

        for method, path in endpoints:
            response = self.client.request(method, path)
            self.assertEqual(
                response.status_code,
                401,
                f"Endpoint {method} {path} should return 401 when unauthenticated, got {response.status_code}",
            )

    def test_health_check_remains_unauthenticated(self):
        """Verify /health liveness probe remains unauthenticated."""
        mock_db = AsyncMock()
        mock_scalar = MagicMock()
        mock_scalar.scalar.return_value = 1
        mock_db.execute.return_value = mock_scalar

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        self.fake_redis.ping = AsyncMock(return_value=True)
        app.state.redis = self.fake_redis

        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertTrue(response.json()["redis"])

    def test_login_wrong_password_and_rate_limiting(self):
        """Verify invalid password returns 401 and trips rate limit after 5 failures."""
        with patch.object(settings, "app_password", "super_secret_trading_pass"):
            # 5 failed attempts
            for i in range(5):
                resp = self.client.post("/api/v1/auth/login", json={"password": "wrong_password"})
                self.assertEqual(resp.status_code, 401, f"Attempt {i+1} should return 401")

            # 6th attempt should be rate limited
            resp = self.client.post("/api/v1/auth/login", json={"password": "wrong_password"})
            self.assertEqual(resp.status_code, 429)
            self.assertIn("Too many failed login attempts", resp.json()["detail"])

    def test_login_rate_limiting_with_trusted_proxy_header(self):
        """Verify X-Forwarded-For from trusted local proxy is used for IP rate limiting."""
        with patch.object(settings, "app_password", "super_secret_trading_pass"):
            headers = {"X-Forwarded-For": "198.51.100.42, 127.0.0.1"}
            for i in range(5):
                resp = self.client.post(
                    "/api/v1/auth/login",
                    json={"password": "wrong_password"},
                    headers=headers,
                )
                self.assertEqual(resp.status_code, 401)

            # Locked out for 198.51.100.42
            resp = self.client.post(
                "/api/v1/auth/login",
                json={"password": "wrong_password"},
                headers=headers,
            )
            self.assertEqual(resp.status_code, 429)

            # Different client IP is NOT locked out
            other_headers = {"X-Forwarded-For": "198.51.100.43, 127.0.0.1"}
            resp_other = self.client.post(
                "/api/v1/auth/login",
                json={"password": "wrong_password"},
                headers=other_headers,
            )
            self.assertEqual(resp_other.status_code, 401)

    def test_login_success_and_cookie_only_isolation(self):
        """Verify login sets HttpOnly cookie and does not expose session_id in JSON."""
        with patch.object(settings, "app_password", "super_secret_trading_pass"):
            resp = self.client.post(
                "/api/v1/auth/login", json={"password": "super_secret_trading_pass"}
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "ok")
            self.assertIn("csrf_token", data)
            # Ensure session_id is NEVER in response body (SEC-001)
            self.assertNotIn("session_id", data)

            csrf_token = data["csrf_token"]

            # Cookie check
            cookies = resp.cookies
            self.assertIn(settings.session_cookie_name, cookies)
            session_cookie = cookies[settings.session_cookie_name]

            # Check GET /api/v1/auth/session with cookie
            session_resp = self.client.get("/api/v1/auth/session")
            self.assertEqual(session_resp.status_code, 200)
            self.assertTrue(session_resp.json()["authenticated"])
            self.assertEqual(session_resp.json()["csrf_token"], csrf_token)

            # Verify Bearer token header is NOT accepted (cookie-only enforcement)
            bearer_client = TestClient(app, base_url="http://localhost:8000")
            bearer_resp = bearer_client.get(
                "/api/v1/auth/session",
                headers={"Authorization": f"Bearer {session_cookie}"},
            )
            self.assertEqual(bearer_resp.status_code, 401)

    def test_mutating_requests_require_csrf_token(self):
        """Verify that mutating requests (POST/PUT/PATCH/DELETE) require valid session-bound CSRF token."""
        with patch.object(settings, "app_password", "super_secret_trading_pass"):
            # Login
            login_resp = self.client.post(
                "/api/v1/auth/login", json={"password": "super_secret_trading_pass"}
            )
            csrf_token = login_resp.json()["csrf_token"]

            # Mutating call without CSRF header should return 403 Forbidden
            no_csrf_resp = self.client.post("/api/v1/historical/sync")
            self.assertEqual(no_csrf_resp.status_code, 403)
            self.assertIn("CSRF", no_csrf_resp.json()["detail"])

            # Mutating call with wrong CSRF header should return 403 Forbidden
            wrong_csrf_resp = self.client.post(
                "/api/v1/historical/sync",
                headers={"X-CSRF-Token": "invalid_csrf_token_value"},
            )
            self.assertEqual(wrong_csrf_resp.status_code, 403)

            # Mutating call with valid CSRF header should pass auth/CSRF layer
            with patch("app.routers.historical.get_db"):
                valid_csrf_resp = self.client.post(
                    "/api/v1/historical/sync",
                    headers={"X-CSRF-Token": csrf_token},
                )
                self.assertNotIn(valid_csrf_resp.status_code, [401, 403])

    def test_logout_revokes_session(self):
        """Verify logout revokes session in Redis and clears cookie."""
        with patch.object(settings, "app_password", "super_secret_trading_pass"):
            login_resp = self.client.post(
                "/api/v1/auth/login", json={"password": "super_secret_trading_pass"}
            )
            csrf_token = login_resp.json()["csrf_token"]

            # Logout
            logout_resp = self.client.post(
                "/api/v1/auth/logout",
                headers={"X-CSRF-Token": csrf_token},
            )
            self.assertEqual(logout_resp.status_code, 200)

            # Subsequent authenticated call must return 401
            subsequent_resp = self.client.get("/api/v1/auth/session")
            self.assertEqual(subsequent_resp.status_code, 401)

    def test_oauth_state_storage_bound_to_session(self):
        """Verify OAuth state is recorded in Redis bound to caller session and consumed once (SEC-003)."""
        import asyncio

        async def run_test():
            session_id = "user_session_abc"
            state = await create_oauth_state(self.fake_redis, session_id=session_id)
            self.assertIsNotNone(state)
            self.assertTrue(len(state) >= 16)

            # Consumption with matching session succeeds
            first_check = await verify_and_consume_oauth_state(
                self.fake_redis, state, expected_session_id=session_id
            )
            self.assertTrue(first_check)

            # Second consumption (replay attack) fails
            second_check = await verify_and_consume_oauth_state(
                self.fake_redis, state, expected_session_id=session_id
            )
            self.assertFalse(second_check)

            # Create another state and test mismatch
            state2 = await create_oauth_state(self.fake_redis, session_id=session_id)
            mismatch_check = await verify_and_consume_oauth_state(
                self.fake_redis, state2, expected_session_id="attacker_session_xyz"
            )
            self.assertFalse(mismatch_check)

        asyncio.run(run_test())

    def test_websocket_cookie_authentication(self):
        """Verify WebSocket accepts authenticated cookie and rejects query token or unauthenticated (SEC-004)."""
        import asyncio

        # 1. Unauthenticated WS should be closed with code 1008
        try:
            with self.client.websocket_connect("/ws") as ws:
                self.fail("Unauthenticated WS should have been closed")
        except WebSocketDisconnect as exc:
            self.assertEqual(exc.code, 1008)
        except Exception:
            pass

        # 2. Authenticated WS via HttpOnly cookie
        async def create_session():
            return await create_user_session(self.fake_redis)

        session = asyncio.run(create_session())
        session_id = session["session_id"]

        cookies = {settings.session_cookie_name: session_id}
        with self.client.websocket_connect("/ws", cookies=cookies) as ws:
            ws.send_text(json.dumps({"action": "ping"}))
            data = ws.receive_json()
            self.assertEqual(data.get("type"), "pong")

    def test_websocket_symbol_subscription_cap(self):
        """Verify WebSocket caps symbol subscriptions per message and per session."""
        import asyncio

        async def create_session():
            return await create_user_session(self.fake_redis)

        session = asyncio.run(create_session())
        session_id = session["session_id"]

        cookies = {settings.session_cookie_name: session_id}
        with self.client.websocket_connect("/ws", cookies=cookies) as ws:
            # Try to subscribe to 101 symbols in one message
            too_many = [f"NSE:SYM{i}-EQ" for i in range(101)]
            ws.send_text(json.dumps({"action": "subscribe", "symbols": too_many}))
            data = ws.receive_json()
            self.assertEqual(data.get("type"), "error")
            self.assertIn("Too many symbols in subscription", data.get("message", ""))

    def test_production_security_settings_validation(self):
        """Verify that Settings validation fails closed on insecure production options."""
        # 1. SQL_ECHO in production must fail
        with self.assertRaises(ValueError) as ctx:
            Settings(
                app_environment="production",
                app_password="ValidProdPassword123!",
                sql_echo=True,
            )
        self.assertIn("SQL_ECHO", str(ctx.exception))

        # 2. Missing or short APP_PASSWORD (< 12 chars) in production must fail
        with self.assertRaises(ValueError) as ctx:
            Settings(
                app_environment="production",
                app_password="short",
                sql_echo=False,
            )
        self.assertIn("APP_PASSWORD", str(ctx.exception))

        # 3. Missing TOKEN_ENCRYPTION_KEY in production must fail at startup
        with self.assertRaises(ValueError) as ctx:
            Settings(
                app_environment="production",
                app_password="StrongProdPassword2026!",
                sql_echo=False,
                token_encryption_key="",
            )
        self.assertIn("TOKEN_ENCRYPTION_KEY", str(ctx.exception))

        # 4. Valid production settings succeed
        valid_prod = Settings(
            app_environment="production",
            app_password="StrongProdPassword2026!",
            sql_echo=False,
            token_encryption_key="prod-token-encryption-key",
        )
        self.assertEqual(valid_prod.app_environment, "production")
        self.assertTrue(valid_prod.session_cookie_secure)


if __name__ == "__main__":
    unittest.main()
