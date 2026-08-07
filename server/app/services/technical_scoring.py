import math
from typing import Any

import pandas as pd

from app.services.screening_config import TechnicalScreeningConfig


CORE_CHECK_KEYS = (
    "price_above_150_200_sma",
    "sma_150_above_200_sma",
    "sma_200_trending_up_1m",
    "sma_50_above_150_200_sma",
    "price_above_50_sma",
)


def clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, value))


def linear_score(
    value: float,
    *,
    full_at: float,
    zero_at: float,
    points: float,
    lower_is_better: bool = True,
) -> float:
    """Return continuous points between two ordered curve endpoints."""
    if lower_is_better:
        return points * clamp_unit((zero_at - value) / (zero_at - full_at))
    return points * clamp_unit((value - zero_at) / (full_at - zero_at))


def relationship_points(
    lhs: float,
    rhs: float,
    *,
    points: float,
    zero_miss_pct: float,
) -> float:
    """Full credit for a pass; partial credit for a bounded percentage miss."""
    if lhs > rhs:
        return points
    miss_fraction = (rhs - lhs) / rhs
    return points * clamp_unit(1.0 - miss_fraction / (zero_miss_pct / 100.0))


def technical_score_grade(
    score: float,
    config: TechnicalScreeningConfig,
) -> str:
    if score >= config.grade_a_min:
        return "A"
    if score >= config.grade_b_min:
        return "B"
    if score >= config.grade_c_min:
        return "C"
    return "D"


