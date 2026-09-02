"""P10 template selection — versioned Python rules, not Gemini.

Maps an LLM+Python feature vector onto the static TEMPLATE_CONFIG in p10_sizing.
Confidence may feed this scorer only; it must not approve, arm, rank, or execute.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.p10_sizing import EntryTemplate


TEMPLATE_POLICY_VERSION = "p10_template_score_v2"
DISAGREEMENT_BANNER_THRESHOLD = 1


@dataclass(frozen=True)
class TemplateScoreFeatures:
    confidence: int
    llm_count: int
    python_count: int
    volume_dry_up: str
    progressive_tightening: str
    price_action: str
    climax_or_gap_violation: str
    risk_pct: Decimal
    pivot_window_vol_adv20: Decimal | None = None
    base_duration_weeks: Decimal | None = None
    distance_to_52w_pct: Decimal | None = None

    @property
    def disagreement(self) -> int:
        return abs(self.llm_count - self.python_count)


@dataclass(frozen=True)
class TemplateScoreResult:
    template: EntryTemplate
    policy_version: str
    disagreement: int
    mismatch_banner: bool
    reason: str


def select_entry_template(features: TemplateScoreFeatures) -> TemplateScoreResult:
    """V1 conservative table. Emits only single | two_leg | three_leg_front."""
    disagreement = features.disagreement
    mismatch_banner = disagreement > DISAGREEMENT_BANNER_THRESHOLD

    if mismatch_banner:
        return TemplateScoreResult(
            template=EntryTemplate.SINGLE,
            policy_version=TEMPLATE_POLICY_VERSION,
            disagreement=disagreement,
            mismatch_banner=True,
            reason="count_disagreement_gt_1",
        )

    dry_up = features.volume_dry_up
    tightening = features.progressive_tightening == "yes"
    orderly = features.price_action == "orderly"
    no_climax = features.climax_or_gap_violation == "no"

    if (
        features.risk_pct <= Decimal("5")
        and features.llm_count >= 3
        and features.python_count >= 3
        and disagreement <= 1
        and dry_up == "clearly"
        and tightening
        and orderly
        and no_climax
        and features.confidence >= 70
    ):
        return TemplateScoreResult(
            template=EntryTemplate.THREE_LEG_FRONT,
            policy_version=TEMPLATE_POLICY_VERSION,
            disagreement=disagreement,
            mismatch_banner=False,
            reason="tight_high_quality",
        )

    if (
        features.risk_pct <= Decimal("6")
        and features.llm_count >= 2
        and features.python_count >= 2
        and disagreement <= 1
        and dry_up in {"clearly", "somewhat"}
        and tightening
        and orderly
        and features.confidence >= 55
    ):
        return TemplateScoreResult(
            template=EntryTemplate.TWO_LEG,
            policy_version=TEMPLATE_POLICY_VERSION,
            disagreement=disagreement,
            mismatch_banner=False,
            reason="standard_quality",
        )

    return TemplateScoreResult(
        template=EntryTemplate.SINGLE,
        policy_version=TEMPLATE_POLICY_VERSION,
        disagreement=disagreement,
        mismatch_banner=False,
        reason="default_single",
    )
