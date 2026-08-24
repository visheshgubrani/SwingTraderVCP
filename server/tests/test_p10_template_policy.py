import unittest
from decimal import Decimal

from app.domain.p10_sizing import EntryTemplate
from app.domain.p10_template_policy import (
    TEMPLATE_POLICY_VERSION,
    TemplateScoreFeatures,
    select_entry_template,
)


def _features(**overrides) -> TemplateScoreFeatures:
    payload = dict(
        confidence=72,
        llm_count=2,
        python_count=2,
        volume_dry_up="clearly",
        progressive_tightening="yes",
        price_action="orderly",
        climax_or_gap_violation="no",
        risk_pct=Decimal("4.0"),
    )
    payload.update(overrides)
    return TemplateScoreFeatures(**payload)


class TestP10TemplatePolicy(unittest.TestCase):
    def test_disagreement_forces_single_and_banner(self) -> None:
        result = select_entry_template(_features(llm_count=4, python_count=2))
        self.assertEqual(result.template, EntryTemplate.SINGLE)
        self.assertTrue(result.mismatch_banner)
        self.assertEqual(result.reason, "count_disagreement_gt_1")
        self.assertEqual(result.policy_version, TEMPLATE_POLICY_VERSION)

    def test_tight_high_quality_selects_three_leg_front(self) -> None:
        result = select_entry_template(_features(llm_count=3, python_count=3, confidence=80))
        self.assertEqual(result.template, EntryTemplate.THREE_LEG_FRONT)
        self.assertFalse(result.mismatch_banner)

    def test_standard_quality_selects_two_leg(self) -> None:
        result = select_entry_template(_features(volume_dry_up="somewhat"))
        self.assertEqual(result.template, EntryTemplate.TWO_LEG)

    def test_default_is_single(self) -> None:
        result = select_entry_template(_features(confidence=40, progressive_tightening="no"))
        self.assertEqual(result.template, EntryTemplate.SINGLE)
        self.assertEqual(result.reason, "default_single")
        self.assertFalse(result.mismatch_banner)

    def test_v1_never_emits_staged_or_balanced(self) -> None:
        for features in (
            _features(),
            _features(llm_count=3, python_count=3, confidence=99),
            _features(llm_count=6, python_count=1),
        ):
            result = select_entry_template(features)
            self.assertNotIn(
                result.template,
                {EntryTemplate.TWO_LEG_STAGED, EntryTemplate.THREE_LEG_BALANCED},
            )


if __name__ == "__main__":
    unittest.main()
