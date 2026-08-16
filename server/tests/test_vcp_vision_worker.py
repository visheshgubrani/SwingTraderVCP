import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.vcp_vision import (
    OpenRouterVisionClient,
    VisionLLMError,
    VisionLLMResult,
    canonical_ohlcv_hash,
    run_vcp_vision_analysis,
    vcp_vision_job_id,
)
from app.services.vcp_vision import settings as vision_settings
from tests.test_vcp_vision_validation import valid_result


def candle_rows(count: int, start: dt.date) -> list[SimpleNamespace]:
    rows = []
    cursor = start
    while len(rows) < count:
        rows.append(
            SimpleNamespace(
                candle_start=dt.datetime(
                    cursor.year, cursor.month, cursor.day, 15, 30,
                    tzinfo=dt.timezone.utc,
                ),
                open_price=100.0,
                high_price=105.0,
                low_price=95.0,
                close_price=102.0,
                volume=100_000,
            )
        )
        cursor += dt.timedelta(days=1)
    return rows


def frozen_candles(count: int = 252, end: dt.date = dt.date(2025, 10, 30)):
    start = end - dt.timedelta(days=count - 1)
    from app.services.vcp_vision import FrozenCandle

    return [
        FrozenCandle(
            date=row.candle_start.date(),
            open=float(row.open_price),
            high=float(row.high_price),
            low=float(row.low_price),
            close=float(row.close_price),
            volume=int(row.volume),
        )
        for row in candle_rows(count, start)
    ]


def in_range_result():
    """valid_result() with all dates inside the frozen window (Feb-Oct 2025)."""
    from app.services.vcp_vision import (
        VcpBaseWindow,
        VcpContractionAnchor,
        VcpPivotZone,
        VcpPriorUptrend,
        VcpVolumeAssessment,
        VcpVisionResultV1,
    )

    return VcpVisionResultV1(
        verdict="valid",
        confidence=80,
        summary="Coherent nested contractions on strong prior uptrend.",
        prior_uptrend=VcpPriorUptrend(
            assessment="clear", note="Higher highs and lows through January."
        ),
        volume=VcpVolumeAssessment(
            assessment="drying_up",
            note="Volume contracts through the base.",
        ),
        bases=[
            VcpBaseWindow(
                start="2025-03-03",
                end="2025-03-07",
                quality="solid",
                notes="Tight base.",
            )
        ],
        contraction_anchors=[
            VcpContractionAnchor(date="2025-03-04", evidence="Swing peak."),
            VcpContractionAnchor(date="2025-03-05", evidence="Nested peak."),
            VcpContractionAnchor(date="2025-03-06", evidence="Final peak."),
        ],
        pivot_zone=VcpPivotZone(
            start="2025-03-06",
            end="2025-03-07",
            rationale="Pivot above the final contraction.",
        ),
        supporting_evidence=["Volume dried up on the last pullback."],
        contrary_evidence=["News-driven gap."],
        human_review_focus=["Confirm pivot holds."],
    )


def session_mock(*side_effects):
    db = AsyncMock()
    db.execute.side_effect = list(side_effects)
    session = AsyncMock()
    session.__aenter__.return_value = db
    session.__aexit__.return_value = False
    session.db = db
    return session


def mappings_result(value):
    result = MagicMock()
    result.mappings.return_value.one_or_none.return_value = value
    return result


def claimed_dict(*, candles, images: bool = True, persisted: bool = False):
    return {
        "id": uuid4(),
        "screening_result_id": uuid4(),
        "chart_source": {
            "as_of_date": "2025-10-30",
            "context_sessions": 252,
            "detail_sessions": 126,
            "symbol": "EXAMPLE",
        },
        "frozen_ohlcv": (
            [
                {
                    "date": candle.date.isoformat(),
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                }
                for candle in candles
            ]
            if persisted
            else None
        ),
        "context_image": b"png-context" if images else None,
        "detail_image": b"png-detail" if images else None,
        "context_image_hash": "ctx-hash",
        "detail_image_hash": "det-hash",
        "source_hash": canonical_ohlcv_hash(candles),
        "renderer_version": "renderer-1",
        "model": "model-x",
        "reasoning_effort": "low",
        "max_tokens": 1234,
        "prompt_version": "prompt-1",
        "schema_version": "v1",
        "input_hash": None,
    }


