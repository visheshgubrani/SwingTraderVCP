"""On-demand VCP vision validator.

Advisory chart-image second opinion for the personal scanner. The AI verdict
never changes technical rank, ``vcp_detected``, ``reviewer_status``,
watchlists, trade drafts, or execution state. It only reads frozen EOD candles
and stored chart PNGs, calls one OpenRouter vision model, and persists the
sanitized outcome plus every provider attempt for audit.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.services.canonical_ohlcv import compact_ohlcv_table
from app.services.fundamental_llm import sanitize_provider_payload
from app.services.openrouter_content import parse_openrouter_structured_content
from app.services.screener import candle_trading_date

logger = logging.getLogger(__name__)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
EXPECTED_CHART_WIDTH = 1280
EXPECTED_CHART_HEIGHT = 720
MAX_DATE_DRIFT_DAYS = 3
MAX_RETRYABLE_ATTEMPTS = 2

Verdict = Literal["valid", "invalid", "uncertain"]


class VisionLLMError(RuntimeError):
    """A model request failed, with safe provider metadata retained."""

    def __init__(
        self,
        message: str,
        *,
        response_payload: dict[str, Any] | None = None,
        http_status: int | None = None,
        request_id: str | None = None,
        usage: dict[str, Any] | None = None,
        cost: float = 0.0,
        retryable: bool = False,
        attempt_status: Literal[
            "invalid_response",
            "provider_error",
            "transport_unknown",
        ] = "provider_error",
    ) -> None:
        super().__init__(message)
        self.response_payload = response_payload
        self.http_status = http_status
        self.request_id = request_id
        self.usage = usage or {}
        self.cost = cost
        self.retryable = retryable
        self.attempt_status = attempt_status


class VisionSchemaError(ValueError):
    """The model response violates the strict date-anchor contract."""


class VisionUploadError(ValueError):
    """A chart PNG failed signature, dimension, or size validation."""


class VcpPriorUptrend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment: Literal["clear", "moderate", "weak", "unclear"]
    note: str = Field(default="", max_length=300)


class VcpVolumeAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment: Literal["drying_up", "supportive", "mixed", "weak", "unclear"]
    note: str = Field(default="", max_length=300)


class VcpBaseWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str
    end: str
    quality: Literal["solid", "loose", "unclear"]
    notes: str = Field(default="", max_length=240)


class VcpContractionAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    evidence: str = Field(min_length=1, max_length=240)


class VcpPivotZone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str
    end: str
    rationale: str = Field(min_length=1, max_length=240)


class VcpVisionResultV1(BaseModel):
    """Strict output contract for the VCP validator (prompt/schema v1)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["vcp_visual_validator_result_v1"] = (
        "vcp_visual_validator_result_v1"
    )
    verdict: Verdict
    confidence: int = Field(ge=0, le=100)
    summary: str = Field(min_length=1, max_length=600)
    prior_uptrend: VcpPriorUptrend
    volume: VcpVolumeAssessment
    bases: list[VcpBaseWindow] = Field(default_factory=list, max_length=5)
    contraction_anchors: list[VcpContractionAnchor] = Field(
        min_length=2,
        max_length=8,
        description=(
            "Ordered swing-peak dates that bound contraction windows. Two nested "
            "contractions need three peaks: C1 start, C2 start, and the final peak "
            "that completes C2 (often the left edge of the pivot zone). Verdict "
            "valid requires at least three peaks, or two start-peaks plus a later "
            "pivot_zone that closes the last window."
        ),
    )
    pivot_zone: VcpPivotZone | None = None
    supporting_evidence: list[str] = Field(default_factory=list, max_length=6)
    contrary_evidence: list[str] = Field(default_factory=list, max_length=6)
    human_review_focus: list[str] = Field(default_factory=list, max_length=6)


@dataclass(frozen=True)
class FrozenCandle:
    date: dt.date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class FrozenOhlcv:
    symbol: str
    as_of_date: dt.date
    candles: list[FrozenCandle]
    source_hash: str
    compact_json: str
    context_sessions: int
    detail_sessions: int

    def candles_by_date(self) -> dict[dt.date, FrozenCandle]:
        return {candle.date: candle for candle in self.candles}


@dataclass(frozen=True)
class VisionLLMResult:
    result: VcpVisionResultV1
    request_id: str | None
    usage: dict[str, Any]
    input_hash: str
    cost: float
    request_payload: dict[str, Any]
    response_payload: dict[str, Any]


def vcp_vision_job_id(analysis_id: UUID | str) -> str:
    """Return a unique dispatch ID while the DB row remains the idempotency guard."""
    return f"vcp-vision:{analysis_id}:{uuid4()}"


# ---------------------------------------------------------------------------
# Candle freezing
# ---------------------------------------------------------------------------

