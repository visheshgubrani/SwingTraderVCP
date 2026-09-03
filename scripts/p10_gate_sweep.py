#!/usr/bin/env python3
"""Gate threshold sweep over the exported unique inputs (dev + holdout).

Every variant is scored with the same cached StructuralFacts; only gate
parameters vary. Outputs a variant-by-variant disposition table and the
named-anchor outcomes (FLUOROCHEM/HONASA/NEULANDLAB/PPLPHARMA/SYRMA/
LALPATHLAB/TORNTPHARM) so defaults can be picked from data, not by hand.
Tuning considerations may only use the dev split; holdout is reported.

Run: cd server && .venv/bin/python ../scripts/p10_gate_sweep.py
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "server"
sys.path.insert(0, str(SERVER))
sys.path.insert(0, str(REPO / "scripts"))

from app.domain.p10_geometry import (  # noqa: E402
    evaluate_structural_gates,
)

ANCHORS = {
    "NSE:FLUOROCHEM-EQ": "forming/developing",
    "NSE:PPLPHARMA-EQ": "forming/developing",
    "NSE:NEULANDLAB-EQ": "not_vcp",
    "NSE:HONASA-EQ": "not_vcp (breaking down)",
    "NSE:SYRMA-EQ": "not_vcp (breaking down)",
    "NSE:LALPATHLAB-EQ": "not_vcp or breaking down",
    "NSE:TORNTPHARM-EQ": "valid",
}

VARIANTS = [
    {"name": "defaults", "maturity": 15, "undercut_atr": "0.10",
     "ratio": "0.90", "step_pp": "0.75", "max_down": "1.75",
     "mean_prev": "1.10", "min_adv": "0.75", "climax": "1.75"},
    {"name": "maturity_12", "maturity": 12, "undercut_atr": "0.10",
     "ratio": "0.90", "step_pp": "0.75", "max_down": "1.75",
     "mean_prev": "1.10", "min_adv": "0.75", "climax": "1.75"},
    {"name": "maturity_18", "maturity": 18, "undercut_atr": "0.10",
     "ratio": "0.90", "step_pp": "0.75", "max_down": "1.75",
     "mean_prev": "1.10", "min_adv": "0.75", "climax": "1.75"},
    {"name": "undercut_005", "maturity": 15, "undercut_atr": "0.05",
     "ratio": "0.90", "step_pp": "0.75", "max_down": "1.75",
     "mean_prev": "1.10", "min_adv": "0.75", "climax": "1.75"},
    {"name": "undercut_020", "maturity": 15, "undercut_atr": "0.20",
     "ratio": "0.90", "step_pp": "0.75", "max_down": "1.75",
     "mean_prev": "1.10", "min_adv": "0.75", "climax": "1.75"},
    {"name": "tighten_095_075", "maturity": 15, "undercut_atr": "0.10",
     "ratio": "0.95", "step_pp": "0.75", "max_down": "1.75",
     "mean_prev": "1.10", "min_adv": "0.75", "climax": "1.75"},
    {"name": "tighten_090_050", "maturity": 15, "undercut_atr": "0.10",
     "ratio": "0.90", "step_pp": "0.50", "max_down": "1.75",
     "mean_prev": "1.10", "min_adv": "0.75", "climax": "1.75"},
    {"name": "pb_maxdown_150", "maturity": 15, "undercut_atr": "0.10",
     "ratio": "0.90", "step_pp": "0.75", "max_down": "1.50",
     "mean_prev": "1.10", "min_adv": "0.75", "climax": "1.75"},
    {"name": "pb_maxdown_200", "maturity": 15, "undercut_atr": "0.10",
     "ratio": "0.90", "step_pp": "0.75", "max_down": "2.00",
     "mean_prev": "1.10", "min_adv": "0.75", "climax": "1.75"},
    {"name": "pb_mean_125", "maturity": 15, "undercut_atr": "0.10",
     "ratio": "0.90", "step_pp": "0.75", "max_down": "1.75",
     "mean_prev": "1.25", "min_adv": "0.75", "climax": "1.75"},
    {"name": "climax_200", "maturity": 15, "undercut_atr": "0.10",
     "ratio": "0.90", "step_pp": "0.75", "max_down": "1.75",
     "mean_prev": "1.10", "min_adv": "0.75", "climax": "2.00"},
    {"name": "climax_150", "maturity": 15, "undercut_atr": "0.10",
     "ratio": "0.90", "step_pp": "0.75", "max_down": "1.75",
     "mean_prev": "1.10", "min_adv": "0.75", "climax": "1.50"},
]

# Factors: deep (non-noise) waves set by the facts cache computed in analytics
# with NOISE_DEPTH_PCT fixed at the default 1.25%. Only evaluate() params vary.
from app.domain.p10_geometry import structural_facts_to_dict  # noqa: E402


def cached_facts_by_key() -> dict[tuple, dict]:
    """Recompute facts per (instrument_id, as_of) once."""
    import json as _json

    facts_cache: dict[tuple, dict] = {}
    # reuse analytics module caches by recompute here from candles
    candles: dict[tuple, dict] = {}
    for line in (REPO / "evidence" / "p10" / "candles.jsonl").open():
        row = _json.loads(line)
        candles[(row["instrument_id"], row["as_of_date"])] = row

    from app.domain.p10_geometry import (
        CandleData,
        compute_structural_facts,
        derive_chart_geometry,
    )

    def to_cd(r: dict) -> CandleData:
        return CandleData(
            open=float(r["o"]), high=float(r["h"]), low=float(r["l"]),
            close=float(r["c"]), volume=int(r["v"]), date=str(r["date"]),
        )

    for key, row in candles.items():
        cds = [to_cd(c) for c in row["candles"]]
        tick = Decimal(str(row["tick_size"]))
        ann = derive_chart_geometry(cds, tick_size=tick)
        facts_cache[key] = compute_structural_facts(
            cds, ann.contractions, tick_size=tick
        )
    return facts_cache


def main() -> int:
    rows = [
        json.loads(line)
        for line in (REPO / "evidence" / "p10" / "unique_inputs.jsonl").open()
    ]
    facts_cache = cached_facts_by_key()
    print(f"rows={len(rows)} facts_cached={len(facts_cache)}")

    results = []
    for variant in VARIANTS:
        verdicts = []
        anchor_out: dict[str, str] = {}
        for row in rows:
            key = (row.get("instrument_id"), row.get("as_of_date"))
            facts = facts_cache.get(key)
            if facts is None:
                verdicts.append("missing")
                continue
            v = evaluate_structural_gates(
                facts,
                maturity_floor_sessions=variant["maturity"],
                undercut_tolerance_atr=Decimal(variant["undercut_atr"]),
                tightening_ratio_max=Decimal(variant["ratio"]),
                tightening_step_pp_min=Decimal(variant["step_pp"]),
                pullback_max_down_adv_ratio=Decimal(variant["max_down"]),
                pullback_mean_prev_ratio=Decimal(variant["mean_prev"]),
                pullback_min_adv_ratio=Decimal(variant["min_adv"]),
                pivot_climax_vol_ratio=Decimal(variant["climax"]),
            )
            verdicts.append(v.disposition)
            if row["symbol"] in ANCHORS:
                anchor_out[row["symbol"]] = v.disposition
        results.append({"variant": variant["name"], "verdicts": verdicts, "anchors": anchor_out})

    from collections import Counter

    lines = []
    lines.append("variant | ok | forming | invalid | anchor outcomes")
    lines.append("--------|----|---------|---------|----------------")
    for res in results:
        counts = Counter(res["verdicts"])
        anchor_txt = "; ".join(
            f"{s.replace('NSE:','').replace('-EQ','')}:{d}"
            for s, d in sorted(res["anchors"].items())
        )
        lines.append(
            f"{res['variant']:<12} | {counts.get('ok',0):<4} | {counts.get('forming',0):<7} "
            f"| {counts.get('invalid',0):<7} | {anchor_txt}"
        )

    text = "\n".join(lines)
    print(text)
    out = REPO / "evidence" / "p10" / "gate_sweep_report.txt"
    out.write_text(text + "\n")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
