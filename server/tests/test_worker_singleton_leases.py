import asyncio
import unittest
from unittest.mock import ANY, AsyncMock, patch
from uuid import uuid4

from app.workers.entry_supervisor import (
    _heartbeat as supervisor_heartbeat,
    run_entry_supervisor,
)
from app.workers.position_monitor import (
    PositionMonitorRuntime,
    _heartbeat_loop as position_monitor_heartbeat,
    run_position_monitor,
)
from app.workers.proposal_worker import (
    _LOCK_KEY as PROPOSAL_LOCK_KEY,
    worker_on_shutdown,
    worker_on_startup,
)
from app.workers.tick_worker import (
    TickWorkerState,
    _heartbeat_loop as tick_worker_heartbeat,
    run_tick_worker,
)
from app.services.reconciliation import run_reconciliation


class WorkerSingletonLeaseTests(unittest.IsolatedAsyncioTestCase):
    # --- Tick Worker ---
    @patch("app.workers.tick_worker.create_async_redis", new_callable=AsyncMock)
    @patch("app.workers.tick_worker.acquire_distributed_lease", new_callable=AsyncMock)
    async def test_tick_worker_singleton_collision_exits_cleanly(
        self,
        mock_acquire: AsyncMock,
        mock_redis_from_url: AsyncMock,
    ) -> None:
        mock_redis = AsyncMock()
        mock_redis_from_url.return_value = mock_redis
        mock_acquire.return_value = False

        await run_tick_worker()

        mock_acquire.assert_awaited_once()
        mock_redis.aclose.assert_awaited_once()

    @patch("app.workers.tick_worker.renew_distributed_lease", new_callable=AsyncMock)
    @patch("app.workers.tick_worker._set_worker_status", new_callable=AsyncMock)
    async def test_tick_worker_heartbeat_lease_loss_triggers_shutdown(
        self,
        mock_set_status: AsyncMock,
        mock_renew: AsyncMock,
    ) -> None:
        mock_renew.return_value = False
        redis = AsyncMock()
        state = TickWorkerState(worker_id=str(uuid4()))

        from app.workers.tick_worker import _shutdown

        _shutdown.clear()
        self.addCleanup(_shutdown.clear)
        self.assertFalse(_shutdown.is_set())

        await tick_worker_heartbeat(redis, state)

        self.assertTrue(_shutdown.is_set())
        mock_renew.assert_awaited_once()

    async def test_tick_worker_import_failure_emits_critical_event(self) -> None:
        redis = AsyncMock()
        session_cm = AsyncMock()
        session_cm.__aenter__.return_value = AsyncMock()

        from app.workers.tick_worker import _shutdown

        _shutdown.clear()
        self.addCleanup(_shutdown.clear)
        with (
            patch(
                "app.workers.tick_worker.create_async_redis",
                new=AsyncMock(return_value=redis),
            ),
            patch(
                "app.workers.tick_worker.acquire_distributed_lease",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.workers.tick_worker.release_distributed_lease",
                new=AsyncMock(),
            ),
            patch(
                "app.workers.tick_worker._heartbeat_loop",
                new=AsyncMock(),
            ),
            patch(
                "app.workers.tick_worker.get_valid_access_token",
                new=AsyncMock(return_value="token"),
            ),
            patch(
                "app.workers.tick_worker.async_session",
                return_value=session_cm,
            ),
            patch(
                "app.workers.tick_worker._load_subscription_symbols",
                new=AsyncMock(return_value=["NSE:NIFTY50-INDEX"]),
            ),
            patch(
                "app.workers.tick_worker._load_fyers_data_socket_class",
                side_effect=ModuleNotFoundError("pkg_resources"),
            ),
            patch(
                "app.workers.tick_worker._set_worker_status",
                new=AsyncMock(),
            ),
            patch(
                "app.workers.tick_worker._emit_system_event",
                new=AsyncMock(),
            ) as emit_event,
        ):
            with self.assertRaises(ModuleNotFoundError):
                await run_tick_worker()

        critical_call = emit_event.await_args_list[0]
        self.assertEqual(critical_call.args[:3], (redis, "critical", "tick_worker_crashed"))
        self.assertEqual(
            critical_call.args[3],
            {"worker_id": ANY, "error_type": "ModuleNotFoundError"},
        )
        self.assertEqual(len(emit_event.await_args_list), 1)

    # --- Position Monitor Worker ---
    @patch("app.workers.position_monitor.create_async_redis", new_callable=AsyncMock)
    @patch("app.workers.position_monitor.acquire_distributed_lease", new_callable=AsyncMock)
    async def test_position_monitor_singleton_collision_exits_cleanly(
        self,
        mock_acquire: AsyncMock,
        mock_redis_from_url: AsyncMock,
    ) -> None:
        mock_redis = AsyncMock()
        mock_redis_from_url.return_value = mock_redis
        mock_acquire.return_value = False

        await run_position_monitor()

        mock_acquire.assert_awaited_once()
        mock_redis.aclose.assert_awaited_once()

    @patch("app.workers.position_monitor.renew_distributed_lease", new_callable=AsyncMock)
    @patch("app.workers.position_monitor._set_status", new_callable=AsyncMock)
    async def test_position_monitor_heartbeat_lease_loss_triggers_shutdown(
        self,
        mock_set_status: AsyncMock,
        mock_renew: AsyncMock,
    ) -> None:
        mock_renew.return_value = False
        redis = AsyncMock()
        runtime = PositionMonitorRuntime()

        from app.workers.position_monitor import _shutdown

        _shutdown.clear()
        self.assertFalse(_shutdown.is_set())

        await position_monitor_heartbeat(redis, runtime, worker_id="worker-pm-1")

        self.assertTrue(_shutdown.is_set())
        mock_renew.assert_awaited_once()

    # --- Entry Supervisor Worker ---
    @patch("app.workers.entry_supervisor.create_async_redis", new_callable=AsyncMock)
    @patch("app.workers.entry_supervisor.acquire_distributed_lease", new_callable=AsyncMock)
    async def test_entry_supervisor_singleton_collision_exits_cleanly(
        self,
        mock_acquire: AsyncMock,
        mock_redis_from_url: AsyncMock,
    ) -> None:
        mock_redis = AsyncMock()
        mock_redis_from_url.return_value = mock_redis
        mock_acquire.return_value = False

        await run_entry_supervisor()

        mock_acquire.assert_awaited_once()
        mock_redis.aclose.assert_awaited_once()

    @patch("app.workers.entry_supervisor.renew_distributed_lease", new_callable=AsyncMock)
    @patch("app.workers.entry_supervisor._set_status", new_callable=AsyncMock)
    async def test_entry_supervisor_heartbeat_lease_loss_triggers_shutdown(
        self,
        mock_set_status: AsyncMock,
        mock_renew: AsyncMock,
    ) -> None:
        mock_renew.return_value = False
        redis = AsyncMock()

        from app.workers.entry_supervisor import _shutdown

        _shutdown.clear()
        self.assertFalse(_shutdown.is_set())

        await supervisor_heartbeat(redis, worker_id="worker-es-1")

        self.assertTrue(_shutdown.is_set())
        mock_renew.assert_awaited_once()

    # --- Reconciliation Run Lease ---
    @patch("app.services.reconciliation.acquire_distributed_lease", new_callable=AsyncMock)
    async def test_reconciliation_lease_collision_skips_run(
        self,
        mock_acquire: AsyncMock,
    ) -> None:
        mock_acquire.return_value = False
        ctx = {"redis": AsyncMock(), "job_id": "job-recon-1"}

        result = await run_reconciliation(ctx, triggered_by="manual")

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "already_running")
        mock_acquire.assert_awaited_once()

    @patch("app.services.reconciliation.acquire_distributed_lease", new_callable=AsyncMock)
    @patch("app.services.reconciliation.release_distributed_lease", new_callable=AsyncMock)
    @patch("app.services.reconciliation.async_session")
    async def test_reconciliation_releases_lease_on_completion_or_failure(
        self,
        mock_session: AsyncMock,
        mock_release: AsyncMock,
        mock_acquire: AsyncMock,
    ) -> None:
        mock_acquire.return_value = True
        mock_release.return_value = True
        mock_session.side_effect = RuntimeError("DB connection failed")
        ctx = {"redis": AsyncMock(), "job_id": "job-recon-2"}

        with self.assertRaises(RuntimeError):
            await run_reconciliation(ctx, triggered_by="scheduler")

        mock_acquire.assert_awaited_once()
        mock_release.assert_awaited_once()

    # --- Proposal Worker ---
    @patch("app.workers.proposal_worker.tune_arq_redis_pool", new_callable=AsyncMock)
    @patch("app.workers.proposal_worker.acquire_distributed_lease", new_callable=AsyncMock)
    async def test_proposal_worker_startup_collision_raises(
        self,
        mock_acquire: AsyncMock,
        mock_tune: AsyncMock,
    ) -> None:
        mock_acquire.return_value = False
        ctx = {"redis": AsyncMock()}

        with self.assertRaisesRegex(RuntimeError, "singleton lease"):
            await worker_on_startup(ctx)

        mock_acquire.assert_awaited_once()
        self.assertNotIn("lease_renew_task", ctx)

    @patch("app.workers.proposal_worker.tune_arq_redis_pool", new_callable=AsyncMock)
    @patch("app.workers.proposal_worker.acquire_distributed_lease", new_callable=AsyncMock)
    @patch("app.workers.proposal_worker.release_distributed_lease", new_callable=AsyncMock)
    async def test_proposal_worker_shutdown_releases_lease(
        self,
        mock_release: AsyncMock,
        mock_acquire: AsyncMock,
        mock_tune: AsyncMock,
    ) -> None:
        mock_acquire.return_value = True
        mock_release.return_value = True
        ctx = {"redis": AsyncMock()}

        await worker_on_startup(ctx)
        self.assertIn("lease_owner", ctx)
        self.assertIn("lease_renew_task", ctx)

        await worker_on_shutdown(ctx)

        mock_release.assert_awaited_once()
        mock_release.assert_awaited_with(
            ctx["redis"],
            PROPOSAL_LOCK_KEY,
            ctx["lease_owner"],
        )


if __name__ == "__main__":
    unittest.main()
