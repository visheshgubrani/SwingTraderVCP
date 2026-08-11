import base64
import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.config import settings
from app.routers.saas_scans import (
    _lock_variant_quota,
    _require_access,
    _variant_runs_today,
)

TEST_SECRET = "test-service-secret-at-least-32-bytes"


def signed_access_token(
    *,
    secret: str,
    subject: str | None = "user-123",
    features: list[str] | None = None,
    issued_at: int | None = None,
    expires_at: int | None = None,
) -> str:
    now = int(time.time())
    payload = {
        "v": 1,
        "iss": "swyingify-next",
        "aud": "swyingify-fastapi",
        "sub": subject,
        "features": features or ["scanner.strict"],
        "iat": issued_at if issued_at is not None else now,
        "exp": expires_at if expires_at is not None else now + 60,
    }
    payload_segment = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), payload_segment.encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    return f"{payload_segment}.{signature}"


class SaasInternalAccessTests(unittest.TestCase):
    def test_valid_signed_claims_supply_subject_and_feature(self) -> None:
        token = signed_access_token(
            secret=TEST_SECRET,
            features=["scanner.custom"],
        )
        with (
            patch.object(settings, "app_environment", "production"),
            patch.object(settings, "saas_internal_api_key", TEST_SECRET),
        ):
            subject = _require_access(
                token,
                any_feature={"scanner.custom"},
                require_subject=True,
            )

        self.assertEqual(subject, "user-123")

    def test_plain_shared_key_and_forged_signature_are_rejected(self) -> None:
        forged = signed_access_token(
            secret="wrong-service-secret-at-least-32-bytes",
            features=["scanner.strict"],
        )
        with (
            patch.object(settings, "app_environment", "production"),
            patch.object(settings, "saas_internal_api_key", TEST_SECRET),
        ):
            for token in ("test-service-secret", forged):
                with self.subTest(token=token[:12]):
                    with self.assertRaises(HTTPException) as raised:
                        _require_access(token, any_feature={"scanner.strict"})
                    self.assertEqual(raised.exception.status_code, 401)

    def test_missing_feature_and_unsigned_subject_are_rejected(self) -> None:
        no_feature = signed_access_token(
            secret=TEST_SECRET,
            features=["scanner.history.recent"],
        )
        no_subject = signed_access_token(
            secret=TEST_SECRET,
            subject=None,
            features=["scanner.custom"],
        )
        with (
            patch.object(settings, "app_environment", "production"),
            patch.object(settings, "saas_internal_api_key", TEST_SECRET),
        ):
            with self.assertRaises(HTTPException) as missing_feature:
                _require_access(no_feature, any_feature={"scanner.strict"})
            with self.assertRaises(HTTPException) as missing_subject:
                _require_access(
                    no_subject,
                    any_feature={"scanner.custom"},
                    require_subject=True,
                )

        self.assertEqual(missing_feature.exception.status_code, 403)
        self.assertEqual(missing_subject.exception.status_code, 401)

    def test_expired_claim_and_production_bypass_are_rejected(self) -> None:
        now = int(time.time())
        expired = signed_access_token(
            secret=TEST_SECRET,
            issued_at=now - 90,
            expires_at=now - 30,
        )
        with (
            patch.object(settings, "app_environment", "production"),
            patch.object(settings, "saas_internal_api_key", TEST_SECRET),
        ):
            for token in (expired, "development-bypass"):
                with self.assertRaises(HTTPException) as raised:
                    _require_access(token, any_feature={"scanner.strict"})
                self.assertEqual(raised.exception.status_code, 401)

    def test_development_bypass_is_explicitly_non_production(self) -> None:
        with patch.object(settings, "app_environment", "development"):
            subject = _require_access(
                "development-bypass",
                any_feature={"scanner.custom"},
                require_subject=True,
            )
        self.assertEqual(subject, "development-bypass-user")


class VariantQuotaTests(unittest.IsolatedAsyncioTestCase):
    async def test_quota_reservation_uses_transaction_advisory_lock(self) -> None:
        db = AsyncMock()

        await _lock_variant_quota(db, "user-123")

        statement = str(db.execute.await_args.args[0])
        params = db.execute.await_args.args[1]
        self.assertIn("pg_advisory_xact_lock", statement)
        self.assertIn("swyingify-variant:user-123:", params["quota_key"])

    async def test_failed_runs_do_not_consume_quota(self) -> None:
        result = MagicMock()
        result.scalar_one.return_value = 3
        db = AsyncMock()
        db.execute.return_value = result

        used = await _variant_runs_today(db, "user-123")

        statement = str(db.execute.await_args.args[0])
        self.assertEqual(used, 3)
        self.assertIn("status IN ('queued', 'running', 'succeeded')", statement)


if __name__ == "__main__":
    unittest.main()
