"""Proposal Generator Service.

Coordinates headless chart rendering, OpenRouter serial Gemini pattern inference,
and deterministic Python validation into immutable trade proposals.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Collection, Mapping, Sequence
from zoneinfo import ZoneInfo

import httpx

from app.config import settings
from app.domain.p10_geometry import (
    CandleData,
    compute_atr14,
    construct_and_validate_proposal,
    DEFAULT_TICK_SIZE,
)
from app.domain.p10_sizing import TEMPLATE_CONFIG
from app.schemas.proposals import GeminiVcpProposalOutput
from app.services.canonical_ohlcv import compact_ohlcv_table
from app.services.openrouter_content import parse_openrouter_structured_content
from app.services.proposal_renderer import RenderedProposalCharts


logger = logging.getLogger(__name__)
IST_TZ = ZoneInfo("Asia/Kolkata")
PROMPT_VERSION = "p10_vcp_proposal_v4"
SCHEMA_VERSION = "gemini_vcp_proposal_output_v3"
GEOMETRY_VERSION = "p10_geometry_three_windows_v2"


GEMINI_PROPOSAL_SYSTEM_PROMPT = """You are a chart-pattern reader specializing in Mark Minervini's Volatility Contraction Pattern (VCP).

Analyze the provided standardized 252-session context chart, 126-session detail chart, and canonical frozen OHLCV table.
Your task is to identify whether a high-conviction Volatility Contraction Pattern (VCP) is present and ready for an imminent breakout.

Requirements:
1. Verdict: 'valid' only if there is a clear prior uptrend, sequential contracting waves (2 to 4 contractions), volume dry-up near the pivot, and overhead resistance room. Otherwise 'invalid' or 'uncertain'.
2. Contradicts Scanner: true if your chart read contradicts a constructive VCP breakout thesis.
3. The OHLCV table is authoritative for exact dated prices. Every cited date must appear in it and on the detail chart. For an anchor, use the exact candle field: contraction_low.price is that date's daily Low; contraction_high.price and resistance.price are that date's daily High. Do not estimate anchor prices from pixels or use Close for an anchor. Identify the exact pivot breakout price and exactly 3 strictly increasing, tick-aligned technical targets t1 < t2 < t3. Those targets are successive upside measured-move / prior-swing / overhead-room objectives in the prior-uptrend direction. Do not set t1 at the pivot, the breakout tick, or the first nearby resistance just above the base high. t1 must be a full last-contraction measured move or a prior major swing high with clear overhead room; t2 and t3 must be further expansions of that same upside structure. All prices must align to the instrument tick shown in the request.
4. Entry Template: Choose the appropriate entry template based on pattern tightness and conviction:
   - 'single' (Tightest bases with maximum conviction, single leg entry)
   - 'two_leg' (Standard VCP base, 2-leg entry)
   - 'three_leg_front' (Front-loaded 3-leg entry for large liquid setups)
   - 'three_leg_balanced' (Balanced 3-leg entry for wider contractions)
