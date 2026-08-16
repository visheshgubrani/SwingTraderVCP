import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
from main import app

from app.routers.vcp_vision import (
    create_vcp_vision_analysis,
    get_analysis_chart,
    get_latest_vcp_vision_analysis,
    get_vcp_vision_status,
    review_vcp_vision_analysis,
    retry_vcp_vision_analysis,
    upload_analysis_chart,
)
from app.routers.vcp_vision import settings as vision_settings


def result_guard_row(*, as_of_date="2025-10-30"):
    return {
        "result_id": uuid4(),
        "instrument_id": uuid4(),
        "symbol": "EXAMPLE",
        "as_of_date": dt.date.fromisoformat(as_of_date) if as_of_date else None,
    }


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


def mappings_result(value):
    result = MagicMock()
    result.mappings.return_value.one_or_none.return_value = value
    return result


def scalar_result(value):
    result = MagicMock()
    result.scalar_one.return_value = value
    result.scalar_one_or_none.return_value = value
    return result


def analysis_row(**overrides):
    now = dt.datetime.now(dt.timezone.utc)
    row = {
        "id": uuid4(),
        "screening_result_id": uuid4(),
        "status": "succeeded",
        "chart_source": {
            "as_of_date": "2025-10-30",
            "context_sessions": 252,
            "detail_sessions": 126,
            "symbol": "EXAMPLE",
        },
        "renderer_version": "renderer-1",
        "model": "model-x",
        "reasoning_effort": "medium",
        "max_tokens": 2400,
        "prompt_version": "prompt-1",
        "schema_version": "v1",
        "result": {"verdict": "valid"},
        "ai_verdict": "valid",
        "error_code": None,
        "error_message": None,
        "usage": {"total_tokens": 5},
        "cost": 0.001,
        "human_verdict": None,
        "human_note": None,
        "human_reviewed_at": None,
        "created_at": now,
        "updated_at": now,
        "instrument_id": uuid4(),
        "source_hash": "placeholder",
        "frozen_ohlcv": [],
    }
    row.update(overrides)
    return row


class VcpVisionApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        settings_patchers = [
            patch.object(vision_settings, "vcp_vision_enabled", True),
            patch.object(vision_settings, "vcp_vision_reasoning_effort", "medium"),
            patch.object(vision_settings, "vcp_vision_max_tokens", 16384),
        ]
        for settings_patcher in settings_patchers:
            settings_patcher.start()
            self.addCleanup(settings_patcher.stop)

    def test_chart_upload_is_a_binary_request_body(self) -> None:
        operation = app.openapi()["paths"][
            "/api/v1/screening/vcp-vision/analyses/{analysis_id}/charts/{chart_kind}"
        ]["put"]
        request_body = operation["requestBody"]
        self.assertTrue(request_body["required"])
        self.assertIn("application/octet-stream", request_body["content"])

    async def test_create_reuses_identical_analysis(self) -> None:
        candles = MagicMock()
        candles.all.return_value = candle_rows(
            252, dt.date(2025, 10, 30) - dt.timedelta(days=251)
        )[::-1]
        db = AsyncMock()
        db.execute.side_effect = [
            mappings_result(result_guard_row()),
            candles,
            MagicMock(),  # advisory lock
            mappings_result({"id": uuid4(), "status": "succeeded"}),
        ]

        response = await create_vcp_vision_analysis(uuid4(), db)

        self.assertTrue(response.reused)
        self.assertEqual(response.status, "succeeded")
        self.assertEqual(db.execute.await_count, 4)
        self.assertIsNone(db.commit.await_args)
        self.assertEqual(db.rollback.await_count, 1)

    async def test_create_creates_new_awaiting_capture(self) -> None:
        db = AsyncMock()
        candles = MagicMock()
        candles.all.return_value = candle_rows(
            252, dt.date(2025, 10, 30) - dt.timedelta(days=251)
        )[::-1]
        inserted = scalar_result(uuid4())
        db.execute.side_effect = [
            mappings_result(result_guard_row()),
            candles,
            MagicMock(),  # advisory lock
            mappings_result(None),  # no reuse
            inserted,
        ]

        response = await create_vcp_vision_analysis(uuid4(), db)

        self.assertFalse(response.reused)
        self.assertEqual(response.status, "awaiting_capture")
        self.assertIsNotNone(response.analysis_id)
        self.assertEqual(db.commit.await_count, 1)
        insert_params = db.execute.await_args_list[4].args[1]
        self.assertEqual(insert_params["reasoning_effort"], "medium")
        self.assertEqual(insert_params["max_tokens"], 16384)
        self.assertIn('"date"', insert_params["frozen_ohlcv"])

    async def test_create_rejects_when_feature_is_disabled(self) -> None:
        db = AsyncMock()
        with patch.object(vision_settings, "vcp_vision_enabled", False):
            with self.assertRaises(HTTPException) as raised:
                await create_vcp_vision_analysis(uuid4(), db)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(db.execute.await_count, 0)

    async def test_create_rejects_missing_result(self) -> None:
        db = AsyncMock()
        db.execute.return_value = mappings_result(None)
        with self.assertRaises(HTTPException) as raised:
            await create_vcp_vision_analysis(uuid4(), db)
        self.assertEqual(raised.exception.status_code, 404)

    async def test_create_rejects_null_as_of_date(self) -> None:
        db = AsyncMock()
        db.execute.return_value = mappings_result(
            result_guard_row(as_of_date=None)
        )
        with self.assertRaises(HTTPException) as raised:
            await create_vcp_vision_analysis(uuid4(), db)
        self.assertEqual(raised.exception.status_code, 422)

    async def test_create_rejects_insufficient_history(self) -> None:
        db = AsyncMock()
        short = MagicMock()
        short.all.return_value = candle_rows(10, dt.date(2025, 10, 1))
        db.execute.side_effect = [
            mappings_result(result_guard_row()),
            short,
        ]
        with self.assertRaises(HTTPException) as raised:
            await create_vcp_vision_analysis(uuid4(), db)
        self.assertEqual(raised.exception.status_code, 422)

    async def test_upload_enqueues_when_both_charts_present(self) -> None:
        png = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\x0dIHDR"
            + (1280).to_bytes(4, "big")
            + (720).to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00"
            + b"\x00" * 16
        )
        db = AsyncMock()
        stored = scalar_result(uuid4())
        transition = scalar_result(uuid4())
        db.execute.side_effect = [stored, transition]
        enqueue = AsyncMock()
        enqueue.return_value = MagicMock()

        class FakeState:
            redis = MagicMock()
            redis.enqueue_job = enqueue

        request = SimpleNamespace(app=SimpleNamespace(state=FakeState()))

        response = await upload_analysis_chart(
            uuid4(), "context", png, request, db
        )

        self.assertEqual(response.status, "queued")
        self.assertEqual(enqueue.await_count, 1)
        self.assertEqual(db.commit.await_count, 1)

    async def test_upload_rejects_invalid_png(self) -> None:
        db = AsyncMock()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=MagicMock())))
        with self.assertRaises(HTTPException) as raised:
            await upload_analysis_chart(uuid4(), "context", b"nope", request, db)
        self.assertEqual(raised.exception.status_code, 400)

    async def test_upload_rejects_missing_analysis(self) -> None:
        png = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\x0dIHDR"
            + (1280).to_bytes(4, "big")
            + (720).to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00"
            + b"\x00" * 16
        )
        db = AsyncMock()
        db.execute.return_value = scalar_result(None)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=MagicMock())))
        with self.assertRaises(HTTPException) as raised:
            await upload_analysis_chart(uuid4(), "context", png, request, db)
        self.assertEqual(raised.exception.status_code, 404)

    async def test_upload_rejects_overwriting_queued_or_completed_images(self) -> None:
        png = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\x0dIHDR"
            + (1280).to_bytes(4, "big")
            + (720).to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00"
            + b"\x00" * 16
        )
        db = AsyncMock()
        db.execute.side_effect = [scalar_result(None), scalar_result("succeeded")]
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=MagicMock())))

        with self.assertRaises(HTTPException) as raised:
            await upload_analysis_chart(uuid4(), "context", png, request, db)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("immutable", raised.exception.detail)
        status_sql = str(db.execute.await_args_list[1].args[0])
        self.assertIn("r.visibility = 'personal'", status_sql)

    async def test_missing_queue_marks_analysis_failed(self) -> None:
        png = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\x0dIHDR"
            + (1280).to_bytes(4, "big")
            + (720).to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00"
            + b"\x00" * 16
        )
        db = AsyncMock()
        db.execute.side_effect = [
            scalar_result(uuid4()),
            scalar_result(uuid4()),
            MagicMock(),
        ]
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))

        with self.assertRaises(HTTPException) as raised:
            await upload_analysis_chart(uuid4(), "detail", png, request, db)

        self.assertEqual(raised.exception.status_code, 503)
        failure_params = db.execute.await_args_list[2].args[1]
        self.assertIn("Redis background queue", failure_params["error"])
        self.assertEqual(db.commit.await_count, 2)

    async def test_get_chart_serves_png(self) -> None:
        row = {"image": b"png-bytes"}
        db = AsyncMock()
        db.execute.return_value = mappings_result(row)
        response = await get_analysis_chart(uuid4(), "context", db)
        self.assertEqual(response.media_type, "image/png")
        self.assertEqual(bytes(response.body), b"png-bytes")

    async def test_get_chart_missing_analysis_404(self) -> None:
        db = AsyncMock()
        db.execute.return_value = mappings_result(None)
        with self.assertRaises(HTTPException) as raised:
            await get_analysis_chart(uuid4(), "detail", db)
        self.assertEqual(raised.exception.status_code, 404)

    async def test_latest_rejects_unknown_result(self) -> None:
        db = AsyncMock()
        db.execute.return_value = mappings_result(None)
        with self.assertRaises(HTTPException) as raised:
            await get_latest_vcp_vision_analysis(uuid4(), db)
        self.assertEqual(raised.exception.status_code, 404)

    async def test_latest_404_when_no_analysis(self) -> None:
        db = AsyncMock()
        db.execute.side_effect = [
            mappings_result(result_guard_row()),
            scalar_result(None),
        ]
        with self.assertRaises(HTTPException) as raised:
            await get_latest_vcp_vision_analysis(uuid4(), db)
        self.assertEqual(raised.exception.status_code, 404)

    async def test_latest_returns_analysis_with_frozen(self) -> None:
        db = AsyncMock()
        candles = MagicMock()
        candles.all.return_value = candle_rows(
            252, dt.date(2025, 10, 30) - dt.timedelta(days=251)
        )[::-1]
        analysis = mappings_result(analysis_row())
        attempts = MagicMock()
        attempts.mappings.return_value = []
        db.execute.side_effect = [
            mappings_result(result_guard_row()),
            scalar_result(uuid4()),
            analysis,
            candles,
            attempts,
        ]

        response = await get_latest_vcp_vision_analysis(uuid4(), db)

        self.assertEqual(response["status"], "succeeded")
        self.assertIsNotNone(response["frozen"])
        self.assertEqual(response["ai_verdict"], "valid")
        self.assertEqual(response["attempts"], [])

    async def test_status_returns_counts(self) -> None:
        result = MagicMock()
        result.all.return_value = [
            SimpleNamespace(status="queued", count=2),
            SimpleNamespace(status="succeeded", count=1),
        ]
        db = AsyncMock()
        db.execute.return_value = result
        with patch.object(vision_settings, "vcp_vision_enabled", True):
            response = await get_vcp_vision_status(db)
        self.assertTrue(response.enabled)
        self.assertEqual(response.counts, {"queued": 2, "succeeded": 1})

    async def test_review_records_human_verdict(self) -> None:
        candles = MagicMock()
        candles.all.return_value = candle_rows(
            252, dt.date(2025, 10, 30) - dt.timedelta(days=251)
        )[::-1]
        attempts = MagicMock()
        attempts.mappings.return_value = []
        db = AsyncMock()
        db.execute.side_effect = [
            MagicMock(),  # review UPDATE
            mappings_result(analysis_row()),  # main select
            candles,  # frozen rebuild
            attempts,  # attempts select
        ]
        from app.schemas.vcp_vision import VcpVisionReviewRequest

        payload = VcpVisionReviewRequest(verdict="valid", note="Looks good")
        response = await review_vcp_vision_analysis(uuid4(), payload, db)
        self.assertEqual(response["ai_verdict"], "valid")
        self.assertEqual(response["attempts"], [])

    async def test_review_requires_succeeded_analysis(self) -> None:
        db = AsyncMock()
        db.execute.side_effect = [scalar_result(None), scalar_result("running")]
        from app.schemas.vcp_vision import VcpVisionReviewRequest

        with self.assertRaises(HTTPException) as raised:
            await review_vcp_vision_analysis(
                uuid4(),
                VcpVisionReviewRequest(verdict="uncertain", note="Wait"),
                db,
            )

        self.assertEqual(raised.exception.status_code, 409)

    async def test_retry_enqueues_failed_analysis(self) -> None:
        db = AsyncMock()
        db.execute.return_value = scalar_result(uuid4())
        enqueue = AsyncMock(return_value=MagicMock())
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    redis=SimpleNamespace(enqueue_job=enqueue)
                )
            )
        )

        response = await retry_vcp_vision_analysis(uuid4(), request, db)

        self.assertEqual(response.status, "queued")
        self.assertEqual(db.commit.await_count, 1)
        self.assertEqual(enqueue.await_count, 1)
        transition_sql = str(db.execute.await_args_list[0].args[0])
        self.assertIn("NOT EXISTS", transition_sql)
        self.assertIn("active.reasoning_effort", transition_sql)
        self.assertIn("GREATEST", transition_sql)
        transition_params = db.execute.await_args_list[0].args[1]
        self.assertEqual(transition_params["max_tokens"], 16384)

    async def test_retry_conflicts_when_transition_is_not_allowed(self) -> None:
        db = AsyncMock()
        db.execute.side_effect = [scalar_result(None), scalar_result("failed")]
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(redis=MagicMock()))
        )

        with self.assertRaises(HTTPException) as raised:
            await retry_vcp_vision_analysis(uuid4(), request, db)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("equivalent active analysis", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
