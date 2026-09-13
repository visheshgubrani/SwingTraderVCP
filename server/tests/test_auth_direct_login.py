"""One-tap Fyers login link (Telegram) and the dual-mode OAuth callback.

The direct flow exists so a phone with no app session can complete the daily
2FA. These tests pin the security properties: the state decides the enforcement
path, the state is consume-once, ownership is verified before persisting, and
the dashboard flow still requires its session + CSRF.
"""

import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.services.auth_readiness import VerifyResult
from app.services.session_service import (
    create_direct_login_nonce,
    create_oauth_state,
    create_user_session,
)
from main import app


class InMemoryRedis:
    """Fake Redis with the subset the auth endpoints use."""

    def __init__(self):
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return None
        self.data[key] = str(value)
        if ex:
            self.ttls[key] = ex
        return True

    async def delete(self, *keys):
        count = 0
        for key in keys:
            if key in self.data:
                del self.data[key]
                count += 1
        return count

    async def incr(self, key):
        self.data[key] = str(int(self.data.get(key, 0)) + 1)
        return int(self.data[key])

    async def expire(self, key, seconds):
        self.ttls[key] = seconds
        return True

    async def ttl(self, key):
        return self.ttls.get(key, -2)


class DirectLoginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.redis = InMemoryRedis()
        app.state.redis = self.redis
        self.db = AsyncMock()

        async def override_get_db():
            yield self.db

        app.dependency_overrides[get_db] = override_get_db
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app, base_url="http://localhost:8000")

        self.settings_patches = [
            patch.object(settings, "fyers_app_id", "XV12345-100"),
            patch.object(settings, "fyers_secret_key", "SECRET"),
            patch.object(settings, "fyers_user_id", "XV12345"),
            patch.object(settings, "fyers_redirect_uri", "https://app.edurel.xyz/callback"),
            patch.object(settings, "auth_magic_link_max_uses", 3),
        ]
        for patcher in self.settings_patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def _nonce(self, **kwargs):
        return await create_direct_login_nonce(self.redis, **kwargs)


class DirectLoginStartTests(DirectLoginTests):
    async def test_valid_nonce_redirects_to_fyers_with_a_direct_state(self):
        nonce = await self._nonce()
        response = self.client.get(
            f"/api/v1/auth/direct-login?t={nonce}", follow_redirects=False
        )
        self.assertEqual(response.status_code, 302)
        location = response.headers["location"]
        self.assertIn("generate-authcode", location)
        self.assertIn("state=", location)

        state_key = [k for k in self.redis.data if k.startswith("auth:oauth_state:")]
        self.assertEqual(len(state_key), 1)
        import json

        payload = json.loads(self.redis.data[state_key[0]])
        self.assertEqual(payload["kind"], "direct")
        self.assertEqual(payload["nonce"], nonce)
        self.assertNotIn("session_id", payload)

    async def test_unknown_nonce_is_rejected(self):
        response = self.client.get("/api/v1/auth/direct-login?t=nope", follow_redirects=False)
        self.assertEqual(response.status_code, 400)
        self.assertIn("invalid or has expired", response.json()["detail"])

    async def test_nonce_use_limit_is_enforced(self):
        nonce = await self._nonce()
        for _ in range(3):
            response = self.client.get(
                f"/api/v1/auth/direct-login?t={nonce}", follow_redirects=False
            )
            self.assertEqual(response.status_code, 302)
        exhausted = self.client.get(
            f"/api/v1/auth/direct-login?t={nonce}", follow_redirects=False
        )
        self.assertEqual(exhausted.status_code, 400)

    async def test_per_ip_rate_limit(self):
        with patch("app.routers.auth._DIRECT_LOGIN_IP_MAX_PER_WINDOW", 2):
            nonces = [await self._nonce() for _ in range(3)]
            codes = [
                self.client.get(
                    f"/api/v1/auth/direct-login?t={nonce}", follow_redirects=False
                ).status_code
                for nonce in nonces
            ]
        self.assertEqual(codes, [302, 302, 429])