def canonical_ohlcv_hash(candles: list[FrozenCandle]) -> str:
    payload = json.dumps(
        [
            {
                "date": candle.date.isoformat(),
                "o": candle.open,
                "h": candle.high,
                "l": candle.low,
                "c": candle.close,
                "v": candle.volume,
            }
            for candle in candles
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def frozen_ohlcv_payload(frozen: FrozenOhlcv) -> list[dict[str, Any]]:
    """Return the durable canonical candle packet stored with an analysis."""
    return [
        {
            "date": candle.date.isoformat(),
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
        }
        for candle in frozen.candles
    ]


def frozen_ohlcv_from_payload(
    payload: Any,
    *,
    symbol: str,
    as_of_date: dt.date,
    context_sessions: int,
    detail_sessions: int,
) -> FrozenOhlcv:
    """Rehydrate and validate a persisted immutable OHLCV packet."""
    if not isinstance(payload, list) or not payload:
        raise ValueError("Analysis has no persisted frozen OHLCV packet.")
    candles: list[FrozenCandle] = []
    for index, raw in enumerate(payload):
        if not isinstance(raw, Mapping):
            raise ValueError(f"Frozen candle {index} is not an object.")
        try:
            candle = FrozenCandle(
                date=dt.date.fromisoformat(str(raw["date"])),
                open=float(raw["open"]),
                high=float(raw["high"]),
                low=float(raw["low"]),
                close=float(raw["close"]),
                volume=int(raw["volume"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Frozen candle {index} is malformed.") from exc
        if not all(
            value > 0
            for value in (candle.open, candle.high, candle.low, candle.close)
        ):
            raise ValueError(f"Frozen candle {index} has a non-positive price.")
        if candle.low > min(candle.open, candle.close, candle.high):
            raise ValueError(f"Frozen candle {index} has an invalid low price.")
        if candle.high < max(candle.open, candle.close, candle.low):
            raise ValueError(f"Frozen candle {index} has an invalid high price.")
        if candle.volume < 0:
            raise ValueError(f"Frozen candle {index} has negative volume.")
        candles.append(candle)
    dates = [candle.date for candle in candles]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError("Frozen candle dates must be unique and ascending.")
    if len(candles) != context_sessions:
        raise ValueError(
            "Frozen candle packet length does not match its context window."
        )
    if detail_sessions > context_sessions:
        raise ValueError("Detail sessions cannot exceed context sessions.")
    if candles[-1].date != as_of_date:
        raise ValueError("Frozen candle packet does not end on its as-of date.")
    compact_json = json.dumps(
        [
            {
                "date": candle.date.isoformat(),
                "o": candle.open,
                "h": candle.high,
                "l": candle.low,
                "c": candle.close,
                "v": candle.volume,
            }
            for candle in candles
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return FrozenOhlcv(
        symbol=symbol,
        as_of_date=as_of_date,
        candles=candles,
        source_hash=canonical_ohlcv_hash(candles),
        compact_json=compact_json,
        context_sessions=context_sessions,
        detail_sessions=detail_sessions,
    )


async def freeze_result_ohlcv(
    db: AsyncSession,
    *,
    instrument_id: UUID,
    as_of_date: dt.date,
    context_sessions: int,
    detail_sessions: int,
    symbol: str = "",
) -> FrozenOhlcv:
    """Freeze the last ``context_sessions`` EOD candles through ``as_of_date``."""
    if detail_sessions > context_sessions:
        raise ValueError("Detail sessions cannot exceed context sessions.")
    result = await db.execute(
        text(
            """
            SELECT c.candle_start, c.open_price, c.high_price,
                   c.low_price, c.close_price, c.volume
            FROM market_candles c
            WHERE c.instrument_id = :instrument_id
              AND c.timeframe = '1d'
              AND (c.candle_start AT TIME ZONE 'Asia/Kolkata')::date <= :as_of_date
            ORDER BY c.candle_start DESC
            LIMIT :limit
            """
        ),
        {
            "instrument_id": instrument_id,
            "as_of_date": as_of_date,
            "limit": max(context_sessions, detail_sessions) * 2,
        },
    )
    rows = list(reversed(result.all()))
    candles = [
        FrozenCandle(
            date=candle_trading_date(row.candle_start),
            open=float(row.open_price),
            high=float(row.high_price),
            low=float(row.low_price),
            close=float(row.close_price),
            volume=int(row.volume or 0),
        )
        for row in rows
    ]
    if len(candles) < context_sessions:
        raise ValueError(
            f"Insufficient EOD candle history for vision validation: "
            f"found {len(candles)}, need {context_sessions} sessions through "
            f"{as_of_date.isoformat()}."
        )
    candles = candles[-context_sessions:]
    dates = [candle.date for candle in candles]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError(
            "EOD candles for vision validation must have unique ascending "
            "trading dates."
        )
    if any(
        price <= 0
        for candle in candles
        for price in (candle.open, candle.high, candle.low, candle.close)
    ):
        raise ValueError(
            "EOD candles for logarithmic vision charts require positive prices."
        )
    if candles[-1].date != as_of_date:
        raise ValueError(
            "Latest EOD candle for vision validation is "
            f"{candles[-1].date.isoformat()}, expected {as_of_date.isoformat()}."
        )
    compact_json = json.dumps(
        [
            {
                "date": candle.date.isoformat(),
                "o": candle.open,
                "h": candle.high,
                "l": candle.low,
                "c": candle.close,
                "v": candle.volume,
            }
            for candle in candles
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return FrozenOhlcv(
        symbol=symbol,
        as_of_date=as_of_date,
        candles=candles,
        source_hash=canonical_ohlcv_hash(candles),
        compact_json=compact_json,
        context_sessions=context_sessions,
        detail_sessions=detail_sessions,
    )


# ---------------------------------------------------------------------------
# PNG validation (no image dependency)
# ---------------------------------------------------------------------------

def validate_chart_png(
    payload: bytes,
    *,
    max_bytes: int,
    expected_width: int = EXPECTED_CHART_WIDTH,
    expected_height: int = EXPECTED_CHART_HEIGHT,
) -> tuple[int, int]:
    if not payload:
        raise VisionUploadError("Empty chart payload.")
    if len(payload) > max_bytes:
        raise VisionUploadError(
            f"Chart PNG is {len(payload)} bytes; limit is {max_bytes} bytes."
        )
    if len(payload) < 24 or not payload.startswith(PNG_MAGIC):
        raise VisionUploadError("Payload is not a PNG file (signature mismatch).")
    if payload[8:12] != (13).to_bytes(4, "big") or payload[12:16] != b"IHDR":
        raise VisionUploadError("PNG does not start with a valid IHDR chunk.")
    width = int.from_bytes(payload[16:20], "big")
    height = int.from_bytes(payload[20:24], "big")
    if width != expected_width or height != expected_height:
        raise VisionUploadError(
            f"Chart PNG must be {expected_width}x{expected_height}, got {width}x{height}."
        )
    return width, height


# ---------------------------------------------------------------------------
# Date-anchor snapping and deterministic enrichment
# ---------------------------------------------------------------------------

def snap_to_nearest_trading_date(
    anchor: dt.date,
    valid_dates: set[dt.date],
    max_drift_days: int = MAX_DATE_DRIFT_DAYS,
) -> dt.date | None:
    """Snap a model date to the nearest frozen trading bar within drift."""
    if anchor in valid_dates:
        return anchor
    for drift in range(1, max_drift_days + 1):
        before = anchor - dt.timedelta(days=drift)
        after = anchor + dt.timedelta(days=drift)
        if before in valid_dates:
            return before
        if after in valid_dates:
            return after
    return None


def _parse_date(value: str, valid_dates: set[dt.date]) -> dt.date:
    try:
        parsed = dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise VisionSchemaError(f"Invalid date {value!r}.") from exc
    snapped = snap_to_nearest_trading_date(parsed, valid_dates)
    if snapped is None:
        raise VisionSchemaError(
            f"Date {value} is not within {MAX_DATE_DRIFT_DAYS} calendar days of any "
            "frozen trading bar."
        )
    return snapped


def contraction_window_bounds(
    anchors: list[dt.date],
    pivot_zone: tuple[dt.date, dt.date] | None,
) -> list[tuple[dt.date, dt.date]]:
    """Pair consecutive peaks into windows; close the last with the pivot if needed.

    The model often returns only the swing peaks where each contraction begins
    (two peaks for two contractions). The last window then ends at a later
    pivot rather than at a third listed peak.
    """
    pairs = [
        (start, end) for start, end in zip(anchors, anchors[1:]) if start < end
    ]
    if len(pairs) >= 2 or not anchors or pivot_zone is None:
        return pairs
    close = pivot_zone[0] if pivot_zone[0] > anchors[-1] else (
        pivot_zone[1] if pivot_zone[1] > anchors[-1] else None
    )
    if close is not None:
        pairs.append((anchors[-1], close))
    return pairs


def _append_review_focus(items: list[str], note: str) -> list[str]:
    if note in items:
        return items
    if len(items) < 6:
        return [*items, note]
    return [*items[:-1], note]


def validate_and_snap_result(
    result: VcpVisionResultV1,
    valid_dates: set[dt.date],
) -> dict[str, Any]:
    """Validate all returned dates against the frozen packet and snap them."""
    if result.verdict == "valid" and result.pivot_zone is None:
        raise VisionSchemaError("A valid VCP verdict requires a pivot zone.")
    anchors = [_parse_date(anchor.date, valid_dates) for anchor in result.contraction_anchors]
    if any(a >= b for a, b in zip(anchors, anchors[1:])):
        raise VisionSchemaError(
            "Contraction anchors must be strictly ordered trading dates."
        )
    if len(set(anchors)) != len(anchors):
        raise VisionSchemaError("Duplicate contraction anchors are not allowed.")

    snapped_bases = []
    for base in result.bases:
        start = _parse_date(base.start, valid_dates)
        end = _parse_date(base.end, valid_dates)
        if start > end:
            raise VisionSchemaError(
                f"Base window {base.start}..{base.end} is inverted."
            )
        snapped_bases.append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "quality": base.quality,
                "notes": base.notes,
            }
        )

    pivot_zone = None
    if result.pivot_zone is not None:
        start = _parse_date(result.pivot_zone.start, valid_dates)
        end = _parse_date(result.pivot_zone.end, valid_dates)
        if start > end:
            raise VisionSchemaError(
                f"Pivot zone {result.pivot_zone.start}..{result.pivot_zone.end} "
                "is inverted."
            )
        pivot_zone = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "rationale": result.pivot_zone.rationale,
        }

    snapped_pivot = (
        (
            dt.date.fromisoformat(pivot_zone["start"]),
            dt.date.fromisoformat(pivot_zone["end"]),
        )
        if pivot_zone
        else None
    )
    verdict = result.verdict
    review_focus = list(result.human_review_focus)
    windows = contraction_window_bounds(anchors, snapped_pivot)
    if verdict == "valid" and len(windows) < 2:
        # Keep the advisory result instead of failing the analysis. Two
        # start-peaks without a later pivot cannot form two contraction windows.
        verdict = "uncertain"
        review_focus = _append_review_focus(
            review_focus,
            "Labeled valid, but fewer than two contraction windows could be "
            "derived from the returned anchors and pivot zone.",
        )

    return {
        "schema_version": result.schema_version,
        "verdict": verdict,
        "confidence": result.confidence,
        "summary": result.summary,
        "prior_uptrend": result.prior_uptrend.model_dump(mode="json"),
        "volume": result.volume.model_dump(mode="json"),
        "bases": snapped_bases,
        "contraction_anchors": [
            {"date": snapped.isoformat(), "evidence": anchor.evidence}
            for anchor, snapped in zip(result.contraction_anchors, anchors)
        ],
        "pivot_zone": pivot_zone,
        "supporting_evidence": result.supporting_evidence,
        "contrary_evidence": result.contrary_evidence,
        "human_review_focus": review_focus,
    }


def derive_contraction_metrics(
    *,
    candles_by_date: dict[dt.date, FrozenCandle],
    anchors: list[dt.date],
    pivot_zone: tuple[dt.date, dt.date] | None,
) -> dict[str, Any]:
    """Deterministic contraction ranges and pivot price from date anchors."""
    ordered_dates = sorted(candles_by_date)
    date_index = {date: index for index, date in enumerate(ordered_dates)}

    contractions: list[dict[str, Any]] = []
    for index, (start, end) in enumerate(
        contraction_window_bounds(anchors, pivot_zone), start=1
    ):
        low_idx = date_index[start]
        high_idx = date_index[end]
        window = ordered_dates[low_idx : high_idx + 1]
        highs = [candles_by_date[date].high for date in window]
        lows = [candles_by_date[date].low for date in window]
        peak = max(highs)
        trough = min(lows)
        contractions.append(
            {
                "label": f"C{index}",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "high": round(peak, 4),
                "low": round(trough, 4),
                "depth_pct": round((peak - trough) / peak * 100, 2) if peak > 0 else 0.0,
                "sessions": len(window),
            }
        )

    pivot_price: float | None = None
    if pivot_zone is not None:
        start, end = pivot_zone
        if start in date_index and end in date_index:
            window = ordered_dates[date_index[start] : date_index[end] + 1]
            pivot_price = round(
                max(candles_by_date[date].high for date in window), 4
            )

    return {"contractions": contractions, "pivot_price": pivot_price}


def enrich_stored_result(
    cleaned: dict[str, Any],
    candles_by_date: dict[dt.date, FrozenCandle],
) -> dict[str, Any]:
    anchors = [
        dt.date.fromisoformat(anchor["date"])
        for anchor in cleaned["contraction_anchors"]
    ]
    pivot_zone = cleaned.get("pivot_zone")
    derived = derive_contraction_metrics(
        candles_by_date=candles_by_date,
        anchors=anchors,
        pivot_zone=(
            (
                dt.date.fromisoformat(pivot_zone["start"]),
                dt.date.fromisoformat(pivot_zone["end"]),
            )
            if pivot_zone
            else None
        ),
    )
    return {**cleaned, "derived": derived}


# ---------------------------------------------------------------------------
# OpenRouter vision client
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_V1 = (
    "You are an expert equity chart analyst applying the Minervini / SEPA "
    "Volatility Contraction Pattern (VCP) methodology to Indian equities.\n"
    "A trader supplies two standardized daily candlestick-and-volume charts "
    "(a long context view and a shorter detail view on a logarithmic price "
    "scale) plus a canonical OHLCV table with exact trading dates.\n"
    "Apply your learned VCP expertise: assess the prior uptrend, base "
    "formation quality, the contraction sequence, volume behavior, and "
    "proximity to a potential pivot.\n"
    "Rules:\n"
    "1. Verdict 'valid' only when the chart coherently shows the classic VCP "
    "sequence (prior uptrend, at least two nested contractions with declining "
    "depth and drying-up volume, tight price action near a pivot). Verdict "
    "'invalid' when the pattern is clearly absent or the base is broken. "
    "Verdict 'uncertain' when the evidence is ambiguous or data is sparse.\n"
    "2. Every date you cite must exist exactly in the supplied OHLCV table; "
    "never invent dates, and keep evidence concise and grounded in the "
    "visible chart.\n"
    "3. This is an advisory second opinion only. Never recommend entry, stop, "
    "target, position size, or any execution action.\n"
    "4. contraction_anchors are ordered swing peaks that BOUND contraction "
    "windows. Two nested contractions require three strictly ordered peaks: "
    "the start of C1, the start of C2, and the final swing peak that completes "
    "C2 (often the left edge of the pivot zone). Verdict 'valid' requires "
    "those three peaks plus a pivot_zone. If you only see one clear "
    "contraction, use 'uncertain' or 'invalid' — never 'valid' with two peaks."
)

USER_PROMPT_V1 = (
    "Frozen EOD window ending {as_of_date}. Context chart: last "
    "{context_sessions} sessions. Detail chart: final {detail_sessions} "
    "sessions (logarithmic price scale). The compact OHLCV table below is the "
    "canonical reference for exact dates; the two charts visualize the same "
    "data. Symbol and company identity are intentionally omitted.\n\n"
    "Canonical OHLCV (Date, Open, High, Low, Close, Volume, Vol/50MA):\n"
    "{ohlcv_table}"
)


class OpenRouterVisionClient:
    """One blind, grounded chart-image second opinion; never a trade action."""

    def __init__(
        self,
        *,
        api_key: str,
        api_url: str = "https://openrouter.ai/api/v1/chat/completions",
        model: str = "google/gemini-3.7-flash",
        reasoning_effort: str = "high",
        prompt_version: str = "vcp_visual_validator_v1",
        schema_version: str = "vcp_visual_validator_result_v1",
        app_title: str = "SwingTraderVCP",
        http_referer: str = "",
        timeout_seconds: float = 60.0,
        max_tokens: int = 16384,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_url = api_url
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._prompt_version = prompt_version
        self._schema_version = schema_version
        self._app_title = app_title
        self._http_referer = http_referer
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens
        self._transport = transport
        self._sleep = sleep or (lambda seconds: asyncio.sleep(seconds))

    @property
    def model(self) -> str:
        return self._model

    @property
    def reasoning_effort(self) -> str:
        return self._reasoning_effort

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    @property
    def schema_version(self) -> str:
        return self._schema_version

    @staticmethod
    def _response_schema() -> dict[str, Any]:
        schema = VcpVisionResultV1.model_json_schema()

        def make_strict(node: Any) -> None:
            if isinstance(node, dict):
                node.pop("default", None)
                if node.get("type") == "object" and isinstance(
                    node.get("properties"), dict
                ):
                    node["additionalProperties"] = False
                    node["required"] = list(node["properties"])
                if "const" in node:
                    node["enum"] = [node.pop("const")]
                for value in node.values():
                    make_strict(value)
            elif isinstance(node, list):
                for value in node:
                    make_strict(value)

        make_strict(schema)
        return schema

    def build_request(
        self,
        *,
        frozen: FrozenOhlcv,
        context_png_b64: str,
        detail_png_b64: str,
    ) -> dict[str, Any]:
        user_text = USER_PROMPT_V1.format(
            as_of_date=frozen.as_of_date.isoformat(),
            context_sessions=frozen.context_sessions,
            detail_sessions=frozen.detail_sessions,
            ohlcv_table=compact_ohlcv_table(frozen.candles),
        )
        request: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT_V1},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {
                            "type": "text",
                            "text": (
                                "IMAGE 1 — "
                                f"{frozen.context_sessions}-session context view:"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{context_png_b64}"
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "IMAGE 2 — "
                                f"{frozen.detail_sessions}-session detail view:"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{detail_png_b64}"
                            },
                        },
                    ],
                },
            ],
            "plugins": [{"id": "response-healing"}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "vcp_visual_validator_v1",
                    "strict": True,
                    "schema": self._response_schema(),
                },
            },
            "provider": {"require_parameters": True, "data_collection": "deny"},
            "reasoning": {"effort": self._reasoning_effort, "exclude": True},
            "max_tokens": self._max_tokens,
            "stream": False,
        }
        return request

    async def send_once(
        self,
        request_payload: dict[str, Any],
    ) -> VisionLLMResult:
        if not self._api_key:
            raise VisionLLMError("OPENROUTER_API_KEY is not configured")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Title": self._app_title,
        }
        if self._http_referer:
            headers["HTTP-Referer"] = self._http_referer
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    self._api_url,
                    headers=headers,
                    json=request_payload,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise VisionLLMError(
                f"OpenRouter request outcome is unknown: {type(exc).__name__}",
                attempt_status="transport_unknown",
            ) from exc

        try:
            raw_payload: Any = response.json()
        except ValueError:
            raw_payload = {"unparsed_body": response.text[:2000]}
        safe_payload = sanitize_provider_payload(raw_payload)
        payload = safe_payload if isinstance(safe_payload, dict) else {"response": safe_payload}
        usage = dict(payload.get("usage") or {}) if isinstance(payload.get("usage"), Mapping) else {}
        try:
            cost = float(usage.get("cost", 0) or 0)
        except (TypeError, ValueError):
            cost = 0.0
        request_id = (
            str(payload["id"])
            if payload.get("id") is not None
            else response.headers.get("X-Generation-Id")
        )

        if response.status_code >= 400:
            raise VisionLLMError(
                _format_openrouter_http_error(response, payload),
                response_payload=payload,
                http_status=response.status_code,
                request_id=request_id,
                usage=usage,
                cost=cost,
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        if isinstance(payload.get("error"), Mapping):
            raise VisionLLMError(
                f"OpenRouter returned an embedded provider error: "
                f"{str(payload['error'].get('message') or 'unknown')[:500]}",
                response_payload=payload,
                http_status=response.status_code,
                request_id=request_id,
                usage=usage,
                cost=cost,
            )

        try:
            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
                raise ValueError("OpenRouter response has no usable choices")
            choice_error = choices[0].get("error")
            if isinstance(choice_error, Mapping):
                raise ValueError(
                    f"provider error: {choice_error.get('message') or choice_error.get('code')}"
                )
            parsed = parse_openrouter_structured_content(choices[0], usage=usage)
            result = VcpVisionResultV1.model_validate(parsed)
        except (ValueError, KeyError, TypeError, ValidationError) as exc:
            raise VisionLLMError(
                f"OpenRouter structured response was invalid: {exc}",
                response_payload=payload,
                http_status=response.status_code,
                request_id=request_id,
                usage=usage,
                cost=cost,
                attempt_status="invalid_response",
            ) from exc

        return VisionLLMResult(
            result=result,
            request_id=request_id,
            usage=usage,
            input_hash=canonical_json_hash(
                {
                    "model": self._model,
                    "prompt": self._prompt_version,
                    "schema": self._schema_version,
                    "reasoning": self._reasoning_effort,
                    "request": request_payload,
                }
            ),
            cost=cost,
            request_payload=request_payload,
            response_payload=payload,
        )


def _format_openrouter_http_error(
    response: httpx.Response,
    payload: Mapping[str, Any] | None = None,
) -> str:
    detail: str | None = None
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping) and isinstance(error.get("message"), str):
            detail = error["message"].strip()
        elif isinstance(payload.get("message"), str):
            detail = payload["message"].strip()
    base = f"OpenRouter returned HTTP {response.status_code}"
    return f"{base}: {detail[:500]}" if detail else base


