"""Scheduled daily auth guard and job_runs audit logging (AUTH-001).

The job was named ``token_refresh`` before SEBI's daily-2FA framework removed
continuous refresh-token sessions; it is now the guard that keeps a live
session (headless TOTP) or alerts the owner (Telegram one-tap link).
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.token_refresh import run_auth_guard


class TokenRefreshTests(unittest.IsolatedAsyncioTestCase):
    def _session_context(self, db):
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = db
        mock_session_ctx.__aexit__.return_value = None
        return mock_session_ctx

    async def test_guard_inserts_triggered_by_and_succeeds(self):
        """The guard records job_runs with triggered_by and closes it as succeeded."""
        mock_db = AsyncMock()
        mock_scalar = MagicMock()
        generated_run_id = uuid4()
        mock_scalar.scalar.return_value = generated_run_id
        mock_db.execute.return_value = mock_scalar

        fake_redis = AsyncMock()
        ctx = {"redis": fake_redis, "job_id": "cron_123", "triggered_by": "scheduler"}

        with (
            patch(
                "app.services.token_refresh.async_session",
                return_value=self._session_context(mock_db),
            ),
            patch(
                "app.services.token_refresh._guard_tick",
                new=AsyncMock(
                    return_value={"status": "refreshed", "method": "headless_totp"}
                ),
            ),
        ):
            result = await run_auth_guard(ctx)

        self.assertEqual(result["status"], "refreshed")

        # First query: INSERT into job_runs with triggered_by (AUTH-001)
        insert_call = mock_db.execute.call_args_list[0]
        query_sql = str(insert_call[0][0])
        params = insert_call[0][1]
        self.assertIn("triggered_by", query_sql)
        self.assertEqual(params["triggered_by"], "scheduler")
        self.assertEqual(params["job_type"], "auth_guard")
        self.assertEqual(params["job_key"], "auth_guard_cron_123")

        # Second query: UPDATE job_runs status = 'succeeded'
        update_call = mock_db.execute.call_args_list[1]
        self.assertIn("UPDATE job_runs", str(update_call[0][0]))
        self.assertEqual(update_call[0][1]["status"], "succeeded")
        self.assertEqual(update_call[0][1]["run_id"], generated_run_id)

    async def test_guard_records_unauthenticated_tick_as_succeeded(self):
        """A healthy job that could not restore the session is still a successful run."""
        mock_db = AsyncMock()
        mock_scalar = MagicMock()
        mock_scalar.scalar.return_value = uuid4()
        mock_db.execute.return_value = mock_scalar

        ctx = {"redis": AsyncMock(), "job_id": "cron_456", "triggered_by": "scheduler"}
        with (
            patch(
                "app.services.token_refresh.async_session",
                return_value=self._session_context(mock_db),
            ),
            patch(
                "app.services.token_refresh._guard_tick",
                new=AsyncMock(
                    return_value={"status": "unauthenticated", "reason": "expired"}
                ),
            ),
        ):
            result = await run_auth_guard(ctx)

        self.assertEqual(result["status"], "unauthenticated")
        update_call = mock_db.execute.call_args_list[1]
        self.assertEqual(update_call[0][1]["status"], "succeeded")

    async def test_guard_handles_crash_gracefully(self):
        """A crashing tick records the error, emits a critical event, and never raises."""
        mock_db = AsyncMock()
        mock_scalar = MagicMock()
        generated_run_id = uuid4()
        mock_scalar.scalar.return_value = generated_run_id
        mock_db.execute.return_value = mock_scalar

        ctx = {"redis": AsyncMock()}
        with (
            patch(
                "app.services.token_refresh.async_session",
                return_value=self._session_context(mock_db),
            ),
            patch(
                "app.services.token_refresh._guard_tick",
                new=AsyncMock(side_effect=RuntimeError("Redis connection lost")),
            ),
            patch(
                "app.services.token_refresh._emit_system_event", new_callable=AsyncMock
            ) as mock_emit,
        ):
            result = await run_auth_guard(ctx)

        self.assertEqual(result["status"], "crashed")
        self.assertIn("Redis connection lost", result["error"])
        mock_emit.assert_called_once()
        self.assertEqual(mock_emit.await_args.args[2], "auth_guard_crashed")

        update_call = mock_db.execute.call_args_list[1]
        self.assertEqual(update_call[0][1]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
