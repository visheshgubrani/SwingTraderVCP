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
    DeterministicChartGeometry,
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
from app.schemas.proposals import GeminiContractionLeg, GeminiVcpProposalOutput
from app.services.fundamental_llm import sanitize_provider_payload
from app.services.openrouter_content import (
    decode_openrouter_json_value,
    parse_openrouter_structured_content,
)
from app.services.openrouter_schema import gemini_compatible_json_schema
from app.services.proposal_renderer import RenderedProposalCharts


logger = logging.getLogger(__name__)
IST_TZ = ZoneInfo("Asia/Kolkata")
PROMPT_VERSION = "p10_vcp_proposal_v5"
SCHEMA_VERSION = "gemini_vcp_proposal_output_v5"
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


GEMINI_PROPOSAL_SYSTEM_PROMPT = """You are a technical analyst trained in Mark Minervini's Volatility Contraction Pattern (VCP) methodology.

You will be shown two standardized stock-chart images: a 252-session context chart and a 126-session detail chart (log price, volume pane, moving averages). Analyze the images only. Do not invent session dates. Estimate prices from the visible axis and snap every price to the instrument tick given in the request.

Your job is to decide whether the charts show a real VCP setup, not to force a VCP label onto noise.

Check, in order:

1. Prior uptrend / Stage 2 context
   Is the stock in a confirmed uptrend before the base (higher highs and higher lows, ideally above a rising 50-day and 150/200-day MA)? Has it already had a meaningful advance before contracting? Bases with no prior trend are not valid VCPs.

2. Count and structure of contractions
   Identify each pullback (T1, T2, T3, …) by its swing high and swing low. A valid VCP has 2 to 6 contractions. Measure each leg's depth as an approximate percent from peak to trough. Each successive contraction MUST be smaller than the prior one (for example 25%, then 15%, then 8%). The final contraction is usually tight (often under 10%).

3. Volume behavior
   Volume should generally decrease as the pattern progresses, especially into each successive contraction low. Look for volume dry-up near the final contraction/pivot — noticeably quieter than earlier in the base. High-volume breakdowns through support are a red flag (distribution, not healthy contraction). If the volume pane is missing or unreadable, say so and do not guess dry-up.

4. Price-action tightness
   Swings should get narrower into the last 1–3 weeks. The tight, low-volatility area just under resistance is the pivot.

5. Base depth and duration
   Overall base depth should be reasonable (very deep bases, e.g. >35–40% from peak to absolute low, are less reliable). Duration is typically several weeks to several months; 1–2 week wiggles are not mature VCPs.

6. Pivot / breakout level
   The pivot is the resistance formed by the highs of the last contraction. Note whether price is still under it on lighter volume (constructive) or has already broken out.

7. Moving-average alignment
   Later contractions should hold the 50-day MA when it is visible. Prefer Stage 2 alignment (50-day above 150/200-day, both rising).

After the visual read, still return:
- pivot_price: your tick-aligned estimate of the breakout/pivot level
- t1, t2, t3: exactly three strictly increasing, tick-aligned upside structural objectives in the prior-uptrend direction. Do not set t1 at the pivot, the breakout tick, or the first nearby resistance just above the base high. t1 is a full last-contraction measured move or a prior major swing high with overhead room; t2 and t3 are further expansions of that same upside structure.
- entry_template:
  - 'single' — tightest bases, maximum pattern quality, one leg
  - 'two_leg_staged' — C3 micro/cheat-pivot clearance then base breakout
  - 'two_leg' — standard VCP base
  - 'three_leg_front' — front-loaded 3-leg for large liquid setups
  - 'three_leg_balanced' — balanced 3-leg for wider contractions

Verdict rules — be strict and skeptical:
- 'valid' only when prior uptrend is present, 2–6 contractions clearly get successively tighter, volume dry-up is visible into the pivot, and the pivot is identifiable.
- 'partial' when some VCP traits exist but the pattern is incomplete, ambiguous, or missing volume evidence.
- 'invalid' when this is not a VCP (widening contractions, no prior trend, distribution, chop, or a deep/immature base).

Do NOT calculate or suggest stops, quantities, capital, monetary risk, position or sector exposure, daily-loss limits, add sizes, target sizes, trailing rules, or a confidence score. Return only the strict JSON schema with no additional properties.
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
            "prior_uptrend": ai_output.prior_uptrend,
            "volume_dry_up": ai_output.volume_dry_up,
            "basis": (
                f"{tmpl.value.upper()} template: {tmpl_info['leg_count']} leg(s) "
                f"with allocations {[float(x)*100 for x in tmpl_info['leg_allocations']]}% "
                f"requiring RVOL >= {tmpl_info['relative_volume_threshold']}x on breakout. "
                f"Max approved risk budget ₹{approved_risk_budget_amount or 0}."
            ),
        },
    }


def _proposal_user_text(*, tick_size: Decimal) -> str:
    return (
        "Evaluate the two charts for a Volatility Contraction Pattern (VCP). "
        f"Instrument tick size is {tick_size}. IMAGE 1 is the 252-session "
        "context view. IMAGE 2 is the 126-session detail view (log price, "
        "volume pane). Estimate prices from the visible axis and snap them to "
        "the tick. Do not invent session dates. Return only the strict "
        "structured opinion."
    )


def proposal_prompt_hash(*, tick_size: Decimal) -> str:
    user_text = _proposal_user_text(tick_size=tick_size)
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
    context_png_b64: str,
    detail_png_b64: str,
    model: str,
    tick_size: Decimal,
) -> dict[str, Any]:
    """Build the chart-only, auditable P10 multimodal request."""
    user_text = _proposal_user_text(tick_size=tick_size)
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
    context_png: bytes,
    detail_png: bytes,
    model: str | None = None,
    tick_size: Decimal = DEFAULT_TICK_SIZE,
) -> tuple[GeminiVcpProposalOutput, dict[str, Any], float, str | None]:
    """Call OpenRouter with the two standardized charts only. No OHLCV table."""
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

    selected_model = model or settings.vcp_vision_model
    context_b64 = base64.b64encode(context_png).decode("ascii")
    detail_b64 = base64.b64encode(detail_png).decode("ascii")
    request_body = build_proposal_vision_request(
        context_png_b64=context_b64,
        detail_png_b64=detail_b64,
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


def _serialize_visual_contraction(leg: GeminiContractionLeg) -> dict[str, Any]:
    return {
        "index": leg.index,
        "depth_pct": str(leg.depth_pct),
        "high_price": str(leg.high_price),
        "low_price": str(leg.low_price),
    }


def contractions_are_successively_tighter(
    contractions: Sequence[GeminiContractionLeg],
) -> bool:
    if not 2 <= len(contractions) <= 6:
        return False
    ordered = sorted(contractions, key=lambda leg: leg.index)
    depths = [leg.depth_pct for leg in ordered]
    return all(depths[index] > depths[index + 1] for index in range(len(depths) - 1))


def _pattern_anchors_from_chart_geometry(
    geometry: DeterministicChartGeometry,
) -> list[ValidatedPatternAnchor]:
    anchors: list[ValidatedPatternAnchor] = []
    for anchor in geometry.anchors:
        try:
            date = dt.date.fromisoformat(anchor.date)
        except ValueError:
            continue
        anchors.append(
            ValidatedPatternAnchor(
                date=date,
                price=anchor.price,
                anchor_type=anchor.anchor_type,
            )
        )
    return anchors


def _final_low_anchor_from_geometry(
    *,
    geometry: DeterministicChartGeometry,
    pattern_anchors: Sequence[ValidatedPatternAnchor],
    candles: Sequence[CandleData],
) -> ValidatedPatternAnchor:
    low_anchors = [
        anchor for anchor in pattern_anchors if anchor.anchor_type == "contraction_low"
    ]
    if low_anchors:
        return max(low_anchors, key=lambda anchor: anchor.date)
    last_dated = next(
        (
            candle
            for candle in reversed(candles)
            if candle.date is not None
        ),
        None,
    )
    if last_dated is None or last_dated.date is None:
        raise ValueError("Dated candles are required to locate the final contraction low")
    return ValidatedPatternAnchor(
        date=dt.date.fromisoformat(last_dated.date),
        price=geometry.final_contraction_low,
        anchor_type="contraction_low",
    )


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
    if ai_output.verdict != "valid":
        logger.info("Symbol %s rejected by AI verdict (%s)", symbol, ai_output.verdict)
        if ai_output.verdict == "partial":
            return _rejected(
                "proposal_ai_partial",
                "Gemini returned a partial VCP verdict.",
            )
        return _rejected(
            "proposal_ai_invalid",
            f"Gemini returned verdict={ai_output.verdict!r}.",
        )
    if ai_output.prior_uptrend != "yes":
        return _rejected(
            "proposal_ai_no_prior_uptrend",
            "Gemini did not confirm a prior Stage 2 uptrend.",
        )
    if ai_output.volume_dry_up != "yes":
        return _rejected(
            "proposal_ai_no_volume_dry_up",
            "Gemini did not confirm volume dry-up into the pivot.",
        )
    if not contractions_are_successively_tighter(ai_output.contractions):
        return _rejected(
            "proposal_ai_contractions_not_tightening",
            "Gemini contractions must be 2–6 legs with strictly decreasing depth.",
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

    try:
        chart_geometry = derive_chart_geometry(candles, tick_size=tick_size)
    except ValueError as exc:
        return _rejected(
            "proposal_geometry_unavailable",
            f"Deterministic chart geometry could not be derived: {exc}",
        )

    tolerance = atr14 * Decimal("0.50")
    validated_pattern_anchors = _pattern_anchors_from_chart_geometry(chart_geometry)
    if not validated_pattern_anchors:
        return _rejected(
            "proposal_geometry_unavailable",
            "Deterministic chart geometry produced no dated contraction/resistance anchors.",
        )

    try:
        final_low_anchor = _final_low_anchor_from_geometry(
            geometry=chart_geometry,
            pattern_anchors=validated_pattern_anchors,
            candles=candles,
        )
    except ValueError as exc:
        return _rejected("proposal_geometry_unavailable", str(exc))
    final_contraction_low = chart_geometry.final_contraction_low

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
        if chart_geometry.cheat_pivot and chart_geometry.cheat_stop:
            cheat_geom = construct_and_validate_proposal(
                pivot_price=chart_geometry.cheat_pivot,
                final_contraction_low=chart_geometry.final_contraction_low,
                t1=ai_output.t1,
                t2=ai_output.t2,
                t3=ai_output.t3,
                atr14=atr14,
                tick_size=tick_size,
            )
            if cheat_geom.is_valid:
                geom = cheat_geom
                ai_output = ai_output.model_copy(update={
                    "pivot_price": chart_geometry.cheat_pivot,
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
        "confidence": Decimal("0"),
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
            "prior_uptrend": ai_output.prior_uptrend,
            "prior_uptrend_note": ai_output.prior_uptrend_note,
            "volume_dry_up": ai_output.volume_dry_up,
            "volume_dry_up_note": ai_output.volume_dry_up_note,
            "contractions": [
                _serialize_visual_contraction(leg)
                for leg in sorted(ai_output.contractions, key=lambda item: item.index)
            ],
            "red_flags": list(ai_output.red_flags),
            "evidence_summary": ai_output.evidence_summary,
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
