from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

PipelineVersion = Literal["vcp_score_v2", "vcp_score_v3"]


class TechnicalScreeningConfig(BaseModel):
    """Versioned, reproducible inputs for the technical score engine."""

    model_config = ConfigDict(frozen=True)

    # Live default stays v2 until shadow-run validation flips it.
    pipeline_version: PipelineVersion = "vcp_score_v2"

    # Eligibility and indicator windows.
    minimum_history_days: int = Field(default=252, ge=252, le=504)
    liquidity_lookback_days: int = Field(default=20, ge=5, le=100)
    min_adtv_crore: float = Field(default=10.0, gt=0)
    stage2_core_checks_required: int = Field(default=4, ge=1, le=5)
    max_distance_52w_high_pct: float = Field(default=25.0, gt=0, le=50)
    atr_short_days: int = Field(default=10, ge=2, le=50)
    atr_long_days: int = Field(default=50, ge=10, le=200)
    atr_low_lookback_days: int = Field(default=63, ge=20, le=252)
    bb_window_days: int = Field(default=20, ge=10, le=100)
    bb_std_deviations: float = Field(default=2.0, gt=0, le=5)
    bb_percentile_lookback_days: int = Field(default=126, ge=20, le=252)
    bb_reference_percentile: float = Field(default=0.20, gt=0, le=1)
    volume_short_days: int = Field(default=10, ge=2, le=50)
    volume_long_days: int = Field(default=50, ge=10, le=200)
    up_down_volume_lookback_days: int = Field(default=50, ge=10, le=200)
    pocket_pivot_lookback_days: int = Field(default=10, ge=5, le=50)
    rs_line_lookback_days: int = Field(default=252, ge=63, le=504)
    rs_benchmark_symbol: str = Field(default="NSE:NIFTY500-INDEX")

    # Score weights. Active set must total 100 for the selected version.
    stage2_weight: float = Field(default=25.0, ge=0, le=100)
    stage2_core_check_points: float = Field(default=4.0, ge=0, le=25)
    stage2_52w_low_points: float = Field(default=5.0, ge=0, le=25)
    rs_weight: float = Field(default=20.0, ge=0, le=100)
    rs_line_high_weight: float = Field(default=0.0, ge=0, le=100)
    high_proximity_weight: float = Field(default=15.0, ge=0, le=100)
    atr_contraction_weight: float = Field(default=15.0, ge=0, le=100)
    bb_contraction_weight: float = Field(default=15.0, ge=0, le=100)
    contraction_weight: float = Field(default=0.0, ge=0, le=100)
    volume_dry_up_weight: float = Field(default=10.0, ge=0, le=100)
    up_down_volume_weight: float = Field(default=0.0, ge=0, le=100)
    pocket_pivot_weight: float = Field(default=0.0, ge=0, le=100)

    # Gentle, continuous score curves.
    stage2_relationship_zero_miss_pct: float = Field(default=2.0, gt=0, le=20)
    stage2_sma200_zero_decline_pct: float = Field(default=1.0, gt=0, le=20)
    stage2_52w_low_zero_pct: float = Field(default=15.0, ge=0, le=100)
    stage2_52w_low_full_pct: float = Field(default=30.0, gt=0, le=500)
    rs_score_zero: float = Field(default=50.0, ge=1, le=99)
    rs_score_full: float = Field(default=90.0, ge=1, le=99)
    rs_line_off_full_pct: float = Field(default=2.0, ge=0, le=50)
    rs_line_off_zero_pct: float = Field(default=12.0, gt=0, le=100)
    high_proximity_full_pct: float = Field(default=15.0, ge=0, le=50)
    high_proximity_zero_pct: float = Field(default=25.0, gt=0, le=100)
    atr_proximity_full: float = Field(default=1.00, gt=0, le=5)
    atr_proximity_zero: float = Field(default=1.40, gt=0, le=5)
    bb_percentile_full: float = Field(default=0.10, ge=0, le=1)
    bb_percentile_zero: float = Field(default=0.60, gt=0, le=1)
    volume_ratio_full: float = Field(default=0.60, ge=0, le=5)
    volume_ratio_zero: float = Field(default=1.20, gt=0, le=5)
    up_down_volume_full: float = Field(default=1.50, gt=0, le=10)
    up_down_volume_zero: float = Field(default=0.80, gt=0, le=10)
    up_down_volume_negative_floor: float = Field(default=-2.0, ge=-20, le=0)
    pocket_pivot_max_extension_pct: float = Field(default=3.0, ge=0, le=20)
    pocket_pivot_full_age: int = Field(default=1, ge=0, le=20)
    pocket_pivot_zero_age: int = Field(default=7, ge=1, le=30)

    # Output policy and display grades.
    shortlist_limit: int = Field(default=500, ge=1, le=1000)
    fundamental_limit: int = Field(default=20, ge=0, le=100)
    grade_a_min: float = Field(default=90.0, ge=0, le=100)
    grade_b_min: float = Field(default=80.0, ge=0, le=100)
    grade_c_min: float = Field(default=70.0, ge=0, le=100)

    @classmethod
    def for_version(cls, version: PipelineVersion) -> Self:
        """Return a fully-specified preset for the given score engine version."""
        if version == "vcp_score_v2":
            return cls()
        return cls(
            pipeline_version="vcp_score_v3",
            stage2_weight=20.0,
            stage2_core_check_points=3.0,
            stage2_52w_low_points=5.0,
            rs_weight=15.0,
            rs_line_high_weight=10.0,
            high_proximity_weight=10.0,
            atr_contraction_weight=0.0,
            bb_contraction_weight=0.0,
            contraction_weight=20.0,
            volume_dry_up_weight=10.0,
            up_down_volume_weight=10.0,
            pocket_pivot_weight=5.0,
        )

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        if self.atr_short_days >= self.atr_long_days:
            raise ValueError("atr_short_days must be less than atr_long_days")
        if self.volume_short_days >= self.volume_long_days:
            raise ValueError("volume_short_days must be less than volume_long_days")
        if self.fundamental_limit > self.shortlist_limit:
            raise ValueError("fundamental_limit cannot exceed shortlist_limit")
        if self.pocket_pivot_full_age >= self.pocket_pivot_zero_age:
            raise ValueError(
                "pocket_pivot_full_age must be less than pocket_pivot_zero_age"
            )

        if self.pipeline_version == "vcp_score_v2":
            component_weight = (
                self.stage2_weight
                + self.rs_weight
                + self.high_proximity_weight
                + self.atr_contraction_weight
                + self.bb_contraction_weight
                + self.volume_dry_up_weight
            )
        else:
            component_weight = (
                self.stage2_weight
                + self.rs_weight
                + self.rs_line_high_weight
                + self.high_proximity_weight
                + self.contraction_weight
                + self.volume_dry_up_weight
                + self.up_down_volume_weight
                + self.pocket_pivot_weight
            )
        if abs(component_weight - 100.0) > 1e-9:
            raise ValueError("technical score component weights must total 100")

        stage2_points = (
            5 * self.stage2_core_check_points + self.stage2_52w_low_points
        )
        if abs(stage2_points - self.stage2_weight) > 1e-9:
            raise ValueError("Stage 2 subcomponent points must equal stage2_weight")

        ordered_ranges = (
            (
                self.stage2_52w_low_zero_pct,
                self.stage2_52w_low_full_pct,
                "Stage 2 52-week-low range",
            ),
            (self.rs_score_zero, self.rs_score_full, "RS score range"),
            (
                self.rs_line_off_full_pct,
                self.rs_line_off_zero_pct,
                "RS line off-high range",
            ),
            (
                self.high_proximity_full_pct,
                self.high_proximity_zero_pct,
                "52-week-high proximity range",
            ),
            (
                self.atr_proximity_full,
                self.atr_proximity_zero,
                "ATR proximity range",
            ),
            (
                self.bb_percentile_full,
                self.bb_percentile_zero,
                "Bollinger percentile range",
            ),
            (
                self.volume_ratio_full,
                self.volume_ratio_zero,
                "volume ratio range",
            ),
            (
                self.up_down_volume_zero,
                self.up_down_volume_full,
                "up/down volume range",
            ),
        )
        for lower, upper, label in ordered_ranges:
            if lower >= upper:
                raise ValueError(f"{label} must have an increasing range")

        if not self.grade_a_min > self.grade_b_min > self.grade_c_min:
            raise ValueError("score grade thresholds must be strictly descending")
        if self.high_proximity_zero_pct != self.max_distance_52w_high_pct:
            raise ValueError(
                "high_proximity_zero_pct must equal max_distance_52w_high_pct"
            )
        return self


DEFAULT_TECHNICAL_SCREENING_CONFIG = TechnicalScreeningConfig()

# Swyingify public Standard board: top 25, no P7 / LLM pass.
SAAS_MINERVINI_STANDARD_CONFIG = TechnicalScreeningConfig(
    shortlist_limit=25,
    fundamental_limit=0,
)


def merge_template_config(raw: dict | None) -> TechnicalScreeningConfig:
    """Merge a template JSON blob onto the SaaS Standard defaults."""
    base = SAAS_MINERVINI_STANDARD_CONFIG.model_dump()
    if raw:
        base.update(raw)
    return TechnicalScreeningConfig.model_validate(base)