5. Strict JSON output adhering exactly to the provided schema with NO additional properties.
6. Your job ends at pattern evidence and an entry idea. Do NOT calculate or suggest stops, quantities, capital, monetary risk, position or sector exposure, daily-loss limits, add sizes, target sizes, or trailing rules.
"""


@dataclass(frozen=True)
class ProposalBuildResult:
    """Deterministic proposal construction outcome, including safe diagnostics."""

    proposal: dict[str, Any] | None
    rejection_code: str | None = None
    rejection_message: str | None = None

    @property
    def accepted(self) -> bool:
        return self.proposal is not None


def _rejected(code: str, message: str) -> ProposalBuildResult:
    return ProposalBuildResult(
        proposal=None,
        rejection_code=code,
        rejection_message=message,
    )


def proposal_prompt_hash(*, symbol: str, tick_size: Decimal) -> str:
    user_text = (
        f"Evaluate the VCP pattern for {symbol}. Instrument tick size is "
        f"{tick_size}. Return only the strict structured opinion."
    )
    return hashlib.sha256(
        json.dumps(
            {
                "prompt_version": PROMPT_VERSION,
                "system": GEMINI_PROPOSAL_SYSTEM_PROMPT,
                "user_text": user_text,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def compute_frozen_source_hash(candles: Sequence[CandleData]) -> str:
    """Canonical OHLCV hash shared by audit attempts and locked proposals."""
    return hashlib.sha256(
        json.dumps(
            [candle.__dict__ for candle in candles],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def compute_proposal_hash(
    locked_plan: Mapping[str, Any],
) -> str:
    """Hash every field accepted by approval, not just price geometry."""

    def json_default(value: Any) -> str:
        if isinstance(value, (Decimal, dt.date, dt.datetime)):
            return value.isoformat() if hasattr(value, "isoformat") else str(value)
        raise TypeError(f"Unsupported proposal hash value: {type(value).__name__}")

    encoded = json.dumps(
        locked_plan,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def calculate_next_session_and_deadline(
    as_of_date: dt.date,
    *,
    holidays: Collection[dt.date] = (),
) -> tuple[dt.date, dt.datetime]:
    """Given EOD session D0, returns (D1 session date, 09:00 IST approval deadline on D1).
    Skips weekends (Saturday, Sunday).
    """
    next_date = as_of_date + dt.timedelta(days=1)
    holiday_set = set(holidays)
    while next_date.weekday() >= 5 or next_date in holiday_set:  # 5=Sat, 6=Sun
        next_date += dt.timedelta(days=1)

    # 09:00 IST on D1
    deadline_dt = dt.datetime(
        next_date.year, next_date.month, next_date.day, 9, 0, 0, tzinfo=IST_TZ
    )
    return next_date, deadline_dt


def build_proposal_vision_request(
    *,
    symbol: str,
    context_png_b64: str,
    detail_png_b64: str,
    candles: Sequence[CandleData],
    model: str,
    tick_size: Decimal,
) -> dict[str, Any]:
    """Build the fully grounded, auditable P10 multimodal request."""
    user_text = (
        f"Evaluate the VCP pattern for {symbol}. Instrument tick size is "
        f"{tick_size}. The charts cover a 252-session context and 126-session "
        "detail view. The canonical frozen OHLCV table below is authoritative "
        "for exact dates and daily high/low anchor prices. Return only the "
        "strict structured opinion.\n\n"
        "Canonical frozen OHLCV (Date,O,H,L,C,Vol,Vol/50MA):\n"
        f"{compact_ohlcv_table(candles)}"
    )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": GEMINI_PROPOSAL_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "text", "text": "IMAGE 1 — 252-session context view:"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{context_png_b64}"},
                    },
                    {"type": "text", "text": "IMAGE 2 — 126-session detail view:"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{detail_png_b64}"},
                    },
                ],
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "gemini_vcp_proposal_output",
                "strict": True,
                "schema": GeminiVcpProposalOutput.model_json_schema(),
            },
        },
        "provider": {"require_parameters": True, "data_collection": "deny"},
        "reasoning": {
            "effort": settings.vcp_vision_reasoning_effort,
            "exclude": True,
        },
        "max_tokens": settings.vcp_vision_max_tokens,
        "stream": False,
    }


async def call_gemini_vision_for_proposal(
    symbol: str,
    context_png: bytes,
    detail_png: bytes,
    candles: Sequence[CandleData],
    model: str | None = None,
    tick_size: Decimal = DEFAULT_TICK_SIZE,
) -> tuple[GeminiVcpProposalOutput, dict[str, Any], float, str | None]:
    """Call OpenRouter with two charts and the immutable canonical OHLCV packet."""
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

    selected_model = model or settings.vcp_vision_model
    context_b64 = base64.b64encode(context_png).decode("ascii")
    detail_b64 = base64.b64encode(detail_png).decode("ascii")
    request_body = build_proposal_vision_request(
        symbol=symbol,
        context_png_b64=context_b64,
        detail_png_b64=detail_b64,
        candles=candles,
        model=selected_model,
        tick_size=tick_size,
    )

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "X-Title": settings.openrouter_app_title,
    }
    if settings.openrouter_http_referer:
        headers["HTTP-Referer"] = settings.openrouter_http_referer

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            settings.openrouter_api_url,
            headers=headers,
            json=request_body,
        )
        resp.raise_for_status()
        data = resp.json()

    return parse_proposal_openrouter_response(data)


def parse_proposal_openrouter_response(
    data: Mapping[str, Any],
) -> tuple[GeminiVcpProposalOutput, dict[str, Any], float, str | None]:
    """Parse a completed OpenRouter chat payload into the locked Gemini schema.

    The helper expects the full choice object, matching VCP vision / P7 / journal.
    Passing message content alone raises because that string has no ``message`` key.
    """
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise RuntimeError("OpenRouter returned no proposal choice")
    raw_usage = data.get("usage", {})
    usage = dict(raw_usage) if isinstance(raw_usage, Mapping) else {}
    parsed_json = parse_openrouter_structured_content(choices[0], usage=usage)
    output = GeminiVcpProposalOutput.model_validate(parsed_json)
    cost = float(usage.get("total_cost", usage.get("cost", 0.0)) or 0.0)
    request_id = str(data["id"]) if data.get("id") is not None else None
    return output, usage, cost, request_id


def generate_trade_proposal_from_analysis(
    symbol: str,
    as_of_date: dt.date,
    screening_result_id: str,
    instrument_id: str,
    candles: Sequence[CandleData],
    ai_output: GeminiVcpProposalOutput,
    rendered_charts: RenderedProposalCharts,
    model: str,
    tick_size: Decimal = DEFAULT_TICK_SIZE,
    risk_policy_id: str | None = None,
    risk_policy_version: int = 1,
    risk_per_trade_pct: Decimal = Decimal("0.0100"),
    approved_risk_budget_amount: Decimal | None = None,
    holidays: Collection[dt.date] = (),
    generated_at: dt.datetime | None = None,
) -> ProposalBuildResult:
    """Combines AI opinion and candles through deterministic Python validation into an immutable proposal dict."""
    if ai_output.verdict != "valid" or ai_output.contradicts_scanner:
        logger.info(f"Symbol {symbol} rejected by AI verdict ({ai_output.verdict}) or contradiction ({ai_output.contradicts_scanner})")
        if ai_output.contradicts_scanner:
            return _rejected(
                "proposal_ai_contradicts_scanner",
                "Gemini marked the pattern as contradicting the scanner thesis.",
            )
        if ai_output.verdict == "uncertain":
            return _rejected(
                "proposal_ai_uncertain",
                "Gemini returned an uncertain pattern verdict.",
            )
        return _rejected(
            "proposal_ai_invalid",
            f"Gemini returned verdict={ai_output.verdict!r}.",
        )

    if len(candles) < 252:
        logger.info("Symbol %s has only %s frozen sessions; 252 required", symbol, len(candles))
        return _rejected(
            "proposal_insufficient_candles",
            f"Frozen input has {len(candles)} sessions; 252 are required.",
        )

    atr14 = compute_atr14(candles)
    frozen_dates: dict[dt.date, CandleData] = {}
    for candle in candles[-126:]:
        if candle.date is None:
            continue
        try:
            frozen_dates[dt.date.fromisoformat(candle.date)] = candle
        except ValueError:
            logger.info("Symbol %s has an invalid frozen candle date %r", symbol, candle.date)
            return _rejected(
                "proposal_invalid_candle_date",
                f"Frozen candle has invalid date {candle.date!r}.",
            )

    tolerance = atr14 * Decimal("0.50")
    validated_anchors: list[dict[str, Any]] = []
    for anchor in ai_output.contraction_anchors:
        candle = frozen_dates.get(anchor.date)
        if candle is None:
            logger.info("Symbol %s returned an anchor outside the frozen detail window", symbol)
            return _rejected(
                "proposal_anchor_date_missing",
                f"{anchor.anchor_type} anchor date {anchor.date.isoformat()} is outside the frozen 126-session detail window.",
            )
        reference = (
            Decimal(str(candle.low))
            if anchor.anchor_type == "contraction_low"
            else Decimal(str(candle.high))
        )
        if abs(anchor.price - reference) > tolerance:
            return _rejected(
                "proposal_anchor_price_out_of_tolerance",
                f"{anchor.anchor_type} anchor on {anchor.date.isoformat()} supplied {anchor.price}; expected daily {'low' if anchor.anchor_type == 'contraction_low' else 'high'} {reference}; tolerance {tolerance} (0.5×ATR14).",
            )
        validated_anchors.append(
            {
                "date": anchor.date.isoformat(),
                "price": str(reference),
                "anchor_type": anchor.anchor_type,
            }
        )

    low_anchors = [
        anchor for anchor in ai_output.contraction_anchors
        if anchor.anchor_type == "contraction_low"
    ]
    final_low_anchor = max(low_anchors, key=lambda anchor: anchor.date)
    final_low_candle = frozen_dates[final_low_anchor.date]
    final_contraction_low = Decimal(str(final_low_candle.low))

    resistance_anchors = [
        anchor for anchor in ai_output.contraction_anchors
        if anchor.anchor_type in {"resistance", "contraction_high"}
    ]
    latest_resistance = max(resistance_anchors, key=lambda anchor: anchor.date)
    latest_resistance_candle = frozen_dates[latest_resistance.date]
    latest_resistance_price = Decimal(str(latest_resistance_candle.high))
    if abs(ai_output.pivot_price - latest_resistance_price) > tolerance:
        return _rejected(
            "proposal_pivot_not_anchored",
            f"Pivot {ai_output.pivot_price} is not within tolerance {tolerance} of latest resistance high {latest_resistance_price} on {latest_resistance.date.isoformat()}.",
        )

    geom = construct_and_validate_proposal(
        pivot_price=ai_output.pivot_price,
        final_contraction_low=final_contraction_low,
        t1=ai_output.t1,
        t2=ai_output.t2,
        t3=ai_output.t3,
        atr14=atr14,
        tick_size=tick_size,
    )

    if not geom.is_valid:
        logger.info(f"Symbol {symbol} proposal validation failed: {geom.rejection_reason}")
        return _rejected(
            "proposal_geometry_invalid",
            geom.rejection_reason or "Deterministic proposal geometry validation failed.",
        )

    entry_session, approval_deadline = calculate_next_session_and_deadline(
        as_of_date,
        holidays=holidays,
    )
    tmpl = ai_output.entry_template
    tmpl_info = TEMPLATE_CONFIG[tmpl]
    if approved_risk_budget_amount is None or approved_risk_budget_amount <= 0:
        logger.info("Symbol %s has no operator-configured monetary risk budget", symbol)
        return _rejected(
            "proposal_risk_budget_missing",
            "No operator-configured monetary risk budget is available for this proposal.",
        )

    source_hash = compute_frozen_source_hash(candles)

    completed_at = generated_at or dt.datetime.now(dt.timezone.utc)
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=dt.timezone.utc)
    live_cutoff = dt.datetime.combine(
        entry_session,
        dt.time(8, 30),
        tzinfo=IST_TZ,
    )
    live_eligible = completed_at.astimezone(IST_TZ) <= live_cutoff

    locked_plan: dict[str, Any] = {
        "screening_result_id": screening_result_id,
        "instrument_id": instrument_id,
        "symbol": symbol,
        "as_of_date": as_of_date,
        "status": "pending_approval",
        "approval_deadline": approval_deadline,
        "entry_session_date": entry_session,
        "source_hash": source_hash,
        "renderer_version": rendered_charts.renderer_version,
        "geometry_version": GEOMETRY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "model": model,
        "confidence": Decimal(str(round(ai_output.confidence, 4))),
        "entry_template": tmpl.value,
        "pivot_price": geom.pivot_price,
        "initial_stop": geom.initial_stop,
        "stop_distance_pct": geom.stop_distance_pct,
        "chase_ceiling": geom.chase_ceiling,
        "t1": geom.t1,
        "t2": geom.t2,
        "t3": geom.t3,
        "risk_policy_id": risk_policy_id,
        "risk_policy_version": risk_policy_version,
        "risk_budget_pct": risk_per_trade_pct * Decimal("100"),
        "approved_risk_budget_amount": approved_risk_budget_amount,
        "leg_count": tmpl_info["leg_count"],
        "leg_risk_allocations": [float(x) for x in tmpl_info["leg_allocations"]],
        "relative_volume_threshold": tmpl_info["relative_volume_threshold"],
        "gemini_evidence": {
            "base_tightness": ai_output.base_tightness,
            "dry_up_quality": ai_output.dry_up_quality,
            "resistance_room": ai_output.resistance_room,
            "evidence_summary": ai_output.evidence_summary,
            "contraction_anchors": validated_anchors,
        },
        "geometry": {
            "atr14": str(geom.atr14),
            "pivot_r_distance": str(geom.r_distance),
            "worst_entry_r_distance": str(geom.chase_ceiling - geom.initial_stop),
            "final_contraction_low": str(final_contraction_low),
            "anchor_merge_tolerance": str(tolerance),
            "tick_size": str(tick_size),
        },
        "context_image_hash": rendered_charts.context_hash,
        "detail_image_hash": rendered_charts.detail_hash,
        "live_eligible": live_eligible,
        "generated_at": completed_at,
    }
    locked_plan["proposal_hash"] = compute_proposal_hash(locked_plan)
    return ProposalBuildResult(proposal=locked_plan)
