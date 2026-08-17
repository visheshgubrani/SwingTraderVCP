"""Unit and database tests for scheduled token refresh and job_runs audit logging (AUTH-001)."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.token_refresh import run_token_refresh


class TokenRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_token_refresh_inserts_triggered_by_and_succeeds(self):
        """Verify run_token_refresh inserts job_runs with triggered_by and marks succeeded (AUTH-001)."""
        mock_db = AsyncMock()
        mock_scalar = MagicMock()
        generated_run_id = uuid4()
        mock_scalar.scalar.return_value = generated_run_id
        mock_db.execute.return_value = mock_scalar

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_db
        mock_session_ctx.__aexit__.return_value = None

        fake_redis = AsyncMock()
        ctx = {"redis": fake_redis, "job_id": "cron_123", "triggered_by": "scheduler"}

        with patch("app.services.token_refresh.async_session", return_value=mock_session_ctx):
            with patch("app.services.token_refresh.refresh_and_save", return_value="new_valid_fyers_token"):
                result = await run_token_refresh(ctx)

        self.assertEqual(result["status"], "refreshed")
        self.assertEqual(result["run_id"], str(generated_run_id))

        # Check DB calls
        self.assertEqual(mock_db.execute.call_count, 2)

        # First query: INSERT into job_runs with triggered_by
        insert_call = mock_db.execute.call_args_list[0]
        query_sql = str(insert_call[0][0])
        params = insert_call[0][1]
        self.assertIn("triggered_by", query_sql)
        self.assertEqual(params["triggered_by"], "scheduler")
        self.assertEqual(params["job_key"], "token_refresh_cron_123")

        # Second query: UPDATE job_runs status = 'succeeded'
        update_call = mock_db.execute.call_args_list[1]
        update_sql = str(update_call[0][0])
        update_params = update_call[0][1]
        self.assertIn("succeeded", update_sql)
        self.assertEqual(update_params["run_id"], generated_run_id)

    async def test_run_token_refresh_handles_refresh_rejection(self):
        """Verify run_token_refresh marks job_runs as failed if refresh fails."""
        mock_db = AsyncMock()
        mock_scalar = MagicMock()
        generated_run_id = uuid4()
        mock_scalar.scalar.return_value = generated_run_id
        mock_db.execute.return_value = mock_scalar

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_db
        mock_session_ctx.__aexit__.return_value = None

        fake_redis = AsyncMock()
        ctx = {"redis": fake_redis, "job_id": "manual_456", "triggered_by": "manual"}

        with patch("app.services.token_refresh.async_session", return_value=mock_session_ctx):
            with patch("app.services.token_refresh.refresh_and_save", return_value=None):
                result = await run_token_refresh(ctx)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["run_id"], str(generated_run_id))

        # Second query should mark status as failed
        update_call = mock_db.execute.call_args_list[1]
        update_sql = str(update_call[0][0])
        self.assertIn("failed", update_sql)

    async def test_run_token_refresh_handles_crash_gracefully(self):
        """Verify run_token_refresh records error in job_runs and emits critical system_event if exception is raised."""
        mock_db = AsyncMock()
        mock_scalar = MagicMock()
        generated_run_id = uuid4()
        mock_scalar.scalar.return_value = generated_run_id
        mock_db.execute.return_value = mock_scalar

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_db
        mock_session_ctx.__aexit__.return_value = None

        fake_redis = AsyncMock()
        ctx = {"redis": fake_redis}

        with patch("app.services.token_refresh.async_session", return_value=mock_session_ctx):
            with patch("app.services.token_refresh.refresh_and_save", side_effect=RuntimeError("Redis connection lost")):
                with patch("app.services.token_refresh._emit_system_event", new_callable=AsyncMock) as mock_emit:
                    result = await run_token_refresh(ctx)

        self.assertEqual(result["status"], "crashed")
        self.assertIn("Redis connection lost", result["error"])
        mock_emit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