def canonical_json_hash(value: Any) -> str:
    """Deterministic sha256 over the canonical JSON encoding of a value."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Worker task
# ---------------------------------------------------------------------------

def _usage(usage: dict[str, Any]) -> dict[str, int]:
    details = usage.get("completion_tokens_details", {}) if isinstance(usage.get("completion_tokens_details"), dict) else {}
    prompt_details = usage.get("prompt_tokens_details", {}) if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    return {
        "input": int(usage.get("prompt_tokens", 0) or 0),
        "output": int(usage.get("completion_tokens", 0) or 0),
        "reasoning": int(details.get("reasoning_tokens", 0) or 0),
        "cached": int(prompt_details.get("cached_tokens", 0) or 0),
    }


def _add_usage(total: dict[str, int], raw: dict[str, Any]) -> None:
    for key, value in _usage(raw).items():
        total[key] = total.get(key, 0) + value


async def _claim_analysis(analysis_id: UUID) -> dict[str, Any] | None:
    async with async_session() as db:
        result = await db.execute(
            text(
                """
                UPDATE vcp_visual_analyses
                SET status = 'running', error_code = NULL, error_message = NULL
                WHERE id = :analysis_id AND status = 'queued'
                RETURNING id, screening_result_id, chart_source,
                          frozen_ohlcv,
                          context_image, detail_image,
                          context_image_hash, detail_image_hash,
                          source_hash, renderer_version, model,
                          reasoning_effort, max_tokens,
                          prompt_version, schema_version, input_hash
                """
            ),
            {"analysis_id": analysis_id},
        )
        row = result.mappings().one_or_none()
        await db.commit()
    return dict(row) if row else None


async def _start_attempt(
    analysis_id: UUID,
    client: OpenRouterVisionClient,
    request_payload: dict[str, Any],
    input_hash: str,
) -> tuple[UUID, int]:
    async with async_session() as db:
        result = await db.execute(
            text(
                """
                WITH numbered AS (
                    SELECT COALESCE(MAX(attempt_number), 0) + 1 AS attempt_number
                    FROM vcp_visual_attempts
                    WHERE analysis_id = :analysis_id
                )
                INSERT INTO vcp_visual_attempts (
                    analysis_id, attempt_number, status, model,
                    reasoning_effort, prompt_version, response_schema,
                    input_hash, request_payload
                )
                SELECT :analysis_id, attempt_number, 'started', :model,
                       :reasoning, :prompt, :schema, :input_hash,
                       CAST(:request_payload AS jsonb)
                FROM numbered
                RETURNING id, attempt_number
                """
            ),
            {
                "analysis_id": analysis_id,
                "model": client.model,
                "reasoning": client.reasoning_effort,
                "prompt": client.prompt_version,
                "schema": client.schema_version,
                "input_hash": input_hash,
                "request_payload": json.dumps(
                    request_payload, separators=(",", ":")
                ),
            },
        )
        row = result.one()
        await db.commit()
        return row.id, int(row.attempt_number)


async def _finish_attempt(
    attempt_id: UUID,
    *,
    status: str,
    result: VisionLLMResult | None = None,
    error: VisionLLMError | None = None,
) -> None:
    response_payload = result.response_payload if result else (
        error.response_payload if error else None
    )
    usage = result.usage if result else (error.usage if error else {})
    cost = result.cost if result else (error.cost if error else 0.0)
    request_id = result.request_id if result else (error.request_id if error else None)
    http_status = error.http_status if error else 200
    async with async_session() as db:
        await db.execute(
            text(
                """
                UPDATE vcp_visual_attempts
                SET status = :status,
                    response_payload = CAST(:response_payload AS jsonb),
                    http_status = :http_status,
                    request_id = :request_id,
                    usage = CAST(:usage AS jsonb),
                    cost = :cost,
                    error_code = :error_code,
                    error_message = :error_message,
                    completed_at = now()
                WHERE id = :attempt_id
                """
            ),
            {
                "attempt_id": attempt_id,
                "status": status,
                "response_payload": (
                    json.dumps(response_payload, separators=(",", ":"))
                    if response_payload is not None
                    else None
                ),
                "http_status": http_status,
                "request_id": request_id,
                "usage": json.dumps(usage, separators=(",", ":")),
                "cost": cost,
                "error_code": type(error).__name__ if error else None,
                "error_message": str(error)[:500] if error else None,
            },
        )
        await db.commit()


async def _finish_analysis(
    analysis_id: UUID,
    *,
    status: str,
    result_payload: dict[str, Any] | None = None,
    verdict: str | None = None,
    input_hash: str | None = None,
    usage: dict[str, Any] | None = None,
    cost: float = 0.0,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    async with async_session() as db:
        await db.execute(
            text(
                """
                UPDATE vcp_visual_analyses
                SET status = :status,
                    result = COALESCE(CAST(:result AS jsonb), result),
                    ai_verdict = COALESCE(:verdict, ai_verdict),
                    input_hash = COALESCE(:input_hash, input_hash),
                    usage = COALESCE(CAST(:usage AS jsonb), usage),
                    cost = COALESCE(:cost, cost),
                    error_code = :error_code,
                    error_message = :error_message
                WHERE id = :analysis_id
                """
            ),
            {
                "analysis_id": analysis_id,
                "status": status,
                "result": (
                    json.dumps(result_payload, separators=(",", ":"))
                    if result_payload is not None
                    else None
                ),
                "verdict": verdict,
                "input_hash": input_hash,
                "usage": json.dumps(usage or {}, separators=(",", ":")),
                "cost": cost,
                "error_code": error_code,
                "error_message": error_message,
            },
        )
        await db.commit()


def _to_uuid(value: Any) -> UUID:
    """Coerce a UUID or UUID string, tolerating asyncpg uuid.UUID objects."""
    if isinstance(value, UUID):
        return value
    return UUID(value)


async def _run_vcp_vision_analysis_impl(
    ctx: dict[str, Any],
    analysis_id: str,
) -> dict[str, Any]:
    """Run the advisory vision validation for one analysis (arq worker task)."""
    job_try = int(ctx.get("job_try", 1) or 1)
    if job_try > 1:
        # A prior invocation may have died after sending a request. Record the
        # unknown transport state, but do not replay it automatically. Only
        # the explicit human retry endpoint may start another analysis run.
        await _settle_interrupted_analysis(
            UUID(analysis_id),
            status="failed",
            message=(
                "A previous worker invocation was interrupted; explicit retry "
                "is required to avoid replaying an unknown provider request."
            ),
        )
    claimed = await _claim_analysis(UUID(analysis_id))
    if claimed is None:
        return {"status": "skipped", "analysis_id": analysis_id}
    if not settings.vcp_vision_enabled:
        await _finish_analysis(
            UUID(analysis_id),
            status="failed",
            error_code="VisionDisabled",
            error_message="VCP vision validation is disabled by configuration.",
        )
        return {"status": "failed", "analysis_id": analysis_id}

    chart_source = dict(claimed.get("chart_source") or {})
    as_of_date = chart_source.get("as_of_date")
    if as_of_date is None:
        await _finish_analysis(
            UUID(analysis_id),
            status="failed",
            error_code="InvalidChartSource",
            error_message="Chart source has no frozen as_of_date.",
        )
        return {"status": "failed", "analysis_id": analysis_id}

    context_png = claimed.get("context_image")
    detail_png = claimed.get("detail_image")
    if not context_png or not detail_png:
        await _finish_analysis(
            UUID(analysis_id),
            status="failed",
            error_code="MissingImages",
            error_message="Both chart images are required before analysis.",
        )
        return {"status": "failed", "analysis_id": analysis_id}

    context_sessions = int(
        chart_source.get("context_sessions") or settings.vcp_vision_context_sessions
    )
    detail_sessions = int(
        chart_source.get("detail_sessions") or settings.vcp_vision_detail_sessions
    )
    try:
        persisted_ohlcv = claimed.get("frozen_ohlcv")
        if persisted_ohlcv:
            frozen = frozen_ohlcv_from_payload(
                persisted_ohlcv,
                symbol=str(chart_source.get("symbol") or ""),
                as_of_date=dt.date.fromisoformat(as_of_date),
                context_sessions=context_sessions,
                detail_sessions=detail_sessions,
            )
        else:
            # Compatibility only for analyses created before the hardening
            # migration. New analyses always carry their immutable packet.
            frozen = await _reload_frozen_ohlcv(
                _to_uuid(claimed["screening_result_id"]),
                dt.date.fromisoformat(as_of_date),
                claimed["source_hash"],
                context_sessions,
                detail_sessions,
            )
        if frozen.source_hash != claimed["source_hash"]:
            raise ValueError(
                "Persisted frozen OHLCV does not match the analysis source hash."
            )
    except Exception as exc:
        logger.exception("VCP vision could not reload frozen candles for %s", analysis_id)
        await _finish_analysis(
            UUID(analysis_id),
            status="failed",
            error_code=type(exc).__name__,
            error_message=str(exc)[:500],
        )
        return {"status": "failed", "analysis_id": analysis_id}

    context_b64 = base64.b64encode(context_png).decode("ascii")
    detail_b64 = base64.b64encode(detail_png).decode("ascii")

    client = OpenRouterVisionClient(
        api_key=settings.openrouter_api_key,
        api_url=settings.openrouter_api_url,
        model=str(claimed.get("model") or settings.vcp_vision_model),
        reasoning_effort=str(
            claimed.get("reasoning_effort")
            or settings.vcp_vision_reasoning_effort
        ),
        prompt_version=str(
            claimed.get("prompt_version") or settings.vcp_vision_prompt_version
        ),
        schema_version=str(
            claimed.get("schema_version") or settings.vcp_vision_schema_version
        ),
        app_title=settings.openrouter_app_title,
        http_referer=settings.openrouter_http_referer,
        timeout_seconds=settings.openrouter_http_timeout_seconds,
        max_tokens=int(claimed.get("max_tokens") or settings.vcp_vision_max_tokens),
    )
    request_payload = client.build_request(
        frozen=frozen,
        context_png_b64=context_b64,
        detail_png_b64=detail_b64,
    )
    input_hash = canonical_json_hash(
        {
            "source_hash": claimed["source_hash"],
            "renderer_version": claimed["renderer_version"],
            "model": client.model,
            "reasoning_effort": client.reasoning_effort,
            "max_tokens": int(
                claimed.get("max_tokens") or settings.vcp_vision_max_tokens
            ),
            "prompt_version": client.prompt_version,
            "schema_version": client.schema_version,
            "context_image_hash": claimed["context_image_hash"],
            "detail_image_hash": claimed["detail_image_hash"],
        }
    )

    last_error: VisionLLMError | None = None
    total_usage = {"input": 0, "output": 0, "reasoning": 0, "cached": 0}
    total_cost = 0.0
    for call_number in range(1, MAX_RETRYABLE_ATTEMPTS + 1):
        attempt_id, _ = await _start_attempt(
            UUID(analysis_id), client, request_payload, input_hash
        )
        try:
            outcome = await client.send_once(request_payload)
        except VisionLLMError as exc:
            last_error = exc
            _add_usage(total_usage, exc.usage)
            total_cost += exc.cost
            await _finish_attempt(
                attempt_id,
                status=exc.attempt_status,
                error=exc,
            )
            if exc.retryable and call_number == 1:
                await asyncio.sleep(0.5)
                continue
            break
        _add_usage(total_usage, outcome.usage)
        total_cost += outcome.cost
        try:
            cleaned = validate_and_snap_result(
                outcome.result,
                valid_dates=set(frozen.candles_by_date()),
            )
            stored = enrich_stored_result(cleaned, frozen.candles_by_date())
        except VisionSchemaError as exc:
            await _finish_attempt(
                attempt_id,
                status="invalid_response",
                error=VisionLLMError(
                    str(exc),
                    response_payload=outcome.response_payload,
                    http_status=200,
                    request_id=outcome.request_id,
                    usage=outcome.usage,
                    cost=outcome.cost,
                    attempt_status="invalid_response",
                ),
            )
            await _finish_analysis(
                UUID(analysis_id),
                status="failed",
                input_hash=input_hash,
                usage=total_usage,
                cost=total_cost,
                error_code="VisionSchemaError",
                error_message=str(exc)[:500],
            )
            return {"status": "failed", "analysis_id": analysis_id}
        await _finish_attempt(attempt_id, status="succeeded", result=outcome)
        await _finish_analysis(
            UUID(analysis_id),
            status="succeeded",
            result_payload=stored,
            verdict=stored["verdict"],
            input_hash=input_hash,
            usage=total_usage,
            cost=total_cost,
        )
        return {"status": "succeeded", "analysis_id": analysis_id}

    await _finish_analysis(
        UUID(analysis_id),
        status="failed",
        input_hash=input_hash,
        usage=total_usage,
        cost=total_cost,
        error_code=type(last_error).__name__ if last_error else "VisionLLMError",
        error_message=str(last_error)[:500] if last_error else "Vision analysis failed.",
    )
    return {"status": "failed", "analysis_id": analysis_id}


async def _settle_interrupted_analysis(
    analysis_id: UUID,
    *,
    status: str,
    message: str,
) -> None:
    """Close ambiguous attempts and prevent an analysis remaining running."""
    async with async_session() as db:
        await db.execute(
            text(
                """
                UPDATE vcp_visual_attempts
                SET status = 'transport_unknown',
                    error_code = 'WorkerInterrupted',
                    error_message = :message,
                    completed_at = now()
                WHERE analysis_id = :analysis_id AND status = 'started'
                """
            ),
            {"analysis_id": analysis_id, "message": message[:500]},
        )
        await db.execute(
            text(
                """
                UPDATE vcp_visual_analyses
                SET status = :status,
                    error_code = 'WorkerInterrupted',
                    error_message = :message
                WHERE id = :analysis_id
                  AND status IN ('queued', 'running')
                """
            ),
            {
                "analysis_id": analysis_id,
                "status": status,
                "message": message[:500],
            },
        )
        await db.commit()


async def run_vcp_vision_analysis(
    ctx: dict[str, Any],
    analysis_id: str,
) -> dict[str, Any]:
    """Run VCP vision safely without leaving interrupted jobs stalled."""
    analysis_uuid = UUID(analysis_id)
    try:
        return await _run_vcp_vision_analysis_impl(ctx, analysis_id)
    except asyncio.CancelledError:
        # arq may retry cancelled jobs, but an interrupted provider request is
        # transport-unknown and must not be replayed automatically.
        await _settle_interrupted_analysis(
            analysis_uuid,
            status="failed",
            message="The worker was cancelled before the analysis completed.",
        )
        raise
    except Exception as exc:
        logger.exception("Unexpected VCP vision worker failure for %s", analysis_id)
        try:
            await _settle_interrupted_analysis(
                analysis_uuid,
                status="failed",
                message=str(exc) or type(exc).__name__,
            )
        except Exception:
            logger.exception(
                "VCP vision could not persist worker failure for %s", analysis_id
            )
        return {"status": "failed", "analysis_id": analysis_id}


async def _reload_frozen_ohlcv(
    screening_result_id: UUID,
    as_of_date: dt.date,
    expected_source_hash: str,
    context_sessions: int,
    detail_sessions: int,
) -> FrozenOhlcv:
    """Rebuild the frozen packet from the result's canonical source."""
    async with async_session() as db:
        row = (
            await db.execute(
                text(
                    """
                    SELECT i.symbol AS symbol, i.id AS instrument_id
                    FROM screening_results s
                    JOIN instruments i ON i.id = s.instrument_id
                    WHERE s.id = :screening_result_id
                    """
                ),
                {"screening_result_id": screening_result_id},
            )
        ).mappings().one_or_none()
        if row is None:
            raise ValueError("Screening result no longer exists.")
        frozen = await freeze_result_ohlcv(
            db,
            instrument_id=row["instrument_id"],
            as_of_date=as_of_date,
            context_sessions=context_sessions,
            detail_sessions=detail_sessions,
            symbol=row["symbol"],
        )
    if frozen.source_hash != expected_source_hash:
        raise ValueError(
            "Frozen candle source hash changed since capture; refusing to "
            "analyze non-reproducible input."
        )
    return frozen
