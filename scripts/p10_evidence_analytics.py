#!/usr/bin/env python3
"""P10 evidence analytics: chain-of-custody, dedupe, split, structural replay.

Reads `evidence/p10/` output from export_proposal_evidence.py and:
  1. Verifies each prompt-v6 attempt's stored `prompt_hash` against a
     recomputation from the exported frozen candles + the exact v6 prompt
     (materialized from git HEAD), proving the A/B replay arm uses inputs
     identical to production.
  2. Deduplicates by (source_hash, detail_image_hash, prompt_hash).
  3. Recomputes StructuralFacts + structural gates (v7 policy) per unique
     input and reports dispositions.
  4. Produces a symbol-grouped dev/holdout split and a labels skeleton.

Writes under evidence/p10/:
  unique_inputs.jsonl      one row per unique v6 input
  recomputed.jsonl         per-unique structural facts + verdict + old status
  split_dev.jsonl / split_holdout.jsonl
  labels.yaml              skeleton for human labels
  label_sheet.html         contact sheet of every unique chart

Run from the repo root with the server venv:
  cd server && ../server/.venv/bin/python ../scripts/p10_evidence_analytics.py
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import importlib.util
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "server"
sys.path.insert(0, str(SERVER))

from app.domain.p10_geometry import (  # noqa: E402
    CandleData,
    compute_structural_facts,
    derive_chart_geometry,
    evaluate_structural_gates,
    structural_facts_to_dict,
)

EVIDENCE = REPO / "evidence" / "p10"


def candle_data(row: dict) -> CandleData:
    return CandleData(
        open=float(row["o"]),
        high=float(row["h"]),
        low=float(row["l"]),
        close=float(row["c"]),
        volume=int(row["v"]),
        date=str(row["date"]),
    )


def load_candles_map() -> dict[tuple[str, str], dict]:
    out = {}
    for line in (EVIDENCE / "candles.jsonl").open():
        row = json.loads(line)
        out[(row["instrument_id"], row["as_of_date"])] = row
    return out


def load_v6_module() -> object:
    """Import the exact v6 proposal_generator from git HEAD (deployed code)."""
    text = subprocess.run(
        ["git", "-C", str(REPO), "show", "HEAD:server/app/services/proposal_generator.py"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    target = REPO / ".git" / "v6_proposal_generator_probe.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    spec = importlib.util.spec_from_file_location("v6gen_probe", target)
    module = importlib.util.module_from_spec(spec)
    sys.modules["v6gen_probe"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def structural_replay(candles_map, attempts):
    """Recompute structural facts/gates per unique (instrument, as_of)."""
    cache: dict[tuple[str, str], dict] = {}
    for attempt in attempts:
        key = (attempt.get("instrument_id"), attempt.get("as_of_date"))
        if key in cache:
            continue
        candle_row = candles_map.get(key)
        if candle_row is None:
            cache[key] = {"error": "missing candle freeze"}
            continue
        candles = [candle_data(c) for c in candle_row["candles"]]
        tick = Decimal(str(candle_row["tick_size"]))
        annotations = derive_chart_geometry(candles, tick_size=tick)
        facts = compute_structural_facts(
            candles, annotations.contractions, tick_size=tick
        )
        verdict = evaluate_structural_gates(facts)
        cache[key] = {
            "raw_count": facts.raw_count,
            "base_age_sessions": facts.base_age_sessions,
            "disposition": verdict.disposition,
            "codes": list(verdict.codes),
            "gate_details": verdict.details,
            "facts": structural_facts_to_dict(facts),
            "candidate_summary_lines": len(annotations.contractions),
        }
    return cache


def main() -> int:
    attempts = [json.loads(line) for line in (EVIDENCE / "attempts.jsonl").open()]
    candles_map = load_candles_map()
    v6 = load_v6_module()
    v6_hash_fn = getattr(v6, "proposal_prompt_hash")

    # 1) chain of custody for v6 rows
    v6_rows = [a for a in attempts if a.get("prompt_version") == "p10_vcp_proposal_v6"]
    mismatch = 0
    checked = 0
    for attempt in v6_rows:
        key = (attempt.get("instrument_id"), attempt.get("as_of_date"))
        candle_row = candles_map.get(key)
        if candle_row is None:
            continue
        candles = [candle_data(c) for c in candle_row["candles"]]
        tick = Decimal(str(candle_row["tick_size"]))
        try:
            annotations = derive_chart_geometry(candles, tick_size=tick)
            summary = v6_module_summary(v6, annotations.contractions)
            recomputed = v6_hash_fn(tick_size=tick, candidate_summary=summary)
        except Exception as exc:  # noqa: BLE001
            attempt["chain_of_custody"] = {"ok": False, "error": str(exc)[:300]}
            mismatch += 1
            continue
        checked += 1
        ok = recomputed == attempt.get("prompt_hash")
        attempt["chain_of_custody"] = {
            "ok": ok,
            "recomputed_prompt_hash": recomputed,
        }
        if not ok:
            mismatch += 1
    print(f"chain-of-custody: checked={checked} mismatches={mismatch}")

    # 2) dedupe unique inputs (v6 only for the primary corpus)
    def dedupe_key(a: dict) -> tuple[str, str, str]:
        return (
            a.get("source_hash", ""),
            a.get("detail_image_hash", ""),
            a.get("prompt_hash", ""),
        )

    unique: dict[tuple, dict] = {}
    for attempt in sorted(v6_rows, key=lambda a: a.get("started_at", "")):
        key = dedupe_key(attempt)
        current = unique.get(key)
        if current is None or attempt["started_at"] > current["started_at"]:
            unique[key] = attempt

    uniq_rows = list(unique.values())
    print(f"unique v6 inputs: {len(uniq_rows)} (rows {len(v6_rows)})")

    # per-unique status aggregation
    per_key_status: dict[tuple, list[str]] = {}
    for attempt in v6_rows:
        per_key_status.setdefault(dedupe_key(attempt), []).append(attempt["status"])
    for row in uniq_rows:
        statuses = per_key_status[dedupe_key(row)]
        row["terminal_statuses"] = sorted(set(statuses))
        row["old_classification"] = None
        so = row.get("structured_output")
        if so:
            try:
                row["old_classification"] = json.loads(so).get("classification")
            except (json.JSONDecodeError, AttributeError):
                pass
        row.pop("structured_output", None)

    # 3) structural replay
    replay_cache = structural_replay(candles_map, uniq_rows)
    for row in uniq_rows:
        replay = replay_cache.get(
            (row.get("instrument_id"), row.get("as_of_date")),
            {"error": "missing candles"},
        )
        row["structural"] = replay

    out_rows = []
    for row in uniq_rows:
        out = {
            "attempt_id": row["id"],
            "instrument_id": row.get("instrument_id"),
            "symbol": row["symbol"],
            "as_of_date": row["as_of_date"],
            "status": row["status"],
            "error_type": row.get("error_type"),
            "error_message": row.get("error_message"),
            "old_classification": row["old_classification"],
            "terminal_statuses": row["terminal_statuses"],
            "prompt_hash": row.get("prompt_hash"),
            "source_hash": row.get("source_hash"),
            "chain_of_custody": row.get("chain_of_custody"),
            "structural": row["structural"],
            "detail_image_file": row.get("detail_image_file"),
            "context_image_file": row.get("context_image_file"),
            "started_at": row.get("started_at"),
        }
        out_rows.append(out)

    with (EVIDENCE / "unique_inputs.jsonl").open("w") as fh:
        for row in out_rows:
            fh.write(json.dumps(row) + "\n")

    # 4) symbol-grouped 70/30 split
    symbols = sorted({r["symbol"] for r in out_rows})
    split_index = max(1, round(len(symbols) * 0.7))
    dev_symbols = set(symbols[:split_index])
    dev, holdout = [], []
    for row in out_rows:
        (dev if row["symbol"] in dev_symbols else holdout).append(row)
    for name, rows in (("split_dev", dev), ("split_holdout", holdout)):
        with (EVIDENCE / f"{name}.jsonl").open("w") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
    print(f"split: dev={len(dev)} holdout={len(holdout)} (symbols {len(dev_symbols)}/{len(symbols)})")

    # 5) labels skeleton
    labels = []
    for row in out_rows:
        labels.append(
            {
                "key": f"{row['symbol']}|{row['as_of_date']}",
                "symbol": row["symbol"],
                "as_of_date": row["as_of_date"],
                "split": "dev" if row in dev else "holdout",
                "old_classification": row["old_classification"],
                "old_status": row["status"],
                "structural_disposition": row["structural"].get("disposition"),
                "structural_codes": row["structural"].get("codes", []),
                "label": None,
                "note": "",
            }
        )
    with (EVIDENCE / "labels.yaml").open("w") as fh:
        fh.write("# P10 truth-set labels. Set `label` to one of: valid | forming | not_vcp\n")
        for item in labels:
            fh.write(
                f"- key: {item['key']}\n  symbol: {item['symbol']}\n"
                f"  as_of_date: {item['as_of_date']}\n  split: {item['split']}\n"
                f"  old_classification: {item['old_classification']}\n"
                f"  old_status: {item['old_status']}\n"
                f"  structural_disposition: {item['structural_disposition']}\n"
                f"  label: null\n  note: ''\n"
            )

    # 6) contact sheet
    html = [
        "<!doctype html><html><head><meta charset='utf-8'><title>P10 label sheet</title></head>",
        "<body style='font-family:sans-serif'>",
        "<h1>P10 unique v6 inputs</h1>",
    ]
    for row in out_rows:
        html.append(
            f"<div style='border:1px solid #ccc;margin:8px;padding:8px;page-break-inside:avoid'>"
            f"<b>{row['symbol']}</b> asof {row['as_of_date']} — old={row['old_classification']}/{row['status']} "
            f"structural={row['structural'].get('disposition')} "
            f"<span style='color:#777'>({row['structural'].get('codes')})</span><br>"
            f"<img src='images/{row['detail_image_file'].split('/')[-1]}' style='max-width:920px'></div>"
        )
    html.append("</body></html>")
    (EVIDENCE / "label_sheet.html").write_text("\n".join(html))

    # summary
    from collections import Counter

    old_cls = Counter(r["old_classification"] for r in out_rows)
    struct = Counter(r["structural"].get("disposition") for r in out_rows)
    print("old classifications:", dict(old_cls))
    print("structural dispositions:", dict(struct))
    return 0


def v6_module_summary(v6_module, contractions) -> str:
    """The v6 summary formatter (module imported from git HEAD)."""
    return v6_module.format_candidate_summary(contractions)


if __name__ == "__main__":
    raise SystemExit(main())
