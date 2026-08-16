import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.vcp_vision import (
    EXPECTED_CHART_HEIGHT,
    EXPECTED_CHART_WIDTH,
    PNG_MAGIC,
    VisionUploadError,
    canonical_ohlcv_hash,
    compact_ohlcv_table,
    freeze_result_ohlcv,
    frozen_ohlcv_from_payload,
    frozen_ohlcv_payload,
    validate_chart_png,
)


def candle_rows(count: int, start: dt.date) -> list[SimpleNamespace]:
    rows = []
    cursor = start
    while len(rows) < count:
        rows.append(
            SimpleNamespace(
                candle_start=dt.datetime(
                    cursor.year, cursor.month, cursor.day, 15, 30, tzinfo=dt.timezone.utc
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


def png_payload(
    *,
    width: int = EXPECTED_CHART_WIDTH,
    height: int = EXPECTED_CHART_HEIGHT,
    size_pad: int = 8,
) -> bytes:
    payload = bytearray(PNG_MAGIC)
    payload += b"\x00\x00\x00\x0dIHDR"
    payload += width.to_bytes(4, "big") + height.to_bytes(4, "big")
    payload += b"\x08\x02\x00\x00\x00"
    payload += b"\x00" * size_pad
    return bytes(payload)


class VcpVisionCandleTests(unittest.IsolatedAsyncioTestCase):
    async def test_freeze_truncates_to_context_window(self) -> None:
        db = AsyncMock()
        result = MagicMock()
        result.all.return_value = candle_rows(300, dt.date(2025, 1, 4))[::-1]
        db.execute.return_value = result

        frozen = await freeze_result_ohlcv(
            db,
            instrument_id=uuid4(),
            as_of_date=dt.date(2025, 10, 30),
            context_sessions=252,
            detail_sessions=126,
            symbol="EXAMPLE",
        )

        self.assertEqual(len(frozen.candles), 252)
        self.assertEqual(frozen.symbol, "EXAMPLE")
        self.assertEqual(frozen.as_of_date, dt.date(2025, 10, 30))
        self.assertTrue(frozen.source_hash)
        self.assertIn('"date"', frozen.compact_json)
        self.assertEqual(frozen.candles[-1].date, dt.date(2025, 10, 30))
        statement = str(db.execute.await_args.args[0])
        self.assertIn("candle_start AT TIME ZONE 'Asia/Kolkata'", statement)

    async def test_freeze_rejects_insufficient_history(self) -> None:
        db = AsyncMock()
        result = MagicMock()
        result.all.return_value = candle_rows(50, dt.date(2025, 1, 1))
        db.execute.return_value = result

        with self.assertRaises(ValueError):
            await freeze_result_ohlcv(
                db,
                instrument_id=uuid4(),
                as_of_date=dt.date(2025, 10, 30),
                context_sessions=252,
                detail_sessions=126,
            )

    async def test_freeze_rejects_window_not_ending_on_scan_date(self) -> None:
        db = AsyncMock()
        result = MagicMock()
        result.all.return_value = candle_rows(252, dt.date(2025, 1, 1))[::-1]
        db.execute.return_value = result

        with self.assertRaisesRegex(ValueError, "Latest EOD candle"):
            await freeze_result_ohlcv(
                db,
                instrument_id=uuid4(),
                as_of_date=dt.date(2025, 10, 30),
                context_sessions=252,
                detail_sessions=126,
            )

    async def test_freeze_rejects_detail_window_larger_than_context(self) -> None:
        with self.assertRaisesRegex(ValueError, "Detail sessions"):
            await freeze_result_ohlcv(
                AsyncMock(),
                instrument_id=uuid4(),
                as_of_date=dt.date(2025, 10, 30),
                context_sessions=126,
                detail_sessions=252,
            )

    async def test_freeze_rejects_non_positive_log_chart_prices(self) -> None:
        db = AsyncMock()
        rows = candle_rows(
            252, dt.date(2025, 10, 30) - dt.timedelta(days=251)
        )
        rows[100].low_price = 0
        result = MagicMock()
        result.all.return_value = rows[::-1]
        db.execute.return_value = result

        with self.assertRaisesRegex(ValueError, "positive prices"):
            await freeze_result_ohlcv(
                db,
                instrument_id=uuid4(),
                as_of_date=dt.date(2025, 10, 30),
                context_sessions=252,
                detail_sessions=126,
            )

    async def test_persisted_packet_round_trips_without_database_access(self) -> None:
        db = AsyncMock()
        result = MagicMock()
        result.all.return_value = candle_rows(
            252, dt.date(2025, 10, 30) - dt.timedelta(days=251)
        )[::-1]
        db.execute.return_value = result
        frozen = await freeze_result_ohlcv(
            db,
            instrument_id=uuid4(),
            as_of_date=dt.date(2025, 10, 30),
            context_sessions=252,
            detail_sessions=126,
            symbol="EXAMPLE",
        )

        restored = frozen_ohlcv_from_payload(
            frozen_ohlcv_payload(frozen),
            symbol="EXAMPLE",
            as_of_date=frozen.as_of_date,
            context_sessions=252,
            detail_sessions=126,
        )

        self.assertEqual(restored.source_hash, frozen.source_hash)
        self.assertEqual(restored.candles, frozen.candles)

    def test_canonical_hash_is_deterministic(self) -> None:
        candles = candle_rows(5, dt.date(2025, 1, 1))
        from app.services.vcp_vision import FrozenCandle

        frozen = [
            FrozenCandle(
                date=row.candle_start.date(),
                open=float(row.open_price),
                high=float(row.high_price),
                low=float(row.low_price),
                close=float(row.close_price),
                volume=int(row.volume),
            )
            for row in candles
        ]
        first = canonical_ohlcv_hash(frozen)
        second = canonical_ohlcv_hash(frozen)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_compact_table_has_50ma_volume_column(self) -> None:
        from app.services.vcp_vision import FrozenCandle

        candles = [
            FrozenCandle(
                date=dt.date(2025, 1, 1) + dt.timedelta(days=index),
                open=100.0,
                high=105.0,
                low=95.0,
                close=102.0,
                volume=100_000 + index,
            )
            for index in range(3)
        ]
        table = compact_ohlcv_table(candles)
        lines = table.splitlines()
        self.assertEqual(lines[0], "Date,O,H,L,C,Vol,Vol/50MA")
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[1].split(",")[0], "2025-01-01")

    def test_png_validation_accepts_valid_payload(self) -> None:
        width, height = validate_chart_png(
            png_payload(),
            max_bytes=3 * 1024 * 1024,
        )
        self.assertEqual((width, height), (1280, 720))

    def test_png_validation_rejects_wrong_magic(self) -> None:
        with self.assertRaises(VisionUploadError):
            validate_chart_png(b"not a png at all", max_bytes=1024)

    def test_png_validation_rejects_missing_ihdr(self) -> None:
        payload = bytearray(png_payload())
        payload[12:16] = b"NOPE"
        with self.assertRaises(VisionUploadError):
            validate_chart_png(bytes(payload), max_bytes=1024)

    def test_png_validation_rejects_wrong_dimensions(self) -> None:
        with self.assertRaises(VisionUploadError):
            validate_chart_png(
                png_payload(width=640, height=360),
                max_bytes=3 * 1024 * 1024,
            )

    def test_png_validation_rejects_oversized_payload(self) -> None:
        with self.assertRaises(VisionUploadError):
            validate_chart_png(png_payload(size_pad=4096), max_bytes=16)


if __name__ == "__main__":
    unittest.main()
