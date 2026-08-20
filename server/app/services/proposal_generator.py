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
from pydantic import ValidationError

from app.config import settings
from app.domain.p10_geometry import (
    CandleData,
    compute_atr14,
    construct_and_validate_proposal,
    DEFAULT_TICK_SIZE,
    derive_chart_geometry,
    ground_pivot_to_resistance_zones,
    MAX_STOP_DISTANCE_PCT,
    PivotResistanceGrounding,
    ProposalGeometry,
    ResistanceZone,
    ValidatedPatternAnchor,
)
from app.domain.p10_sizing import EntryTemplate, TEMPLATE_CONFIG
from app.schemas.proposals import GeminiVcpProposalOutput
from app.services.canonical_ohlcv import compact_ohlcv_table
from app.services.fundamental_llm import sanitize_provider_payload
from app.services.openrouter_content import (
    decode_openrouter_json_value,
    parse_openrouter_structured_content,
)
from app.services.openrouter_schema import gemini_compatible_json_schema
from app.services.proposal_renderer import RenderedProposalCharts


logger = logging.getLogger(__name__)
IST_TZ = ZoneInfo("Asia/Kolkata")
PROMPT_VERSION = "p10_vcp_proposal_v4"
SCHEMA_VERSION = "gemini_vcp_proposal_output_v4"
GEOMETRY_VERSION = "p10_geometry_rr_adjusted_chase_v4"
PROPOSAL_INVALID_PROVIDER_JSON = "proposal_invalid_provider_json"
_PROVIDER_PAYLOAD_SNIPPET_LIMIT = 4000


class ProposalProviderError(RuntimeError):
    """OpenRouter returned a payload that is not usable proposal JSON."""

    error_type = PROPOSAL_INVALID_PROVIDER_JSON

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = details or {}