def attempt_row():
    result = MagicMock()
    result.one.return_value = SimpleNamespace(id=uuid4(), attempt_number=1)
    return result


class VcpVisionWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.analysis_id = str(uuid4())
        self.candles = frozen_candles()
        self.instruments_result = mappings_result(
            {"symbol": "EXAMPLE", "instrument_id": uuid4()}
        )
        self.candles_result = MagicMock()
        self.candles_result.all.return_value = candle_rows(
            252, dt.date(2025, 10, 30) - dt.timedelta(days=251)
        )[::-1]

    def patch_worker(self, *, sessions, client_side_effect):
        session_patcher = patch(
            "app.services.vcp_vision.async_session",
            side_effect=sessions,
        )
        client_patcher = patch(
            "app.services.vcp_vision.OpenRouterVisionClient"
        )
        client_cls = client_patcher.start()
        self.client_cls = client_cls
        client = client_cls.return_value
        client.model = "model-x"
        client.reasoning_effort = "low"
        client.prompt_version = "prompt-1"
        client.schema_version = "v1"
        client.build_request.return_value = {"prompt": "hello"}
        client.send_once = AsyncMock(side_effect=client_side_effect)
        enabled_patcher = patch.object(vision_settings, "vcp_vision_enabled", True)
        session_patcher.start()
        enabled_patcher.start()
        self.addCleanup(session_patcher.stop)
        self.addCleanup(client_patcher.stop)
        self.addCleanup(enabled_patcher.stop)
        return client

    async def test_disabled_returns_immediately(self) -> None:
        claim = session_mock(mappings_result(claimed_dict(candles=self.candles)))
        finish = session_mock(MagicMock())
        with patch.object(vision_settings, "vcp_vision_enabled", False):
            with patch(
                "app.services.vcp_vision.async_session",
                side_effect=[claim, finish],
            ):
                outcome = await run_vcp_vision_analysis({}, self.analysis_id)
        self.assertEqual(outcome, {"status": "failed", "analysis_id": self.analysis_id})
        params = finish.db.execute.await_args.args[1]
        self.assertEqual(params["error_code"], "VisionDisabled")

    async def test_unclaimable_analysis_is_skipped(self) -> None:
        session = session_mock(mappings_result(None))
        self.patch_worker(sessions=[session], client_side_effect=[])
        outcome = await run_vcp_vision_analysis({}, self.analysis_id)
        self.assertEqual(outcome, {"status": "skipped", "analysis_id": self.analysis_id})

    async def test_arq_retry_settles_interrupted_run_without_provider_replay(self) -> None:
        settle = session_mock(MagicMock(), MagicMock())
        claim = session_mock(mappings_result(None))
        client = self.patch_worker(
            sessions=[settle, claim], client_side_effect=[]
        )

        outcome = await run_vcp_vision_analysis(
            {"job_try": 2}, self.analysis_id
        )

        self.assertEqual(outcome["status"], "skipped")
        self.assertEqual(settle.db.execute.await_count, 2)
        recovery_sql = str(settle.db.execute.await_args_list[0].args[0])
        self.assertIn("transport_unknown", recovery_sql)
        analysis_params = settle.db.execute.await_args_list[1].args[1]
        self.assertEqual(analysis_params["status"], "failed")
        self.assertEqual(client.send_once.await_count, 0)

    async def test_unexpected_worker_error_settles_running_analysis(self) -> None:
        claim = session_mock(
            mappings_result(
                claimed_dict(candles=self.candles, persisted=True)
            )
        )
        settle = session_mock(MagicMock(), MagicMock())
        self.patch_worker(sessions=[claim, settle], client_side_effect=[])
        self.client_cls.return_value.build_request.side_effect = RuntimeError(
            "renderer packet failure"
        )

        outcome = await run_vcp_vision_analysis({}, self.analysis_id)

        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(settle.db.execute.await_count, 2)
        attempt_sql = str(settle.db.execute.await_args_list[0].args[0])
        self.assertIn("transport_unknown", attempt_sql)
        analysis_params = settle.db.execute.await_args_list[1].args[1]
        self.assertEqual(analysis_params["status"], "failed")
        self.assertIn("renderer packet failure", analysis_params["message"])

    def test_dispatch_job_ids_are_unique(self) -> None:
        first = vcp_vision_job_id(self.analysis_id)
        second = vcp_vision_job_id(self.analysis_id)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith(f"vcp-vision:{self.analysis_id}:"))

    async def test_missing_images_fails_cleanly(self) -> None:
        claim = session_mock(mappings_result(claimed_dict(candles=self.candles, images=False)))
        finish = session_mock(MagicMock())
        self.patch_worker(sessions=[claim, finish], client_side_effect=[])
        outcome = await run_vcp_vision_analysis({}, self.analysis_id)
        self.assertEqual(outcome["status"], "failed")
        params = finish.db.execute.await_args.args[1]
        self.assertEqual(params["error_code"], "MissingImages")

    async def test_source_hash_mismatch_refuses_analysis(self) -> None:
        changed = self.candles[:-1] + [
            self.candles[-1].__class__(
                date=self.candles[-1].date,
                open=self.candles[-1].open,
                high=self.candles[-1].high + 1,
                low=self.candles[-1].low,
                close=self.candles[-1].close,
                volume=self.candles[-1].volume,
            )
        ]
        claim = session_mock(mappings_result(claimed_dict(candles=changed)))
        reload = session_mock(self.instruments_result, self.candles_result)
        finish = session_mock(MagicMock())
        self.patch_worker(sessions=[claim, reload, finish], client_side_effect=[])
        outcome = await run_vcp_vision_analysis({}, self.analysis_id)
        self.assertEqual(outcome["status"], "failed")
        params = finish.db.execute.await_args.args[1]
        self.assertEqual(params["error_code"], "ValueError")
        self.assertIn("source hash", params["error_message"])

    async def test_success_stores_snapped_result(self) -> None:
        claim = session_mock(mappings_result(claimed_dict(candles=self.candles)))
        reload = session_mock(self.instruments_result, self.candles_result)
        start = session_mock(attempt_row())
        attempt_finish = session_mock(MagicMock())
        finish = session_mock(MagicMock())
        client = self.patch_worker(
            sessions=[claim, reload, start, attempt_finish, finish],
            client_side_effect=[
                VisionLLMResult(
                    result=in_range_result(),
                    request_id="req-1",
                    usage={"total_tokens": 42},
                    input_hash="h",
                    cost=0.001,
                    request_payload={},
                    response_payload={"choices": []},
                )
            ],
        )
        outcome = await run_vcp_vision_analysis({}, self.analysis_id)
        self.assertEqual(outcome, {"status": "succeeded", "analysis_id": self.analysis_id})
        params = finish.db.execute.await_args.args[1]
        self.assertEqual(params["status"], "succeeded")
        self.assertEqual(params["verdict"], "valid")
        self.assertIn("derived", params["result"])
        self.assertEqual(client.send_once.await_count, 1)
        attempt_params = attempt_finish.db.execute.await_args.args[1]
        self.assertEqual(attempt_params["status"], "succeeded")

    async def test_persisted_packet_avoids_mutable_candle_reload(self) -> None:
        claim = session_mock(
            mappings_result(
                claimed_dict(candles=self.candles, persisted=True)
            )
        )
        start = session_mock(attempt_row())
        attempt_finish = session_mock(MagicMock())
        finish = session_mock(MagicMock())
        self.patch_worker(
            sessions=[claim, start, attempt_finish, finish],
            client_side_effect=[
                VisionLLMResult(
                    result=in_range_result(),
                    request_id="req-persisted",
                    usage={"prompt_tokens": 10, "completion_tokens": 5},
                    input_hash="unused",
                    cost=0.002,
                    request_payload={},
                    response_payload={"choices": []},
                )
            ],
        )

        outcome = await run_vcp_vision_analysis({}, self.analysis_id)

        self.assertEqual(outcome["status"], "succeeded")
        self.assertEqual(self.client_cls.call_args.kwargs["model"], "model-x")
        self.assertEqual(
            self.client_cls.call_args.kwargs["reasoning_effort"], "low"
        )
        self.assertEqual(self.client_cls.call_args.kwargs["max_tokens"], 1234)
        finish_params = finish.db.execute.await_args.args[1]
        self.assertEqual(
            finish_params["usage"],
            '{"input":10,"output":5,"reasoning":0,"cached":0}',
        )

    async def test_schema_violation_fails_without_retry(self) -> None:
        claim = session_mock(mappings_result(claimed_dict(candles=self.candles)))
        reload = session_mock(self.instruments_result, self.candles_result)
        start = session_mock(attempt_row())
        attempt_finish_invalid = session_mock(MagicMock())
        finish = session_mock(MagicMock())
        from tests.test_vcp_vision_validation import valid_result as build_valid

        bad = build_valid()
        bad.contraction_anchors = [
            bad.contraction_anchors[0].__class__(date="2025-11-03", evidence="a"),
            bad.contraction_anchors[1].__class__(date="2025-11-04", evidence="b"),
        ]
        client = self.patch_worker(
            sessions=[
                claim, reload, start, attempt_finish_invalid, finish,
            ],
            client_side_effect=[
                VisionLLMResult(
                    result=bad,
                    request_id="req-2",
                    usage={"total_tokens": 10},
                    input_hash="h",
                    cost=0.0,
                    request_payload={},
                    response_payload={},
                )
            ],
        )
        outcome = await run_vcp_vision_analysis({}, self.analysis_id)
        self.assertEqual(outcome["status"], "failed")
        params = finish.db.execute.await_args.args[1]
        self.assertEqual(params["error_code"], "VisionSchemaError")
        self.assertEqual(client.send_once.await_count, 1)
        attempt_params = attempt_finish_invalid.db.execute.await_args.args[1]
        self.assertEqual(attempt_params["status"], "invalid_response")

    async def test_retryable_error_retries_once_then_fails(self) -> None:
        claim = session_mock(mappings_result(claimed_dict(candles=self.candles)))
        reload = session_mock(self.instruments_result, self.candles_result)
        start_1 = session_mock(attempt_row())
        finish_1 = session_mock(MagicMock())
        start_2 = session_mock(attempt_row())
        finish_2 = session_mock(MagicMock())
        finish = session_mock(MagicMock())
        client = self.patch_worker(
            sessions=[
                claim, reload, start_1, finish_1, start_2, finish_2, finish,
            ],
            client_side_effect=[
                VisionLLMError(
                    "rate limited",
                    http_status=429,
                    attempt_status="provider_error",
                    retryable=True,
                    usage={"total_tokens": 1},
                ),
                VisionLLMError(
                    "still rate limited",
                    http_status=429,
                    attempt_status="provider_error",
                    retryable=True,
                    usage={"total_tokens": 1},
                ),
            ],
        )
        with patch("app.services.vcp_vision.asyncio.sleep", new=AsyncMock()):
            outcome = await run_vcp_vision_analysis({}, self.analysis_id)
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(client.send_once.await_count, 2)
        params = finish.db.execute.await_args.args[1]
        self.assertEqual(params["error_code"], "VisionLLMError")
        self.assertEqual(params["status"], "failed")
        self.assertEqual(start_2.db.execute.await_count, 1)


if __name__ == "__main__":
    unittest.main()
