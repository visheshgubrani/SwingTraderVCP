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


def signed_linear_score(
    value: float,
    *,
    full_at: float,
    zero_at: float,
    points: float,
    negative_floor: float,
    lower_is_better: bool = False,
) -> float:
    """
    Linear ramp that continues below the zero endpoint with the same slope,
    clamped at ``negative_floor``.
    """
    if lower_is_better:
        raise ValueError("signed_linear_score currently supports higher-is-better only")
    span = full_at - zero_at
    if span == 0:
        return 0.0
    unit = (value - zero_at) / span
    raw = points * unit
    if raw >= 0:
        return min(points, raw)
    return max(negative_floor, raw)


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


def pocket_pivot_points(age: float | None, config: TechnicalScreeningConfig) -> float:
    """Recency-weighted pocket pivot: full through full_age, zero at zero_age."""
    if age is None or not math.isfinite(float(age)):
        return 0.0
    age_value = float(age)
    if age_value <= config.pocket_pivot_full_age:
        return config.pocket_pivot_weight
    if age_value >= config.pocket_pivot_zero_age:
        return 0.0
    span = float(config.pocket_pivot_zero_age - config.pocket_pivot_full_age)
    return config.pocket_pivot_weight * (
        1.0 - (age_value - config.pocket_pivot_full_age) / span
    )


def evaluate_technical_setup(
    df: pd.DataFrame,
    *,
    rs_rating: int,
    history_days: int,
    config: TechnicalScreeningConfig | None = None,
) -> dict[str, Any]:
    """Evaluate eligibility and calculate a deterministic 0-100 score."""
    config = config or TechnicalScreeningConfig()
    if df.empty:
        return _invalid_result("No indicator rows available")

    latest = df.iloc[-1]
    required = [
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
    ]
    if config.pipeline_version == "vcp_score_v3":
        required.extend(
            [
                "up_down_volume_ratio",
                "rs_line_pct_off_high",
            ]
        )

    values: dict[str, float] = {}
    for name in required:
        if name not in latest.index:
            return _invalid_result(f"Missing indicator column: {name}")
        values[name] = float(latest[name])

    pocket_age_raw = latest["pocket_pivot_age"] if "pocket_pivot_age" in latest.index else float("nan")
    pocket_age = (
        float(pocket_age_raw)
        if pocket_age_raw is not None and not pd.isna(pocket_age_raw)
        else None
    )

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
        "rs_above_minimum": (
            config.min_rs_rating is None or rs_rating >= config.min_rs_rating
        ),
        "atr_contraction_within_limit": (
            config.max_atr_proximity_factor is None
            or atr_proximity_factor <= config.max_atr_proximity_factor
        ),
        "bollinger_contraction_within_limit": (
            config.max_bb_width_percentile is None
            or values["bb_width_percentile"] <= config.max_bb_width_percentile
        ),
        "volume_dry_up_within_limit": (
            config.max_volume_dry_up_ratio is None
            or values["volume_dry_up_ratio"] <= config.max_volume_dry_up_ratio
        ),
    }

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
    volume_points = linear_score(
        values["volume_dry_up_ratio"],
        full_at=config.volume_ratio_full,
        zero_at=config.volume_ratio_zero,
        points=config.volume_dry_up_weight,
    )

    atr_unit = clamp_unit(
        (config.atr_proximity_zero - atr_proximity_factor)
        / (config.atr_proximity_zero - config.atr_proximity_full)
    )
    bb_unit = clamp_unit(
        (config.bb_percentile_zero - values["bb_width_percentile"])
        / (config.bb_percentile_zero - config.bb_percentile_full)
    )

    if config.pipeline_version == "vcp_score_v3":
        contraction_points = ((atr_unit + bb_unit) / 2.0) * config.contraction_weight
        rs_line_points = linear_score(
            values["rs_line_pct_off_high"],
            full_at=config.rs_line_off_full_pct,
            zero_at=config.rs_line_off_zero_pct,
            points=config.rs_line_high_weight,
        )
        up_down_points = signed_linear_score(
            values["up_down_volume_ratio"],
            full_at=config.up_down_volume_full,
            zero_at=config.up_down_volume_zero,
            points=config.up_down_volume_weight,
            negative_floor=config.up_down_volume_negative_floor,
        )
        pocket_points = pocket_pivot_points(pocket_age, config)
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
            "rs_line_high": {
                "points": rs_line_points,
                "max_points": config.rs_line_high_weight,
                "raw_value": values["rs_line_pct_off_high"],
            },
            "high_proximity": {
                "points": high_points,
                "max_points": config.high_proximity_weight,
                "raw_value": distance_52w_high_pct,
            },
            "volatility_contraction": {
                "points": contraction_points,
                "max_points": config.contraction_weight,
                "raw_value": {
                    "atr_unit": atr_unit,
                    "bb_unit": bb_unit,
                    "atr_proximity_factor": atr_proximity_factor,
                    "bb_width_percentile": values["bb_width_percentile"],
                },
            },
            "volume_dry_up": {
                "points": volume_points,
                "max_points": config.volume_dry_up_weight,
                "raw_value": values["volume_dry_up_ratio"],
            },
            "up_down_volume": {
                "points": up_down_points,
                "max_points": config.up_down_volume_weight,
                "raw_value": values["up_down_volume_ratio"],
            },
            "pocket_pivot": {
                "points": pocket_points,
                "max_points": config.pocket_pivot_weight,
                "raw_value": pocket_age,
            },
        }
    else:
        atr_points = atr_unit * config.atr_contraction_weight
        bb_points = bb_unit * config.bb_contraction_weight
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

    raw_total = sum(component["points"] for component in components.values())
    score = round(max(0.0, min(100.0, raw_total)), 2)
    eligibility["technical_score_above_minimum"] = (
        config.minimum_technical_score is None
        or score >= config.minimum_technical_score
    )
    eligible = all(eligibility.values())

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
            "pocket_pivot_age": pocket_age,
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
