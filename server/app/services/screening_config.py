from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TechnicalScreeningConfig(BaseModel):
    """Versioned inputs for producing the manual-review shortlist."""

    model_config = ConfigDict(frozen=True)

    pipeline_version: Literal["vcp_shortlist_v1"] = "vcp_shortlist_v1"
    liquidity_lookback_days: int = Field(default=20, ge=5, le=100)
    min_adtv_crore: float = Field(default=10.0, gt=0)
    pivot_max_distance_pct: float = Field(default=15.0, gt=0, le=25)
    atr_short_days: int = Field(default=10, ge=2, le=50)
    atr_long_days: int = Field(default=50, ge=10, le=200)
    atr_low_lookback_days: int = Field(default=63, ge=20, le=252)
    atr_near_low_multiplier: float = Field(default=1.10, ge=1.0, le=2.0)
    bb_window_days: int = Field(default=20, ge=10, le=100)
    bb_std_deviations: float = Field(default=2.0, gt=0, le=5)
    bb_percentile_lookback_days: int = Field(default=126, ge=20, le=252)
    bb_max_percentile: float = Field(default=0.20, gt=0, le=1)
    volume_short_days: int = Field(default=10, ge=2, le=50)
    volume_long_days: int = Field(default=50, ge=10, le=200)
    max_volume_dry_up_ratio: float = Field(default=0.80, gt=0, le=1)
    shortlist_limit: int = Field(default=20, ge=15, le=20)

    @model_validator(mode="after")
    def validate_window_ordering(self) -> Self:
        if self.atr_short_days >= self.atr_long_days:
            raise ValueError("atr_short_days must be less than atr_long_days")
        if self.volume_short_days >= self.volume_long_days:
            raise ValueError("volume_short_days must be less than volume_long_days")
        return self


DEFAULT_TECHNICAL_SCREENING_CONFIG = TechnicalScreeningConfig()
