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
    SurvivingContraction,
    VcpContractionWave,
    compute_atr14,
    construct_python_owned_levels,
    DEFAULT_TICK_SIZE,
    derive_chart_geometry,
    depths_non_increasing,
    format_candidate_summary,
    ProposalGeometry,
    resolve_surviving_contractions,
)
from app.domain.p10_sizing import EntryTemplate, TEMPLATE_CONFIG
from app.domain.p10_triggers import BREAKOUT_BAR_SIGNAL_POLICY_V2
from app.domain.p10_template_policy import (
    TEMPLATE_POLICY_VERSION,
    TemplateScoreFeatures,
    select_entry_template,
)
from app.schemas.proposals import GeminiVcpProposalOutput
from app.services.fundamental_llm import sanitize_provider_payload
from app.services.openrouter_content import (
    decode_openrouter_json_value,
    parse_openrouter_structured_content,
)
from app.services.openrouter_schema import gemini_compatible_json_schema
from app.services.proposal_renderer import RenderedProposalCharts


logger = logging.getLogger(__name__)
IST_TZ = ZoneInfo("Asia/Kolkata")
PROMPT_VERSION = "p10_vcp_proposal_v6"
SCHEMA_VERSION = "gemini_vcp_proposal_output_v6"
GEOMETRY_VERSION = "p10_python_owned_levels_v5"
ENTRY_TRIGGER_POLICY_VERSION = BREAKOUT_BAR_SIGNAL_POLICY_V2
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

You will be shown ONE standardized 126-session stock-chart image (log price, volume pane, EMA21 and SMA50/150/200) plus a short numbered list of Python swing-detector candidate contractions. Those candidates already have exact dates, percent depths, and volume-vs-average ratios computed from real OHLCV. Do not re-estimate those numbers from pixels.

Your job is to audit whether the visual pattern supports those candidates, and to add qualitative judgments an algorithm cannot make.

Audit checklist:

1. Classification
   - valid: a completed VCP with the last contraction in place (pivot identifiable on the chart).
   - forming: 1–2 pullbacks so far and the pattern still looks like it is developing (no real pivot yet) OR still tightening but incomplete.
   - not_vcp: not a VCP (breakdown, distribution, chop, no Stage 2 context, widening swings).

2. For each numbered Python candidate, return exactly one assessment:
   - confirm: the visual pullback matches that candidate window.
   - merge: two (or more) candidates are the same contraction; set merge_with_index to the sibling index.
   - reject: that candidate is noise, not a real contraction.

3. extra_windows: date ranges (YYYY-MM-DD) only, if you see a contraction Python missed. No prices.

4. Progressive tightening: yes only if each surviving contraction looks shallower than the last.

5. Volume dry-up: clearly / somewhat / not_really. Use the chart AND the volume ratios in the candidate list. Do not invent percentages.

6. Base quality:
   - price_action: orderly vs choppy
   - climax_or_gap_violation: climactic volume spikes or gap days that violate the pattern
   - stage2_context: sitting inside a prior uptrend, not a downtrend bounce

7. confidence: integer 0–100 for how clearly the chart supports your classification. This is a display field only.

Do NOT output a pivot, stop, target, entry, quantity, risk, template, or a free contraction_count. Python derives counts from your confirm/merge/reject/extra actions. Return only the strict JSON schema.
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


def _serialize_survivor(item: SurvivingContraction) -> dict[str, Any]:
    return {
        "index": item.index,
        "high_date": item.high_date,
        "high_price": str(item.high_price),
        "low_date": item.low_date,
        "low_price": str(item.low_price),
        "depth_pct": str(item.depth_pct),
        "vol_adv20": str(item.vol_adv20) if item.vol_adv20 is not None else None,
        "vol_adv50": str(item.vol_adv50) if item.vol_adv50 is not None else None,
        "source": item.source,
        "member_indices": list(item.member_indices),
    }


def _serialize_python_candidate(wave: VcpContractionWave) -> dict[str, Any]:
    return {
        "index": wave.index,
        "high_date": wave.high_date,
        "high_price": str(wave.high_price),
        "low_date": wave.low_date,
        "low_price": str(wave.low_price),
        "depth_pct": str(wave.depth_pct),
        "vol_adv20": str(wave.vol_adv20) if wave.vol_adv20 is not None else None,
        "vol_adv50": str(wave.vol_adv50) if wave.vol_adv50 is not None else None,
    }


