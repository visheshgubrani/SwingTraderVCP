"""Versioned, deterministic P7 fundamental assessment rules.

The assessment is deliberately Minervini-inspired rather than a claim of a
complete SEPA screen. Upstox does not expose the quarterly EPS/YoY history
needed for an exact implementation, so unavailable metrics reduce coverage
and never become negative evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

CriterionStatus = Literal["positive", "negative", "mixed", "unknown", "not_applicable"]
LegacyVerdict = Literal["pass", "fail", "uncertain"]
AssessmentGrade = Literal["A", "B", "C", "D", "insufficient"]

RUBRIC_VERSION = "minervini_inspired_v1"
FACTS_SCHEMA_VERSION = "fundamental_facts_v3"


@dataclass(frozen=True)
class RuleCriterion:
    name: str
    status: CriterionStatus
    explanation: str
    evidence_keys: list[str]


@dataclass(frozen=True)
class MetricRule:
    key: str
    label: str
    weight: float
    low: float
    high: float
    evidence_keys: tuple[str, ...]
    unit: str
    unavailable_reason: str
    applicable: bool = True


COMPONENT_WEIGHTS: dict[str, float] = {
    "earnings": 40.0,
    "sales": 20.0,
    "profitability": 20.0,
    "cash_conversion": 10.0,
    "sponsorship": 10.0,
}

_PROVIDER_LIMITATIONS = [
    "quarterly_eps_yoy",
    "quarterly_sales_yoy",
    "debt_to_equity",
    "promoter_pledge",
]


def _value(facts: dict[str, Any], key: str) -> float | None:
    evidence = facts.get("evidence")
    item = evidence.get(key) if isinstance(evidence, dict) else None
    raw = item.get("value") if isinstance(item, dict) else None
    if isinstance(raw, dict):
        for candidate in (
            "value_pct",
            "value",
            "ratio",
            "change_percentage_points",
        ):
            value = raw.get(candidate)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    return None


def _ratio(facts: dict[str, Any], name: str) -> float | None:
    raw = facts.get("ratios", {}).get(name, {}) if isinstance(facts.get("ratios"), dict) else {}
    if not isinstance(raw, dict):
        return None
    value = raw.get("company")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _history_count(facts: dict[str, Any], scope: str, metric: str) -> int:
    histories = facts.get("histories")
    values = histories.get(scope, {}).get(metric) if isinstance(histories, dict) else None
    return len(values) if isinstance(values, list) else 0


def _criterion(name: str, status: CriterionStatus, explanation: str, *keys: str) -> RuleCriterion:
    return RuleCriterion(name, status, explanation, [key for key in keys if key])


def _clamp_points(value: float, rule: MetricRule) -> float:
    if rule.high <= rule.low:
        return rule.weight
    ratio = (value - rule.low) / (rule.high - rule.low)
    return round(rule.weight * max(0.0, min(1.0, ratio)), 2)


def _metric_status(value: float | None, *, red_flag: bool = False, strong_at: float | None = None) -> CriterionStatus:
    if value is None:
        return "unknown"
    if red_flag:
        return "negative"
    if strong_at is not None and value >= strong_at:
        return "positive"
    return "mixed"


def _ownership_value(facts: dict[str, Any], names: tuple[str, ...]) -> float | None:
    evidence = facts.get("evidence")
    if not isinstance(evidence, dict):
        return None
    values: list[float] = []
    for key, item in evidence.items():
        if not isinstance(key, str) or not key.startswith("ownership.") or not isinstance(item, dict):
            continue
        normalized = key.casefold()
        if any(name in normalized for name in names):
            raw = item.get("value")
            if isinstance(raw, dict):
                raw = raw.get("change_percentage_points")
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                values.append(float(raw))
    return sum(values) if values else None


def _metric(
    rule: MetricRule,
    value: float | None,
    *,
    red_flag: bool = False,
    strong_at: float | None = None,
) -> dict[str, Any]:
    points = _clamp_points(value, rule) if value is not None and rule.applicable else 0.0
    status = "not_applicable" if not rule.applicable else _metric_status(value, red_flag=red_flag, strong_at=strong_at)
    return {
        "key": rule.key,
        "label": rule.label,
        "value": value,
        "unit": rule.unit,
        "weight": rule.weight,
        "points": points,
        "available": value is not None,
        "status": status,
        "evidence_keys": list(rule.evidence_keys),
        "unavailable_reason": "Not applicable to this sector." if not rule.applicable else (None if value is not None else rule.unavailable_reason),
    }


def _component(name: str, metrics: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": name,
        "earned_points": round(sum(float(metric["points"]) for metric in metrics), 2),
        "available_points": round(sum(float(metric["weight"]) for metric in metrics if metric["available"]), 2),
        "max_points": COMPONENT_WEIGHTS[name],
        "metrics": metrics,
    }


def _legacy_criteria(components: list[dict[str, Any]], red_flags: list[str]) -> list[RuleCriterion]:
    red_flag_metric_keys = {
        "latest_annual_eps_decline": "latest_annual_eps_growth",
        "non_positive_eps_cagr": "annual_eps_cagr",
        "non_positive_revenue_cagr": "annual_revenue_cagr",
        "annual_margin_compression": "annual_margin_change",
        "weak_cash_conversion": "cash_conversion",
        "low_returns": "roe",
        "promoter_reduction": "promoter_stability",
        "institutional_selling": "institutional_change",
    }
    flagged_metric_keys = {red_flag_metric_keys.get(flag, flag) for flag in red_flags}
    criteria: list[RuleCriterion] = []
    for component in components:
        metrics = component["metrics"]
        available = [metric for metric in metrics if metric["available"]]
        if not available:
            status: CriterionStatus = "not_applicable" if component["name"] == "cash_conversion" and component["max_points"] == 0 else "unknown"
            explanation = "No supported data is available for this component."
        elif any(metric["key"] in flagged_metric_keys for metric in metrics):
            status = "negative"
            explanation = "At least one material deterioration flag is present."
        else:
            earned = float(component["earned_points"])
            available_points = float(component["available_points"])
            ratio = earned / available_points if available_points else 0
            status = "positive" if ratio >= 0.8 else ("mixed" if ratio > 0.35 else "negative")
            explanation = "Supported metrics are strong." if status == "positive" else "Supported metrics are mixed or incomplete."
        keys = [key for metric in metrics for key in metric["evidence_keys"]]
        criteria.append(_criterion(component["name"], status, explanation, *keys))
    return criteria


def score_minervini_inspired(facts: dict[str, Any]) -> dict[str, Any]:
    """Return the authoritative, coverage-aware fundamental assessment."""

    financial = bool(facts.get("company", {}).get("is_financial_sector"))
    annual_eps_count = _history_count(facts, "annual", "basic_eps")
    annual_revenue_count = _history_count(facts, "annual", "revenue")
    annual_pat_count = _history_count(facts, "annual", "net_profit")

    rules = {
        "earnings": [
            MetricRule("quarterly_eps_yoy", "Quarterly EPS YoY", 15, 0, 25, ("growth.latest_quarter_eps_yoy",), "percent", "Upstox does not provide quarterly EPS history."),
            MetricRule("annual_eps_cagr", "Annual EPS CAGR", 10, 0, 25, ("growth.annual_eps_cagr",), "percent", "At least two dated annual EPS values are required."),
            MetricRule("latest_annual_eps_growth", "Latest annual EPS growth", 10, 0, 25, ("growth.latest_annual_eps_yoy",), "percent", "A comparable prior-year annual EPS value is required."),
            MetricRule("annual_pat_cagr", "Annual PAT CAGR", 5, 0, 25, ("growth.annual_net_profit_cagr",), "percent", "At least two dated annual PAT values are required."),
        ],
        "sales": [
            MetricRule("quarterly_sales_yoy", "Quarterly sales YoY", 8, 0, 20, ("growth.latest_quarter_revenue_yoy",), "percent", "A matching prior-year quarter is not returned in the current Upstox history."),
            MetricRule("annual_revenue_cagr", "Annual revenue CAGR", 6, 0, 20, ("growth.annual_revenue_cagr",), "percent", "At least two dated annual revenue values are required."),
            MetricRule("latest_annual_revenue_growth", "Latest annual revenue growth", 6, 0, 20, ("growth.latest_annual_revenue_yoy",), "percent", "A comparable prior-year annual revenue value is required."),
        ],
        "profitability": [
            MetricRule("roe", "Return on equity", 8, 8, 20, ("ratios.roe",), "percent", "Upstox did not return ROE."),
            MetricRule("roce", "Return on capital employed", 7, 8, 20, ("ratios.roce",), "percent", "Upstox did not return ROCE."),
            MetricRule("annual_margin_change", "Annual operating-margin change", 5, -2, 1, ("margins.latest_annual_yoy_change",), "percentage_points", "Comparable annual operating margins are required."),
        ],
        "cash_conversion": [
            MetricRule("cash_conversion", "CFO / PAT", 10, 0.5, 1.0, ("quality.cash_from_operations_to_pat_3y",), "ratio", "Operating cash flow and PAT history are unavailable.", applicable=not financial),
        ],
        "sponsorship": [
            MetricRule("institutional_change", "Institutional holding change", 6, -3, 3, ("ownership.institutional_change",), "percentage_points", "Comparable FII/MF/DII holdings are unavailable."),
            MetricRule("promoter_stability", "Promoter stability", 4, -2, 0, ("ownership.promoters_change",), "percentage_points", "Comparable promoter holdings are unavailable."),
        ],
    }

    values = {
        "quarterly_eps_yoy": _value(facts, "growth.latest_quarter_eps_yoy"),
        "annual_eps_cagr": _value(facts, "growth.annual_eps_cagr"),
        "latest_annual_eps_growth": _value(facts, "growth.latest_annual_eps_yoy"),
        "annual_pat_cagr": _value(facts, "growth.annual_net_profit_cagr"),
        "quarterly_sales_yoy": _value(facts, "growth.latest_quarter_revenue_yoy"),
        "annual_revenue_cagr": _value(facts, "growth.annual_revenue_cagr"),
        "latest_annual_revenue_growth": _value(facts, "growth.latest_annual_revenue_yoy"),
        "roe": _ratio(facts, "roe"),
        "roce": _ratio(facts, "roce"),
        "annual_margin_change": _value(facts, "margins.latest_annual_yoy_change"),
        "cash_conversion": _value(facts, "quality.cash_from_operations_to_pat_3y") if not financial else None,
        "institutional_change": _ownership_value(facts, ("institutional", "fii", "foreign", "mutual", "dii")),
        "promoter_stability": _ownership_value(facts, ("promoter",)),
    }

    red_flags: list[str] = []
    if values["latest_annual_eps_growth"] is not None and values["latest_annual_eps_growth"] <= -20:
        red_flags.append("latest_annual_eps_decline")
    if values["annual_eps_cagr"] is not None and values["annual_eps_cagr"] <= 0:
        red_flags.append("non_positive_eps_cagr")
    if values["annual_revenue_cagr"] is not None and values["annual_revenue_cagr"] <= 0:
        red_flags.append("non_positive_revenue_cagr")
    if values["annual_margin_change"] is not None and values["annual_margin_change"] <= -2:
        red_flags.append("annual_margin_compression")
    if values["cash_conversion"] is not None and values["cash_conversion"] < 0.5:
        red_flags.append("weak_cash_conversion")
    if values["roe"] is not None and values["roce"] is not None and values["roe"] < 10 and values["roce"] < 10:
        red_flags.append("low_returns")
    if values["promoter_stability"] is not None and values["promoter_stability"] <= -2:
        red_flags.append("promoter_reduction")
    if values["institutional_change"] is not None and values["institutional_change"] <= -3:
        red_flags.append("institutional_selling")

    components: list[dict[str, Any]] = []
    applicable_weight = 0.0
    available_weight = 0.0
    earned_points = 0.0
    for name, component_rules in rules.items():
        metrics: list[dict[str, Any]] = []
        for rule in component_rules:
            if rule.applicable:
                applicable_weight += rule.weight
            metric = _metric(
                rule,
                values[rule.key] if rule.applicable else None,
                red_flag=rule.key in red_flags,
                strong_at=rule.high,
            )
            if rule.applicable and metric["available"]:
                available_weight += rule.weight
                earned_points += float(metric["points"])
            metrics.append(metric)
        component = _component(name, metrics)
        if not any(rule.applicable for rule in component_rules):
            component["available_points"] = 0.0
            component["max_points"] = 0.0
        components.append(component)

    coverage_pct = round((available_weight / applicable_weight) * 100, 2) if applicable_weight else 0.0
    core_sufficient = min(annual_eps_count, annual_revenue_count, annual_pat_count) >= 3
    insufficient_reason = None
    if coverage_pct < 50:
        insufficient_reason = "Less than half of the applicable rubric has provider data."
    elif not core_sufficient:
        insufficient_reason = "At least three annual EPS, revenue, and PAT observations are required."
    grade: AssessmentGrade
    score: float | None
    if insufficient_reason:
        grade = "insufficient"
        score = None
    else:
        score = round((earned_points / available_weight) * 100, 2) if available_weight else None
        grade = "A" if score is not None and score >= 80 else "B" if score is not None and score >= 65 else "C" if score is not None and score >= 50 else "D"

    criteria = _legacy_criteria(components, red_flags)
    return {
        "rubric_version": RUBRIC_VERSION,
        "score": score,
        "grade": grade,
        "coverage_pct": coverage_pct,
        "earned_points": round(earned_points, 2),
        "available_points": round(available_weight, 2),
        "max_points": round(applicable_weight, 2),
        "core_sufficient": core_sufficient,
        "insufficient_reason": insufficient_reason,
        "components": components,
        "criteria": [criterion.__dict__ for criterion in criteria],
        "red_flags": red_flags,
        "provider_limitations": list(dict.fromkeys([*facts.get("provider_limitations", []), *_PROVIDER_LIMITATIONS])),
        # New v3 results intentionally leave legacy verdict fields null. This
        # field is retained only for callers/tests of the old rule API.
        "verdict": None,
    }


def score_balanced_sepa(facts: dict[str, Any]) -> dict[str, Any]:
    """Compatibility entry point retained for historical callers.

    New P7 jobs use :func:`score_minervini_inspired`; this alias makes the
    public service import stable while returning the new authoritative shape.
    """

    scorecard = score_minervini_inspired(facts)
    scorecard["rubric_version"] = "balanced_sepa_v2"
    return scorecard
