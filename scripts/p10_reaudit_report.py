#!/usr/bin/env python3
"""A/B report over evidence/p10/reaudit_ab.jsonl.

Per-arm classification summaries, agreement crosstabs, flag calibration
(volume_dry_up / confidence), candidate-action usage, structural-gate
alignment, named-anchor outcomes, and (once evidence/p10/labels.yaml is
filled) confusion matrices + acceptance metrics for model-level and
final-pipeline outcomes.

Run: cd server && .venv/bin/python ../scripts/p10_reaudit_report.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "evidence" / "p10"

ANCHOR_EXPECTED = {
    "NSE:FLUOROCHEM-EQ": "forming",
    "NSE:PPLPHARMA-EQ": "forming",
    "NSE:NEULANDLAB-EQ": "not_vcp",
    "NSE:HONASA-EQ": "not_vcp",
    "NSE:SYRMA-EQ": "not_vcp",
    "NSE:LALPATHLAB-EQ": "not_vcp",
    "NSE:TORNTPHARM-EQ": "valid",
}

STRUCTURAL_CODES_FOR_ANCHORS = {
    "NSE:NEULANDLAB-EQ": {"structural_flat_shelf_not_tightening"},
    "NSE:HONASA-EQ": {"structural_undercut_lower_low"},
    "NSE:SYRMA-EQ": {"structural_undercut_lower_low",
                     "structural_final_pullback_distribution"},
    "NSE:LALPATHLAB-EQ": {"structural_final_pullback_distribution"},
}


def load_calls() -> list[dict]:
    path = EVIDENCE / "reaudit_ab.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.open()]


def load_old() -> dict[str, dict]:
    out = {}
    for line in (EVIDENCE / "unique_inputs.jsonl").open():
        row = json.loads(line)
        out[f"{row['symbol']}|{row['as_of_date']}"] = row
    return out


def load_labels() -> dict[str, str]:
    out = {}
    path = EVIDENCE / "labels.yaml"
    if not path.exists():
        return out
    current_key = None
    for line in path.open():
        line = line.strip()
        if line.startswith("- key:"):
            current_key = line.split(":", 1)[1].strip()
        elif line.startswith("label:") and current_key:
            val = line.split(":", 1)[1].strip()
            if val and val.lower() != "null":
                out[current_key] = val
    return out


def pipeline_outcome(classification: str | None, structural: dict) -> str:
    if classification is None:
        return "failed"
    if classification == "not_vcp":
        return "rejected"
    if classification == "forming":
        return "watch"
    # valid
    disp = structural.get("disposition")
    if disp == "invalid":
        return "rejected"  # hard structural invalidation (inference skipped in prod)
    if disp == "forming":
        return "watch"
    return "proposal"


def main() -> int:
    calls = load_calls()
    old = load_old()
    labels = load_labels()
    if not calls:
        print("no A/B calls yet")
        return 1
    print(f"calls={len(calls)} labeled={len(labels)}")
    arms = sorted({c["arm"] for c in calls})

    for arm in arms:
        rows = [c for c in calls if c["arm"] == arm]
        ok = [c for c in rows if c.get("ok")]
        cls = Counter(c.get("classification") for c in ok)
        dry = Counter(c.get("volume_dry_up") for c in ok)
        conf_ok = [c.get("confidence", 0) for c in ok if c.get("classification") == "valid"]
        merges = sum(c.get("merge_rows", 0) for c in ok)
        rejects = sum(c.get("reject_rows", 0) for c in ok)
        extras = sum(c.get("extra_windows", 0) for c in ok)
        total_llm_rows = sum(c.get("llm_candidate_rows", 0) for c in ok)
        costs = [float(c.get("cost") or 0) for c in ok]
        lat = [c.get("latency_s", 0) for c in ok]
        lat_sorted = sorted(lat)
        p95 = lat_sorted[int(len(lat_sorted) * 0.95)] if lat_sorted else 0
        mean_conf = sum(conf_ok) / len(conf_ok) if conf_ok else 0
        print(f"\n=== arm {arm}: calls={len(rows)} ok={len(ok)} ===")
        print(f"classification: {dict(cls)}")
        print(f"volume_dry_up: {dict(dry)}")
        print(f"valid conf mean={mean_conf:.0f} | merge={merges} reject={rejects} "
              f"extra={extras} (candidate rows {total_llm_rows})")
        print(f"cost total=${sum(costs):.3f} mean=${(sum(costs)/len(costs) if costs else 0):.4f} "
              f"| latency mean={sum(lat)/len(lat):.1f}s p95={p95:.1f}s")

        # alignment with structural gates (valid-only rows)
        valid_rows = [c for c in ok if c.get("classification") == "valid"]
        struct_ok = sum(
            1 for c in valid_rows if c.get("structural_disposition") == "ok"
        )
        struct_forming = sum(
            1 for c in valid_rows if c.get("structural_disposition") == "forming"
        )
        struct_invalid = sum(
            1 for c in valid_rows if c.get("structural_disposition") == "invalid"
        )
        print(
            f"valid rows by structural disposition: ok={struct_ok} forming={struct_forming} "
            f"invalid={struct_invalid}"
        )

    # agreement crosstab flash_v7 vs strong_v7
    def by_arm(arm: str) -> dict[str, str]:
        out = {}
        for c in calls:
            if c["arm"] == arm and c.get("ok"):
                out[c["key"]] = c.get("classification")
        return out

    a, b = by_arm("flash_v7"), by_arm("strong_v7")
    shared = sorted(set(a) & set(b))
    if shared:
        agree = sum(1 for k in shared if a[k] == b[k])
        print(f"\nflash_v7 vs strong_v7 (dev, n={len(shared)}): agreement={agree} "
              f"({agree/len(shared):.0%})")
        for cls_a in ("valid", "forming", "not_vcp"):
            for cls_b in ("valid", "forming", "not_vcp"):
                n = sum(1 for k in shared if a[k] == cls_a and b[k] == cls_b)
                if n:
                    print(f"  flash_v7={cls_a:<8} x strong_v7={cls_b:<8}: {n}")

    # named anchors
    print("\n=== named anchors (expected in parentheses) ===")
    for arm in arms:
        row_out = []
        for symbol, expected in sorted(ANCHOR_EXPECTED.items()):
            got = [c for c in calls if c["arm"] == arm and c["symbol"] == symbol and c.get("ok")]
            if not got:
                row_out.append(f"{symbol.replace('NSE:','').replace('-EQ','')}:none")
                continue
            counts = Counter(c.get("classification") for c in got)
            top = counts.most_common(1)[0][0]
            mark = "OK " if top == expected else "xx "
            row_out.append(f"{mark}{symbol.replace('NSE:','').replace('-EQ','')}:{top}")
        print(f"{arm:<10} " + " ".join(row_out))

    # structural gate agreement vs deterministic codes on anchor invalids
    print("\nstructural gates on named invalids (expect invalid):")
    for symbol, codes in STRUCTURAL_CODES_FOR_ANCHORS.items():
        hits = [c for c in calls if c["symbol"] == symbol and c.get("ok")]
        for c in hits[:1]:
            print(
                f"  {symbol.replace('NSE:','').replace('-EQ','')} "
                f"struct={c['structural_disposition']} codes={c['structural_codes']}"
            )

    # labels (optional)
    if labels:
        print(f"\n=== labels vs arms (n={len(labels)}) ===")
        for arm in arms:
            rows = [c for c in calls if c["arm"] == arm and c.get("ok") and c["key"] in labels]
            if not rows:
                continue
            cm = Counter((labels[c["key"]], c.get("classification")) for c in rows)
            print(f"arm {arm}:")
            for (truth, pred), n in sorted(cm.items()):
                print(f"  truth={truth:<8} pred={pred:<8}: {n}")
            # pipeline metrics
            valid_truth = sum(1 for c in rows if labels[c["key"]] == "valid")
            invalid_truth = sum(1 for c in rows if labels[c["key"]] != "valid")
            pipe = Counter(pipeline_outcome(c.get("classification"), {"disposition": c.get("structural_disposition")}) for c in rows)
            print(f"  pipeline outcomes: {dict(pipe)} (truth valid={valid_truth} non-valid={invalid_truth})")
    else:
        print("\n(labels.yaml has no labels yet — confusion matrices pending user labels)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