def _serialize_calculation_basis(
    *,
    geom: ProposalGeometry,
    ai_output: GeminiVcpProposalOutput,
    survivors: Sequence[SurvivingContraction],
    atr14: Decimal,
    tmpl: EntryTemplate,
    tmpl_info: dict[str, Any],
    template_reason: str,
    risk_per_trade_pct: Decimal,
    approved_risk_budget_amount: Decimal | None,
    risk_policy_version: int,
    tick_size: Decimal,
) -> dict[str, Any]:
    final = survivors[-1]
    stop_dist = (geom.planned_entry or geom.pivot_price) - geom.initial_stop
    entry = geom.planned_entry or geom.pivot_price
    chase_margin = geom.chase_ceiling - entry
    chase_pct = (
        round((chase_margin / entry) * Decimal("100"), 2) if entry > 0 else Decimal("0")
    )
    slots = geom.target_slots or ("floor", "measured", "stretch")
    return {
        "pivot": {
            "pivot_price": str(geom.pivot_price),
            "source": "final_surviving_contraction_high",
            "final_contraction_high_date": final.high_date,
            "basis": (
                f"Pivot ₹{geom.pivot_price} is the snapped high of the latest-dated "
                f"surviving contraction on {final.high_date}."
            ),
        },
        "stop_loss": {
            "initial_stop": str(geom.initial_stop),
            "final_contraction_low": str(final.low_price),
            "final_contraction_low_date": final.low_date,
            "atr14": str(atr14),
            "stop_buffer_multiplier": "0.25",
            "stop_buffer_amount": str(atr14 * Decimal("0.25")),
            "stop_distance": str(stop_dist),
            "stop_distance_pct": str(geom.stop_distance_pct),
            "max_allowed_stop_pct": "8.00",
            "wide_risk_flag": geom.wide_risk_flag,
            "formula": (
                f"Final surviving low (₹{final.low_price}) - 0.25xATR14 "
                f"(₹{atr14 * Decimal('0.25'):.2f}), snapped to tick"
            ),
        },
        "entry_chase": {
            "planned_entry": str(entry),
            "entry_buffer_multiplier": "0.10",
            "base_chase_ceiling": _decimal_str(geom.base_chase_ceiling or geom.chase_ceiling),
            "final_chase_ceiling": str(geom.chase_ceiling),
            "max_chase_margin": str(chase_margin),
            "max_chase_pct": str(chase_pct),
            "formula": "planned_entry + min(2% of entry, 0.5xR) floored to tick",
            "targets_locked_at_planned_entry": True,
        },
        "targets": {
            "t1": {"price": str(geom.t1), "formula": slots[0], "r_at_entry": _decimal_str(geom.t1_r)},
            "t2": {"price": str(geom.t2), "formula": slots[1], "r_at_entry": _decimal_str(geom.t2_r)},
            "t3": {"price": str(geom.t3), "formula": slots[2], "r_at_entry": _decimal_str(geom.t3_r)},
            "slots": {"t1": slots[0], "t2": slots[1], "t3": slots[2]},
            "frozen_at_planned_entry": True,
        },
        "fifty_two_week": {
            "tags": list(geom.fifty_two_week_tags),
            "distance_to_52w_pct": _decimal_str(geom.distance_to_52w_pct),
        },
        "sizing_and_risk": {
            "entry_template": tmpl.value,
            "template_policy_version": TEMPLATE_POLICY_VERSION,
            "template_reason": template_reason,
            "leg_count": tmpl_info["leg_count"],
            "leg_risk_allocations": [float(x) for x in tmpl_info["leg_allocations"]],
            "relative_volume_threshold": float(
                tmpl_info["breakout_bar_rvol_threshold"]
            ),
            "entry_trigger_policy_version": ENTRY_TRIGGER_POLICY_VERSION,
            "risk_per_trade_pct": str(risk_per_trade_pct * Decimal("100")),
            "approved_risk_budget_amount": (
                str(approved_risk_budget_amount) if approved_risk_budget_amount is not None else None
            ),
            "risk_policy_version": risk_policy_version,
            "classification": ai_output.classification,
            "volume_dry_up": ai_output.volume_dry_up,
            "stage2_context": ai_output.base_quality.stage2_context,
            "tick_size": str(tick_size),
        },
    }


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
    python_candidates: Sequence[VcpContractionWave] | None = None,
) -> ProposalBuildResult:
    """Combine Gemini audit + frozen candles into an immutable proposal, or a gated non-proposal."""
    if ai_output.classification == "forming":
        return _rejected(
            "proposal_ai_forming",
            "Gemini classified the pattern as forming; no pivot/entry/target is computed.",
            details={
                "forming_state": ai_output.forming_state,
                "confidence": ai_output.confidence,
            },
        )
    if ai_output.classification != "valid":
        return _rejected(
            "proposal_ai_invalid",
            f"Gemini returned classification={ai_output.classification!r}.",
        )

    if len(candles) < 252:
        return _rejected(
            "proposal_insufficient_candles",
            f"Frozen input has {len(candles)} sessions; 252 are required.",
        )

    try:
        chart_geometry = derive_chart_geometry(candles, tick_size=tick_size)
    except ValueError as exc:
        return _rejected(
            "proposal_geometry_unavailable",
            f"Deterministic chart geometry could not be derived: {exc}",
        )

    candidates = tuple(python_candidates) if python_candidates is not None else chart_geometry.contractions
    if not candidates:
        return _rejected(
            "proposal_no_python_candidates",
            "Python swing detection found no contraction candidates in the 126-session window.",
        )

    resolution = resolve_surviving_contractions(
        candidates=candidates,
        assessments=ai_output.candidate_assessments,
        extra_windows=ai_output.extra_windows,
        candles=candles,
    )
    if not resolution.is_valid:
        return _rejected(
            "proposal_survivor_resolution_failed",
            resolution.rejection_reason or "Survivor resolution failed.",
            details={"python_count": resolution.python_count, "llm_count": resolution.llm_count},
        )

    numeric_failures: list[str] = []
    if resolution.llm_count < 2:
        numeric_failures.append("llm_count_lt_2")
    if not depths_non_increasing(resolution.survivors):
        numeric_failures.append("depths_not_non_increasing")
    if ai_output.base_quality.stage2_context != "yes":
        numeric_failures.append("stage2_context_no")
    if ai_output.volume_dry_up not in {"clearly", "somewhat"}:
        numeric_failures.append("volume_dry_up_not_really")
    first_vol = resolution.survivors[0].vol_adv20 if resolution.survivors else None
    last_vol = resolution.survivors[-1].vol_adv20 if resolution.survivors else None
    if first_vol is not None and last_vol is not None and last_vol > first_vol:
        numeric_failures.append("volume_ratio_not_drying")

    if numeric_failures:
        return _rejected(
            "proposal_numeric_gate_failed",
            "Independent numeric gates failed after a valid Gemini classification.",
            details={
                "failures": numeric_failures,
                "python_count": resolution.python_count,
                "llm_count": resolution.llm_count,
                "survivors": [_serialize_survivor(item) for item in resolution.survivors],
            },
        )

    atr14 = compute_atr14(candles)
    geom = construct_python_owned_levels(
        survivors=resolution.survivors,
        candles=candles,
        atr14=atr14,
        tick_size=tick_size,
    )
    if not geom.is_valid:
        return _rejected(
            "proposal_geometry_invalid",
            geom.rejection_reason or "Deterministic proposal geometry validation failed.",
            details={
                "python_count": resolution.python_count,
                "llm_count": resolution.llm_count,
                "survivors": [_serialize_survivor(item) for item in resolution.survivors],
                **_serialize_rr_audit(geom),
            },
        )

    if approved_risk_budget_amount is None or approved_risk_budget_amount <= 0:
        return _rejected(
            "proposal_risk_budget_missing",
            "No operator-configured monetary risk budget is available for this proposal.",
        )

    score = select_entry_template(
        TemplateScoreFeatures(
            confidence=ai_output.confidence,
            llm_count=resolution.llm_count,
            python_count=resolution.python_count,
            volume_dry_up=ai_output.volume_dry_up,
            progressive_tightening=ai_output.progressive_tightening,
            price_action=ai_output.base_quality.price_action,
            climax_or_gap_violation=ai_output.base_quality.climax_or_gap_violation,
            risk_pct=geom.stop_distance_pct,
            pivot_window_vol_adv20=resolution.survivors[-1].vol_adv20,
            distance_to_52w_pct=geom.distance_to_52w_pct,
        )
    )
    tmpl = score.template
    tmpl_info = TEMPLATE_CONFIG[tmpl]
    entry_session, approval_deadline = calculate_next_session_and_deadline(
        as_of_date,
        holidays=holidays,
    )
    source_hash = compute_frozen_source_hash(candles)
    completed_at = generated_at or dt.datetime.now(dt.timezone.utc)
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=dt.timezone.utc)
    live_cutoff = dt.datetime.combine(entry_session, dt.time(8, 30), tzinfo=IST_TZ)
    live_eligible = completed_at.astimezone(IST_TZ) <= live_cutoff
    proposal_status = (
        "pending_approval"
        if completed_at.astimezone(IST_TZ) < approval_deadline
        else "expired_unapproved"
    )
    calc_basis = _serialize_calculation_basis(
        geom=geom,
        ai_output=ai_output,
        survivors=resolution.survivors,
        atr14=atr14,
        tmpl=tmpl,
        tmpl_info=tmpl_info,
        template_reason=score.reason,
        risk_per_trade_pct=risk_per_trade_pct,
        approved_risk_budget_amount=approved_risk_budget_amount,
        risk_policy_version=risk_policy_version,
        tick_size=tick_size,
    )
    locked_plan: dict[str, Any] = {
        "screening_result_id": screening_result_id,
        "instrument_id": instrument_id,
        "symbol": symbol,
        "as_of_date": as_of_date,
        "status": proposal_status,
        "approval_deadline": approval_deadline,
        "entry_session_date": entry_session,
        "source_hash": source_hash,
        "renderer_version": rendered_charts.renderer_version,
        "geometry_version": GEOMETRY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "model": model,
        "confidence": Decimal(str(ai_output.confidence)),
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
        "relative_volume_threshold": tmpl_info["breakout_bar_rvol_threshold"],
        "entry_trigger_policy_version": ENTRY_TRIGGER_POLICY_VERSION,
        "gemini_evidence": {
            "classification": ai_output.classification,
            "forming_state": ai_output.forming_state,
            "progressive_tightening": ai_output.progressive_tightening,
            "volume_dry_up": ai_output.volume_dry_up,
            "base_quality": ai_output.base_quality.model_dump(),
            "candidate_assessments": [
                row.model_dump(mode="json") for row in ai_output.candidate_assessments
            ],
            "extra_windows": [row.model_dump(mode="json") for row in ai_output.extra_windows],
            "confidence": ai_output.confidence,
            "red_flags": list(ai_output.red_flags),
            "evidence_summary": ai_output.evidence_summary,
            "python_count": resolution.python_count,
            "llm_count": resolution.llm_count,
            "mismatch_banner": score.mismatch_banner,
        },
        "geometry": {
            "atr14": str(geom.atr14),
            "planned_entry": str(geom.planned_entry),
            "pivot_r_distance": str(geom.r_distance),
            "worst_entry_r_distance": str(geom.chase_ceiling - geom.initial_stop),
            "final_contraction_low": str(resolution.survivors[-1].low_price),
            "python_candidates": [_serialize_python_candidate(wave) for wave in candidates],
            "survivors": [_serialize_survivor(item) for item in resolution.survivors],
            "target_slots": {
                "t1": (geom.target_slots or ("floor", "measured", "stretch"))[0],
                "t2": (geom.target_slots or ("floor", "measured", "stretch"))[1],
                "t3": (geom.target_slots or ("floor", "measured", "stretch"))[2],
            },
            "fifty_two_week_tags": list(geom.fifty_two_week_tags),
            "distance_to_52w_pct": _decimal_str(geom.distance_to_52w_pct),
            "wide_risk_flag": geom.wide_risk_flag,
            "template_policy_version": TEMPLATE_POLICY_VERSION,
            "template_reason": score.reason,
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

def _proposal_user_text(*, tick_size: Decimal, candidate_summary: str) -> str:
    return (
        "Audit the 126-session chart for a Volatility Contraction Pattern (VCP). "
        f"Instrument tick size is {tick_size} (for your orientation only — do not emit prices). "
        "IMAGE 1 is the 126-session window (log price, volume, EMA21/SMA50/150/200). "
        "Python already detected these candidate contractions from real OHLCV:\n"
        f"{candidate_summary}\n"
        "Confirm, merge, or reject each numbered candidate. Add extra_windows only "
        "for contractions the algorithm missed. Do not invent prices, stops, targets, "
        "or a contraction_count. Return only the strict structured opinion."
    )


def proposal_prompt_hash(*, tick_size: Decimal, candidate_summary: str = "") -> str:
    user_text = _proposal_user_text(tick_size=tick_size, candidate_summary=candidate_summary)
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
    detail_png_b64: str,
    model: str,
    tick_size: Decimal,
    candidate_summary: str,
) -> dict[str, Any]:
    """Build the chart + candidate-summary P10 multimodal request."""
    user_text = _proposal_user_text(
        tick_size=tick_size,
        candidate_summary=candidate_summary,
    )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": GEMINI_PROPOSAL_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "text", "text": "IMAGE 1 — 126-session VCP window:"},
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
    candidate_summary: str = "",
) -> tuple[GeminiVcpProposalOutput, dict[str, Any], float, str | None]:
    """Call OpenRouter with the 126-session chart plus candidate summary."""
    del context_png
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

    selected_model = model or settings.vcp_vision_model
    detail_b64 = base64.b64encode(detail_png).decode("ascii")
    request_body = build_proposal_vision_request(
        detail_png_b64=detail_b64,
        model=selected_model,
        tick_size=tick_size,
        candidate_summary=candidate_summary,
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
    if "classification" in data and "choices" not in data:
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
    if isinstance(choice, Mapping) and "classification" in choice and "message" not in choice:
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

