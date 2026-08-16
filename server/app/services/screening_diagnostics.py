"""Read-only diagnostics over persisted personal scanner results."""

from __future__ import annotations

import math
from collections import Counter
from statistics import fmean, pstdev
from typing import Any

import pandas as pd


def _score_components(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    score = metrics.get("score") if isinstance(metrics, dict) else None
    components = score.get("components") if isinstance(score, dict) else None
    return components if isinstance(components, dict) else {}


def _scalar_raw(component: dict[str, Any]) -> float | None:
    value = component.get("raw_value")
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def build_scanner_diagnostics(
    rows: list[dict[str, Any]],
    *,
    xbrl_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    component_points: dict[str, list[float]] = {}
    raw_series: dict[str, list[float | None]] = {}
    stage2_distribution: Counter[int] = Counter()
    before_industry: Counter[str] = Counter()
    after_industry: Counter[str] = Counter()

    for row_index, row in enumerate(rows, start=1):
        metrics = dict(row.get("technical_metrics") or {})
        components = _score_components(metrics)
        stage2 = components.get("stage2", {}).get("raw_value", {})
        core_passed = stage2.get("core_checks_passed") if isinstance(stage2, dict) else None
        if isinstance(core_passed, int):
            stage2_distribution[core_passed] += 1
        industry = str(metrics.get("industry_key") or f"unknown:{row.get('symbol', '')}")
        if row_index <= 20:
            before_industry[industry] += 1
        if metrics.get("fundamental_selected"):
            after_industry[industry] += 1
        for name, component in components.items():
            if not isinstance(component, dict):
                continue
            points = component.get("points")
            if isinstance(points, (int, float)) and math.isfinite(float(points)):
                component_points.setdefault(name, []).append(float(points))
            raw_series.setdefault(name, []).append(_scalar_raw(component))

    correlations: dict[str, dict[str, float | None]] = {}
    usable_raw = {
        name: values
        for name, values in raw_series.items()
        if sum(value is not None for value in values) >= 3
    }
    for left_index, left in enumerate(sorted(usable_raw)):
        for right in sorted(usable_raw)[left_index + 1 :]:
            pairs = [
                (a, b)
                for a, b in zip(usable_raw[left], usable_raw[right], strict=False)
                if a is not None and b is not None
            ]
            if len(pairs) < 3:
                continue
            frame = pd.DataFrame(pairs, columns=[left, right])
            correlations[f"{left}__{right}"] = {
                "pearson": _finite_or_none(frame[left].corr(frame[right], method="pearson")),
                "spearman": _finite_or_none(
                    frame[left].rank(method="average").corr(
                        frame[right].rank(method="average"),
                        method="pearson",
                    )
                ),
                "samples": len(pairs),
            }

    component_stats: dict[str, dict[str, Any]] = {}
    for name, points in component_points.items():
        maxima = []
        for row in rows:
            component = _score_components(dict(row.get("technical_metrics") or {})).get(name)
            if isinstance(component, dict) and isinstance(component.get("max_points"), (int, float)):
                maxima.append(float(component["max_points"]))
        max_points = max(maxima) if maxima else 0.0
        component_stats[name] = {
            "variance": round(pstdev(points) ** 2, 6) if len(points) > 1 else 0.0,
            "mean_points": round(fmean(points), 6),
            "max_points": max_points,
            "saturation_pct": round(
                100 * sum(abs(value - max_points) <= 1e-9 for value in points) / len(points),
                2,
            ) if points and max_points > 0 else 0.0,
        }

    current_top = [str(row.get("symbol")) for row in rows[:20]]
    leave_one_out: dict[str, Any] = {}
    for component_name in component_points:
        reranked = sorted(
            rows,
            key=lambda row: (
                -(
                    float(row.get("technical_score") or 0)
                    - float(
                        _score_components(dict(row.get("technical_metrics") or {}))
                        .get(component_name, {})
                        .get("points", 0)
                    )
                ),
                -int((row.get("technical_metrics") or {}).get("rs_rating", 0)),
                float(row.get("pct_from_52w_high") or 0),
                str(row.get("symbol") or ""),
            ),
        )
        new_top = [str(row.get("symbol")) for row in reranked[:20]]
        leave_one_out[component_name] = {
            "top20_overlap": len(set(current_top) & set(new_top)),
            "symbols_added": sorted(set(new_top) - set(current_top)),
            "symbols_removed": sorted(set(current_top) - set(new_top)),
        }

    counts = xbrl_counts or {}
    total_linked = counts.get("total", 0)
    return {
        "component_correlations": correlations,
        "component_statistics": component_stats,
        "leave_one_component_out": leave_one_out,
        "stage2_survivor_distribution": dict(sorted(stage2_distribution.items())),
        "industry_concentration": {
            "technical_top20": dict(before_industry.most_common(20)),
            "fundamental_selection": dict(after_industry.most_common()),
        },
        "xbrl_coverage": {
            **counts,
            "ambiguity_pct": round(100 * counts.get("ambiguous", 0) / total_linked, 2)
            if total_linked else None,
            "missing_pct": round(100 * counts.get("missing", 0) / max(len(rows) * 2, 1), 2),
        },
    }


def _finite_or_none(value: float) -> float | None:
    return round(float(value), 6) if math.isfinite(float(value)) else None