def evaluate_technical_setup(
    df: pd.DataFrame,
    *,
    rs_rating: int,
    history_days: int,
    config: TechnicalScreeningConfig | None = None,
) -> dict[str, Any]:
    """Evaluate v2 eligibility and calculate a deterministic 0-100 score."""
    config = config or TechnicalScreeningConfig()
    if df.empty:
        return _invalid_result("No indicator rows available")

    latest = df.iloc[-1]
    required = (
        "close",
        "sma_50",
        "sma_150",
        "sma_200",
        "sma_200_prev_22",
        "high_52w",
        "low_52w",
        "adtv_crore",
        "atr_ratio",
        "atr_ratio_3m_low",
        "bb_width",
        "bb_width_percentile",
        "volume_dry_up_ratio",
    )
    values = {name: float(latest[name]) for name in required}
    inputs_valid = all(math.isfinite(value) for value in values.values())
    inputs_valid = inputs_valid and all(
        values[name] > 0
        for name in (
            "close",
            "sma_50",
            "sma_150",
            "sma_200",
            "sma_200_prev_22",
            "high_52w",
            "low_52w",
            "atr_ratio_3m_low",
        )
    )
    if not inputs_valid:
        return _invalid_result(
            "Insufficient or invalid data for technical scoring",
            raw_inputs=values,
        )

    close = values["close"]
    sma_50 = values["sma_50"]
    sma_150 = values["sma_150"]
    sma_200 = values["sma_200"]
    sma_200_prev_22 = values["sma_200_prev_22"]

    core_checks = {
        "price_above_150_200_sma": close > sma_150 and close > sma_200,
        "sma_150_above_200_sma": sma_150 > sma_200,
        "sma_200_trending_up_1m": sma_200 > sma_200_prev_22,
        "sma_50_above_150_200_sma": sma_50 > sma_150 and sma_50 > sma_200,
        "price_above_50_sma": close > sma_50,
    }
    core_checks_passed = sum(core_checks.values())
    distance_52w_high_pct = ((values["high_52w"] - close) / values["high_52w"]) * 100
    above_52w_low_pct = ((close / values["low_52w"]) - 1.0) * 100
    atr_proximity_factor = values["atr_ratio"] / values["atr_ratio_3m_low"]

    eligibility = {
        "active_nifty500_member": True,
        "current_reference_eod": True,
        "minimum_history": history_days >= config.minimum_history_days,
        "valid_indicator_inputs": inputs_valid,
        "adtv_above_minimum": values["adtv_crore"] > config.min_adtv_crore,
        "close_above_200_sma": close > sma_200,
        "stage2_core_checks": (
            core_checks_passed >= config.stage2_core_checks_required
        ),
        "within_52w_high_guardrail": (
            distance_52w_high_pct <= config.max_distance_52w_high_pct
        ),
    }
    eligible = all(eligibility.values())

    relationship_zero_miss = config.stage2_relationship_zero_miss_pct
    core_points = {
        "price_above_150_200_sma": min(
            relationship_points(
                close,
                sma_150,
                points=config.stage2_core_check_points,
                zero_miss_pct=relationship_zero_miss,
            ),
            relationship_points(
                close,
                sma_200,
                points=config.stage2_core_check_points,
                zero_miss_pct=relationship_zero_miss,
            ),
        ),
        "sma_150_above_200_sma": relationship_points(
            sma_150,
            sma_200,
            points=config.stage2_core_check_points,
            zero_miss_pct=relationship_zero_miss,
        ),
        "sma_200_trending_up_1m": relationship_points(
            sma_200,
            sma_200_prev_22,
            points=config.stage2_core_check_points,
            zero_miss_pct=config.stage2_sma200_zero_decline_pct,
        ),
        "sma_50_above_150_200_sma": min(
            relationship_points(
                sma_50,
                sma_150,
                points=config.stage2_core_check_points,
                zero_miss_pct=relationship_zero_miss,
            ),
            relationship_points(
                sma_50,
                sma_200,
                points=config.stage2_core_check_points,
                zero_miss_pct=relationship_zero_miss,
            ),
        ),
        "price_above_50_sma": relationship_points(
            close,
            sma_50,
            points=config.stage2_core_check_points,
            zero_miss_pct=relationship_zero_miss,
        ),
    }
    low_points = linear_score(
        above_52w_low_pct,
        full_at=config.stage2_52w_low_full_pct,
        zero_at=config.stage2_52w_low_zero_pct,
        points=config.stage2_52w_low_points,
        lower_is_better=False,
    )
    stage2_points = sum(core_points.values()) + low_points
    rs_points = linear_score(
        float(rs_rating),
        full_at=config.rs_score_full,
        zero_at=config.rs_score_zero,
        points=config.rs_weight,
        lower_is_better=False,
    )
    high_points = linear_score(
        distance_52w_high_pct,
        full_at=config.high_proximity_full_pct,
        zero_at=config.high_proximity_zero_pct,
        points=config.high_proximity_weight,
    )
    atr_points = linear_score(
        atr_proximity_factor,
        full_at=config.atr_proximity_full,
        zero_at=config.atr_proximity_zero,
        points=config.atr_contraction_weight,
    )
    bb_points = linear_score(
        values["bb_width_percentile"],
        full_at=config.bb_percentile_full,
        zero_at=config.bb_percentile_zero,
        points=config.bb_contraction_weight,
    )
    volume_points = linear_score(
        values["volume_dry_up_ratio"],
        full_at=config.volume_ratio_full,
        zero_at=config.volume_ratio_zero,
        points=config.volume_dry_up_weight,
    )

    components = {
        "stage2": {
            "points": stage2_points,
            "max_points": config.stage2_weight,
            "raw_value": {
                "core_checks_passed": core_checks_passed,
                "core_checks_total": len(CORE_CHECK_KEYS),
                "above_52w_low_pct": above_52w_low_pct,
                "core_check_points": core_points,
            },
        },
        "relative_strength": {
            "points": rs_points,
            "max_points": config.rs_weight,
            "raw_value": float(rs_rating),
        },
        "high_proximity": {
            "points": high_points,
            "max_points": config.high_proximity_weight,
            "raw_value": distance_52w_high_pct,
        },
        "atr_contraction": {
            "points": atr_points,
            "max_points": config.atr_contraction_weight,
            "raw_value": atr_proximity_factor,
        },
        "bollinger_contraction": {
            "points": bb_points,
            "max_points": config.bb_contraction_weight,
            "raw_value": values["bb_width_percentile"],
        },
        "volume_dry_up": {
            "points": volume_points,
            "max_points": config.volume_dry_up_weight,
            "raw_value": values["volume_dry_up_ratio"],
        },
    }
    score = round(sum(component["points"] for component in components.values()), 2)

    return {
        "eligible": eligible,
        "eligibility": eligibility,
        "core_checks": core_checks,
        "score": score,
        "grade": technical_score_grade(score, config),
        "components": components,
        "raw_inputs": {
            **values,
            "rs_rating": rs_rating,
            "history_days": history_days,
            "core_checks_passed": core_checks_passed,
            "distance_52w_high_pct": distance_52w_high_pct,
            "above_52w_low_pct": above_52w_low_pct,
            "atr_proximity_factor": atr_proximity_factor,
        },
    }


def _invalid_result(
    error: str,
    *,
    raw_inputs: dict[str, float] | None = None,
) -> dict[str, Any]:
    return {
        "eligible": False,
        "eligibility": {"valid_indicator_inputs": False},
        "core_checks": {},
        "score": None,
        "grade": None,
        "components": {},
        "raw_inputs": raw_inputs or {},
        "error": error,
    }