class DirectCallbackTests(DirectLoginTests):
    async def test_direct_callback_completes_without_an_app_session(self):
        state = await create_oauth_state(self.redis, session_id="", kind="direct", nonce="n1")
        exchange = AsyncMock(
            return_value={"ok": True, "access_token": "AT", "refresh_token": None, "expires_in": 86400}
        )
        persist = AsyncMock()
        mark = AsyncMock()
        notify = AsyncMock(return_value=True)
        with (
            patch("app.routers.auth.exchange_authorization_code", new=exchange),
            patch(
                "app.routers.auth.verify_fyers_session",
                new=AsyncMock(return_value=VerifyResult(ok=True, identity="XV12345")),
            ),
            patch("app.routers.auth.persist_and_cache_fyers_token", new=persist),
            patch("app.services.token_refresh.mark_session_authenticated", new=mark),
            patch("app.services.telegram_service.send_auth_success", new=notify),
        ):
            response = self.client.post(
                "/api/v1/auth/callback", json={"code": "CODE", "state": state}
            )

        self.assertEqual(response.status_code, 200)
        exchange.assert_awaited_once_with("CODE")
        persist.assert_awaited_once()
        mark.assert_awaited_once()
        self.assertEqual(mark.await_args.kwargs["method"], "direct_link")
        notify.assert_awaited_once()
        self.assertNotIn(f"auth:oauth_state:{state}", self.redis.data)

    async def test_direct_state_cannot_be_replayed(self):
        state = await create_oauth_state(self.redis, session_id="", kind="direct", nonce="n1")
        with (
            patch(
                "app.routers.auth.exchange_authorization_code",
                new=AsyncMock(return_value={"ok": True, "access_token": "AT", "expires_in": 10}),
            ),
            patch(
                "app.routers.auth.verify_fyers_session",
                new=AsyncMock(return_value=VerifyResult(ok=True, identity="XV12345")),
            ),
            patch("app.routers.auth.persist_and_cache_fyers_token", new=AsyncMock()),
            patch("app.services.token_refresh.mark_session_authenticated", new=AsyncMock()),
            patch("app.services.telegram_service.send_auth_success", new=AsyncMock(return_value=True)),
        ):
            first = self.client.post("/api/v1/auth/callback", json={"code": "C", "state": state})
            second = self.client.post("/api/v1/auth/callback", json={"code": "C", "state": state})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)

    async def test_foreign_account_is_rejected_and_not_persisted(self):
        state = await create_oauth_state(self.redis, session_id="", kind="direct", nonce="n1")
        persist = AsyncMock()
        with (
            patch(
                "app.routers.auth.exchange_authorization_code",
                new=AsyncMock(return_value={"ok": True, "access_token": "AT", "expires_in": 10}),
            ),
            patch(
                "app.routers.auth.verify_fyers_session",
                new=AsyncMock(return_value=VerifyResult(ok=True, identity="ZZ99999")),
            ),
            patch("app.routers.auth.persist_and_cache_fyers_token", new=persist),
            patch("app.services.telegram_service.send_auth_success", new=AsyncMock(return_value=True)),
        ):
            response = self.client.post(
                "/api/v1/auth/callback", json={"code": "C", "state": state}
            )
        self.assertEqual(response.status_code, 403)
        persist.assert_not_awaited()

    async def test_direct_state_without_nonce_is_rejected(self):
        state = await create_oauth_state(self.redis, session_id="", kind="direct", nonce="")
        response = self.client.post("/api/v1/auth/callback", json={"code": "C", "state": state})
        self.assertEqual(response.status_code, 400)


class SessionCallbackTests(DirectLoginTests):
    async def test_session_state_still_requires_a_session(self):
        session = await create_user_session(self.redis)
        state = await create_oauth_state(self.redis, session_id=session["session_id"])
        response = self.client.post(
            "/api/v1/auth/callback", json={"code": "C", "state": state}
        )
        self.assertEqual(response.status_code, 401)
        # Unconsumed so the dashboard can retry after logging in.
        self.assertIn(f"auth:oauth_state:{state}", self.redis.data)

    async def test_session_state_requires_csrf(self):
        session = await create_user_session(self.redis)
        state = await create_oauth_state(self.redis, session_id=session["session_id"])
        self.client.cookies.set(settings.session_cookie_name, session["session_id"])
        response = self.client.post(
            "/api/v1/auth/callback", json={"code": "C", "state": state}
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("CSRF", response.json()["detail"])

    async def test_session_state_with_session_and_csrf_completes(self):
        session = await create_user_session(self.redis)
        state = await create_oauth_state(self.redis, session_id=session["session_id"])
        self.client.cookies.set(settings.session_cookie_name, session["session_id"])
        persist = AsyncMock()
        with (
            patch(
                "app.routers.auth.exchange_authorization_code",
                new=AsyncMock(
                    return_value={"ok": True, "access_token": "AT", "expires_in": 86400}
                ),
            ),
            patch(
                "app.routers.auth.verify_fyers_session",
                new=AsyncMock(return_value=VerifyResult(ok=True, identity="XV12345")),
            ),
            patch("app.routers.auth.persist_and_cache_fyers_token", new=persist),
            patch("app.services.token_refresh.mark_session_authenticated", new=AsyncMock()),
            patch("app.services.telegram_service.send_auth_success", new=AsyncMock(return_value=True)),
        ):
            response = self.client.post(
                "/api/v1/auth/callback",
                json={"code": "C", "state": state},
                headers={"X-CSRF-Token": session["csrf_token"]},
            )
        self.assertEqual(response.status_code, 200)
        persist.assert_awaited_once()

    async def test_session_state_from_another_session_is_rejected(self):
        owner = await create_user_session(self.redis)
        attacker = await create_user_session(self.redis)
        state = await create_oauth_state(self.redis, session_id=owner["session_id"])
        self.client.cookies.set(settings.session_cookie_name, attacker["session_id"])
        response = self.client.post(
            "/api/v1/auth/callback",
            json={"code": "C", "state": state},
            headers={"X-CSRF-Token": attacker["csrf_token"]},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