GEMINI_PROPOSAL_SYSTEM_PROMPT = """You are a chart-pattern reader specializing in Mark Minervini's Volatility Contraction Pattern (VCP).

Analyze the provided standardized 252-session context chart, 126-session detail chart, and canonical frozen OHLCV table.
Your task is to identify whether a high-conviction Volatility Contraction Pattern (VCP) is present and ready for an imminent breakout.

Requirements:
1. Verdict: 'valid' only if there is a clear prior uptrend, sequential contracting waves (2 to 4 contractions), volume dry-up near the pivot, and overhead resistance room. Otherwise 'invalid' or 'uncertain'.
2. Contradicts Scanner: true if your chart read contradicts a constructive VCP breakout thesis.
3. The OHLCV table is authoritative for exact dated prices. Every cited date must appear in it and on the detail chart. For an anchor, use the exact candle field: contraction_low.price is that date's daily Low; contraction_high.price and resistance.price are that date's daily High. Do not estimate anchor prices from pixels or use Close for an anchor. Identify the exact pivot breakout price and exactly 3 strictly increasing, tick-aligned technical targets t1 < t2 < t3. Those targets are successive upside measured-move / prior-swing / overhead-room objectives in the prior-uptrend direction. Do not set t1 at the pivot, the breakout tick, or the first nearby resistance just above the base high. t1 must be a full last-contraction measured move or a prior major swing high with clear overhead room; t2 and t3 must be further expansions of that same upside structure. If there are 3 contractions (C1, C2, C3) and C3 forms a tight micro-consolidation, you may set pivot_price to the clearance of C3 high (the cheat pivot) with two_leg_staged template. All prices must align to the instrument tick shown in the request.
4. Entry Template: Choose the appropriate entry template based on pattern tightness and conviction:
   - 'single' (Tightest bases with maximum conviction, single leg entry)
   - 'two_leg_staged' (Staged entry: 50% on C3 micro/cheat pivot clearance, 50% on Base breakout)
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
    rejection_details: dict[str, Any] | None = None

    @property
    def accepted(self) -> bool:
        return self.proposal is not None


def _rejected(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> ProposalBuildResult:
    return ProposalBuildResult(
        proposal=None,
        rejection_code=code,
        rejection_message=message,
        rejection_details=details,
    )


def _serialize_resistance_anchor(
    anchor: ValidatedPatternAnchor,
    *,
    older_boundary_dates: set[dt.date],
) -> dict[str, str]:
    return {
        "date": anchor.date.isoformat(),
        "price": str(anchor.price),
        "anchor_type": anchor.anchor_type,
        "eligibility": (
            "supported_older_base_boundary"
            if anchor.date in older_boundary_dates
            else "recent_60_sessions"
        ),
    }


def _serialize_resistance_zone(
    zone: ResistanceZone,
    *,
    older_boundary_dates: set[dt.date],
) -> dict[str, Any]:
    return {
        "low": str(zone.low),
        "high": str(zone.high),
        "median": str(zone.median),
        "most_recent_date": zone.most_recent_date.isoformat(),
        "members": [
            _serialize_resistance_anchor(
                member,
                older_boundary_dates=older_boundary_dates,
            )
            for member in zone.members
        ],
    }


def _serialize_pivot_grounding(
    grounding: PivotResistanceGrounding,
) -> dict[str, Any]:
    older_boundary_dates = set(grounding.older_boundary_dates)
    return {
        "rule_version": GEOMETRY_VERSION,
        "frozen_atr14": str(grounding.frozen_atr14),
        "zone_width_and_pivot_tolerance": str(grounding.tolerance),
        "recent_session_count": 60,
        "recent_start_date": grounding.recent_start_date.isoformat(),
        "older_boundary_rule": (
            "explicit resistance in detail window; at least two strictly later "
            "contraction lows; strictly later recent high retest within 0.5xATR14"
        ),
        "eligible_anchors": [
            _serialize_resistance_anchor(
                anchor,
                older_boundary_dates=older_boundary_dates,
            )
            for anchor in grounding.eligible_anchors
        ],
        "older_boundary_dates": [
            boundary_date.isoformat()
            for boundary_date in grounding.older_boundary_dates
        ],
        "zones": [
            _serialize_resistance_zone(
                zone,
                older_boundary_dates=older_boundary_dates,
            )
            for zone in grounding.zones
        ],
        "selected_zone": (
            _serialize_resistance_zone(
                grounding.selected_zone,
                older_boundary_dates=older_boundary_dates,
            )
            if grounding.selected_zone is not None
            else None
        ),
        "higher_zones": [
            _serialize_resistance_zone(
                zone,
                older_boundary_dates=older_boundary_dates,
            )
            for zone in grounding.higher_zones
        ],
        "boundary_distance": (
            str(grounding.boundary_distance)
            if grounding.boundary_distance is not None
            else None
        ),
        "next_higher_zone_distance": {
            "price": (
                str(grounding.next_higher_distance)
                if grounding.next_higher_distance is not None
                else None
            ),
            "atr": (
                str(grounding.next_higher_distance_atr)
                if grounding.next_higher_distance_atr is not None
                else None
            ),
            "percent": (
                str(grounding.next_higher_distance_pct)
                if grounding.next_higher_distance_pct is not None
                else None
            ),
        },
        "audit_flags": list(grounding.audit_flags),
        "is_grounded": grounding.is_grounded,
        "subreason": grounding.subreason,
    }


def _decimal_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _serialize_rr_audit(geom: ProposalGeometry) -> dict[str, Any]:
    return {
        "base_chase_ceiling": _decimal_str(geom.base_chase_ceiling),
        "rr_adjusted_chase_ceiling": _decimal_str(geom.rr_adjusted_chase_ceiling),
        "r_at_pivot": _decimal_str(geom.r_at_pivot),
        "r_at_base_chase_ceiling": _decimal_str(geom.r_at_base_chase_ceiling),
        "final_r_at_chase_ceiling": _decimal_str(geom.final_r_at_chase_ceiling),
        "t1_r": _decimal_str(geom.t1_r),
        "t2_r": _decimal_str(geom.t2_r),
        "t3_r": _decimal_str(geom.t3_r),
        "t2_below_2r": geom.t2_below_2r,
        "t3_below_3r": geom.t3_below_3r,
        "target_r_multiples": (
            {
                "t1": _decimal_str(geom.t1_r),
                "t2": _decimal_str(geom.t2_r),
                "t3": _decimal_str(geom.t3_r),
            }
            if geom.t1_r is not None
            else None
        ),
    }


def _serialize_calculation_basis(
    *,
    geom: ProposalGeometry,
    ai_output: GeminiVcpProposalOutput,
    final_contraction_low: Decimal,
    final_low_anchor: ValidatedPatternAnchor,
    atr14: Decimal,
    tolerance: Decimal,
    pivot_grounding_payload: dict[str, Any],
    tmpl: EntryTemplate,
    tmpl_info: dict[str, Any],
    risk_per_trade_pct: Decimal,
    approved_risk_budget_amount: Decimal | None,
    risk_policy_version: int,
    tick_size: Decimal,
) -> dict[str, Any]:
    chase_tightened = (
        geom.base_chase_ceiling is not None
        and geom.chase_ceiling < geom.base_chase_ceiling
    )
    stop_dist = geom.pivot_price - geom.initial_stop
    chase_margin = geom.chase_ceiling - geom.pivot_price
    chase_pct = (
        round((chase_margin / geom.pivot_price) * Decimal("100"), 2)
        if geom.pivot_price > 0
        else Decimal("0")
    )
    selected_zone = pivot_grounding_payload.get("selected_zone") or {}

    return {
        "pivot": {
            "pivot_price": str(geom.pivot_price),
            "grounding_status": "grounded" if pivot_grounding_payload.get("is_grounded") else "ungrounded",
            "selected_zone_low": selected_zone.get("low"),
            "selected_zone_high": selected_zone.get("high"),
            "selected_zone_median": selected_zone.get("median"),
            "selected_zone_recent_date": selected_zone.get("most_recent_date"),
            "boundary_distance": str(pivot_grounding_payload.get("boundary_distance")),
            "tolerance_atr": str(tolerance),
            "tolerance_rule": "0.50x ATR14 anchor merge tolerance",
            "basis": (
                f"Pivot ₹{geom.pivot_price} grounded to resistance zone "
                f"₹{selected_zone.get('low', '-')}..₹{selected_zone.get('high', '-')} "
                f"within {tolerance} tolerance (0.5xATR14)."
            ),
        },
        "stop_loss": {
            "initial_stop": str(geom.initial_stop),
            "final_contraction_low": str(final_contraction_low),
            "final_contraction_low_date": final_low_anchor.date.isoformat(),
            "atr14": str(atr14),
            "stop_buffer_multiplier": "0.25",
            "stop_buffer_amount": str(atr14 * Decimal("0.25")),
            "stop_distance": str(stop_dist),
            "stop_distance_pct": str(geom.stop_distance_pct),
            "max_allowed_stop_pct": "8.00",
            "formula": f"Final contraction low (₹{final_contraction_low}) - 0.25xATR14 (₹{atr14 * Decimal('0.25'):.2f}), snapped to tick",
            "basis": (
                f"Structural SL set at ₹{geom.initial_stop} (-{geom.stop_distance_pct}% from pivot) "
                f"anchored to contraction low ₹{final_contraction_low} on {final_low_anchor.date.isoformat()} "
                f"with 0.25xATR14 buffer."
            ),
        },
        "entry_chase": {
            "pivot_entry": str(geom.pivot_price),
            "base_chase_ceiling": _decimal_str(geom.base_chase_ceiling),
            "rr_adjusted_chase_ceiling": _decimal_str(geom.rr_adjusted_chase_ceiling),
            "final_chase_ceiling": str(geom.chase_ceiling),
            "ceiling_tightened_for_1r": chase_tightened,
            "max_chase_margin": str(chase_margin),
            "max_chase_pct": str(chase_pct),
            "worst_entry_r_distance": str(geom.chase_ceiling - geom.initial_stop),
            "formula": "min(pivot + min(2% of pivot, 0.5xStopDistance), (T1 + initial_stop) / 2) floored to tick",
            "basis": (
                f"Entry trigger at pivot ₹{geom.pivot_price}. Max allowable chase ceiling ₹{geom.chase_ceiling} "
                f"(+{chase_pct}% / ₹{chase_margin}) "
                + (f"[Tightened from base ceiling ₹{geom.base_chase_ceiling} to guarantee T1 >= 1R]" if chase_tightened else "[Guarantees T1 >= 1R at ceiling]")
            ),
        },
        "targets": {
            "t1": {
                "price": str(geom.t1),
                "r_at_ceiling": _decimal_str(geom.t1_r),
                "r_at_pivot": _decimal_str(geom.r_at_pivot),
                "min_required_r": "1.00",
                "objective": "Primary structural objective / last-contraction measured move",
            },
            "t2": {
                "price": str(geom.t2),
                "r_at_ceiling": _decimal_str(geom.t2_r),
                "below_2r_flag": geom.t2_below_2r,
                "objective": "Secondary structural expansion objective",
            },
            "t3": {
                "price": str(geom.t3),
                "r_at_ceiling": _decimal_str(geom.t3_r),
                "below_3r_flag": geom.t3_below_3r,
                "objective": "Major trend swing objective / runner target",
            },
            "basis": (
                f"T1=₹{geom.t1} ({geom.t1_r}R at ceiling, {geom.r_at_pivot}R at pivot), "
                f"T2=₹{geom.t2} ({geom.t2_r}R), T3=₹{geom.t3} ({geom.t3_r}R) strictly increasing."
            ),
        },
        "sizing_and_risk": {
            "entry_template": tmpl.value,
            "leg_count": tmpl_info["leg_count"],
            "leg_risk_allocations": [float(x) for x in tmpl_info["leg_allocations"]],
            "relative_volume_threshold": float(tmpl_info["relative_volume_threshold"]),
            "risk_per_trade_pct": str(risk_per_trade_pct * Decimal("100")),
            "approved_risk_budget_amount": str(approved_risk_budget_amount) if approved_risk_budget_amount is not None else None,
            "risk_policy_version": risk_policy_version,
            "base_tightness": ai_output.base_tightness,
            "dry_up_quality": ai_output.dry_up_quality,
            "resistance_room": ai_output.resistance_room,
            "basis": (
                f"{tmpl.value.upper()} template: {tmpl_info['leg_count']} leg(s) "
                f"with allocations {[float(x)*100 for x in tmpl_info['leg_allocations']]}% "
                f"requiring RVOL >= {tmpl_info['relative_volume_threshold']}x on breakout. "
                f"Max approved risk budget ₹{approved_risk_budget_amount or 0}."
            ),
        },
    }


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
                "schema": gemini_compatible_json_schema(
                    GeminiVcpProposalOutput.model_json_schema()
                ),
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
        try:
            data = resp.json()
        except ValueError:
            data = {"unparsed_body": resp.text[:2000]}

    return parse_proposal_openrouter_response(data)


def _provider_payload_snippet(value: Any) -> Any:
    sanitized = sanitize_provider_payload(value)
    encoded = json.dumps(sanitized, default=str)
    if len(encoded) <= _PROVIDER_PAYLOAD_SNIPPET_LIMIT:
        return sanitized
    return {"truncated": True, "preview": encoded[:_PROVIDER_PAYLOAD_SNIPPET_LIMIT]}


def _unwrap_json_payload(data: Any) -> Any:
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    if not isinstance(data, str):
        return data
    try:
        return decode_openrouter_json_value(data)
    except json.JSONDecodeError as exc:
        raise ProposalProviderError(
            f"OpenRouter proposal payload is not valid JSON: {exc}",
            details={"payload_type": "str"},
        ) from exc


def _extract_proposal_json(
    data: Mapping[str, Any],
    *,
    usage: Mapping[str, Any],
) -> dict[str, Any]:
    if "verdict" in data and "choices" not in data:
        return dict(data)

    choices: Any = data.get("choices")
    if isinstance(choices, str):
        choices = _unwrap_json_payload(choices)
    if isinstance(choices, Mapping):
        choices = [choices]
    if not isinstance(choices, list) or not choices:
        raise ValueError("OpenRouter returned no proposal choice")

    choice: Any = choices[0]
    if isinstance(choice, str):
        choice = _unwrap_json_payload(choice)
    if isinstance(choice, Mapping) and "verdict" in choice and "message" not in choice:
        return dict(choice)
    if not isinstance(choice, Mapping):
        raise ValueError("OpenRouter returned no proposal choice")
    return parse_openrouter_structured_content(choice, usage=usage)


def parse_proposal_openrouter_response(
    data: Any,
) -> tuple[GeminiVcpProposalOutput, dict[str, Any], float, str | None]:
    """Parse a completed OpenRouter chat payload into the locked Gemini schema.

    Accepts a normal chat-completions object, a JSON string envelope, an
    unwrapped structured proposal object, or a choice whose content is the
    proposal JSON itself.
    """
    original = data
    try:
        data = _unwrap_json_payload(data)
        if not isinstance(data, Mapping):
            raise ValueError(
                f"OpenRouter proposal payload is {type(data).__name__}, not an object"
            )
        raw_usage = data.get("usage", {})
        usage = dict(raw_usage) if isinstance(raw_usage, Mapping) else {}
        parsed_json = _extract_proposal_json(data, usage=usage)
        output = GeminiVcpProposalOutput.model_validate(parsed_json)
        cost = float(usage.get("total_cost", usage.get("cost", 0.0)) or 0.0)
        request_id = str(data["id"]) if data.get("id") is not None else None
        return output, usage, cost, request_id
    except ProposalProviderError:
        raise
    except (
        ValueError,
        TypeError,
        KeyError,
        ValidationError,
        json.JSONDecodeError,
        AttributeError,
    ) as exc:
        raise ProposalProviderError(
            f"OpenRouter structured response was invalid: {exc}",
            details={
                "payload_type": type(original).__name__,
                "payload": _provider_payload_snippet(original),
            },
        ) from exc


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
    validated_pattern_anchors: list[ValidatedPatternAnchor] = []
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
        validated_pattern_anchors.append(
            ValidatedPatternAnchor(
                date=anchor.date,
                price=reference,
                anchor_type=anchor.anchor_type,
            )
        )

    low_anchors = [
        anchor for anchor in validated_pattern_anchors
        if anchor.anchor_type == "contraction_low"
    ]
    final_low_anchor = max(low_anchors, key=lambda anchor: anchor.date)
    final_low_candle = frozen_dates[final_low_anchor.date]
    final_contraction_low = Decimal(str(final_low_candle.low))

    pivot_grounding = ground_pivot_to_resistance_zones(
        pivot=ai_output.pivot_price,
        anchors=validated_pattern_anchors,
        session_dates=tuple(sorted(frozen_dates)),
        frozen_atr14=atr14,
    )
    pivot_grounding_payload = _serialize_pivot_grounding(pivot_grounding)
    if not pivot_grounding.is_grounded:
        selected_zone = pivot_grounding.selected_zone
        message = (
            "No eligible current-base resistance evidence remains after the "
            "60-session recency and supported-boundary rules."
            if selected_zone is None
            else (
                f"Pivot {ai_output.pivot_price} is {pivot_grounding.boundary_distance} "
                f"from closest resistance zone {selected_zone.low}–{selected_zone.high}; "
                f"maximum tolerance is {pivot_grounding.tolerance} (0.5× frozen ATR14)."
            )
        )
        return _rejected(
            "proposal_pivot_not_anchored",
            message,
            details={
                "subreason": pivot_grounding.subreason,
                "pivot_grounding": pivot_grounding_payload,
            },
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

    if not geom.is_valid and ai_output.entry_template == EntryTemplate.TWO_LEG_STAGED:
        dyn_geom = derive_chart_geometry(candles, tick_size=tick_size)
        if dyn_geom.cheat_pivot and dyn_geom.cheat_stop:
            cheat_geom = construct_and_validate_proposal(
                pivot_price=dyn_geom.cheat_pivot,
                final_contraction_low=dyn_geom.final_contraction_low,
                t1=ai_output.t1,
                t2=ai_output.t2,
                t3=ai_output.t3,
                atr14=atr14,
                tick_size=tick_size,
            )
            if cheat_geom.is_valid:
                geom = cheat_geom
                ai_output = ai_output.model_copy(update={
                    "pivot_price": dyn_geom.cheat_pivot,
                    "entry_template": EntryTemplate.TWO_LEG_STAGED,
                })

    if not geom.is_valid:
        logger.info(f"Symbol {symbol} proposal validation failed: {geom.rejection_reason}")
        chase_ceiling_evaluated = (
            geom.initial_stop > 0
            and geom.stop_distance_pct <= MAX_STOP_DISTANCE_PCT
        )
        worst_entry_r = (
            geom.chase_ceiling - geom.initial_stop
            if chase_ceiling_evaluated
            else None
        )
        rr_audit = _serialize_rr_audit(geom) if chase_ceiling_evaluated else {}
        return _rejected(
            "proposal_geometry_invalid",
            geom.rejection_reason or "Deterministic proposal geometry validation failed.",
            details={
                "subreason": "deterministic_geometry_invalid",
                "geometry_inputs": {
                    "pivot_price": str(ai_output.pivot_price),
                    "final_contraction_low": str(final_contraction_low),
                    "final_contraction_low_date": final_low_anchor.date.isoformat(),
                    "frozen_atr14": str(atr14),
                    "calculated_initial_stop": str(geom.initial_stop),
                    "stop_distance_pct": str(geom.stop_distance_pct),
                    "calculated_chase_ceiling": (
                        str(geom.chase_ceiling)
                        if chase_ceiling_evaluated
                        else None
                    ),
                    "worst_entry_r_distance": (
                        str(worst_entry_r)
                        if worst_entry_r is not None
                        else None
                    ),
                    "t1": str(ai_output.t1),
                    "t2": str(ai_output.t2),
                    "t3": str(ai_output.t3),
                    **rr_audit,
                },
                "pivot_grounding": pivot_grounding_payload,
            },
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

    calc_basis = _serialize_calculation_basis(
        geom=geom,
        ai_output=ai_output,
        final_contraction_low=final_contraction_low,
        final_low_anchor=final_low_anchor,
        atr14=atr14,
        tolerance=tolerance,
        pivot_grounding_payload=pivot_grounding_payload,
        tmpl=tmpl,
        tmpl_info=tmpl_info,
        risk_per_trade_pct=risk_per_trade_pct,
        approved_risk_budget_amount=approved_risk_budget_amount,
        risk_policy_version=risk_policy_version,
        tick_size=tick_size,
    )

    selected_zone = pivot_grounding_payload.get("selected_zone") or {}
    logger.info(
        "[Proposal Built] Symbol %s -> ATR14=%s | Pivot=₹%s (zone: ₹%s..₹%s, dist=%s, tol=%s) | "
        "SL=₹%s (final low ₹%s on %s - 0.25*ATR14=₹%s, risk=%s%%) | "
        "Ceiling=₹%s (base=₹%s, 1R_cap=₹%s) | "
        "Targets: T1=₹%s (%sR), T2=₹%s (%sR), T3=₹%s (%sR) | "
        "Template=%s (%s legs, RVOL>=%sx, max budget=₹%s)",
        symbol,
        atr14,
        geom.pivot_price,
        selected_zone.get("low", "-"),
        selected_zone.get("high", "-"),
        pivot_grounding_payload.get("boundary_distance"),
        tolerance,
        geom.initial_stop,
        final_contraction_low,
        final_low_anchor.date.isoformat(),
        atr14 * Decimal("0.25"),
        geom.stop_distance_pct,
        geom.chase_ceiling,
        geom.base_chase_ceiling,
        geom.rr_adjusted_chase_ceiling,
        geom.t1,
        geom.t1_r,
        geom.t2,
        geom.t2_r,
        geom.t3,
        geom.t3_r,
        tmpl.value,
        tmpl_info["leg_count"],
        tmpl_info["relative_volume_threshold"],
        approved_risk_budget_amount,
    )

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
            "pivot_grounding": pivot_grounding_payload,
            "calculation_basis": calc_basis,
            "tick_size": str(tick_size),
            **_serialize_rr_audit(geom),
        },
        "context_image_hash": rendered_charts.context_hash,
        "detail_image_hash": rendered_charts.detail_hash,
        "live_eligible": live_eligible,
        "generated_at": completed_at,
    }
    locked_plan["proposal_hash"] = compute_proposal_hash(locked_plan)
    return ProposalBuildResult(proposal=locked_plan)
