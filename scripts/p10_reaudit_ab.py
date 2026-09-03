#!/usr/bin/env python3
"""Three-arm re-audit of the frozen P10 charts (evaluation only).

Arms (identical frozen images + production request builders):
  1. flash_v6  - google/gemini-3.7-flash with the exact v6 prompt/schema
                 (module materialized from git HEAD) — contemporaneous replay.
  2. flash_v7  - google/gemini-3.7-flash with v7 prompt/schema + facts.
  3. strong_v7 - AUDIT_MODEL (e.g. google/gemini-3.1-pro-preview) with v7.

Reads evidence/p10 (attempts, candles, unique_inputs, splits), writes
evidence/p10/reaudit_ab.jsonl (append, one JSON per call). The OpenRouter
key is read from evidence/.secrets/openrouter.key (gitignored) and never
printed. No DB writes, no money path.

Usage (from repo root with the server venv):
  cd server && .venv/bin/python ../scripts/p10_reaudit_ab.py \
      --split dev --arms flash_v6,flash_v7,strong_v7 [--limit N]
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "server"
sys.path.insert(0, str(SERVER))
EVIDENCE = REPO / "evidence" / "p10"

KEY_FILE = REPO / "evidence" / ".secrets" / "openrouter.key"
AUDIT_MODEL = os.environ.get("AUDIT_MODEL", "google/gemini-3.1-pro-preview")

os.environ["APP_ENVIRONMENT"] = "development"
key = KEY_FILE.read_text().strip()
if not key:
    raise SystemExit("missing evidence/.secrets/openrouter.key")
os.environ["OPENROUTER_API_KEY"] = key

import httpx  # noqa: E402

from app.domain.p10_geometry import (  # noqa: E402
    CandleData,
    compute_structural_facts,
    derive_chart_geometry,
    evaluate_structural_gates,
    format_candidate_summary,
)
from app.services import proposal_generator as gen_v7  # noqa: E402


def load_v6_module() -> object:
    text = subprocess.run(
        ["git", "-C", str(REPO), "show", "HEAD:server/app/services/proposal_generator.py"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    target = REPO / ".git" / "v6_proposal_generator_probe.py"
    target.write_text(text)
    spec = importlib.util.spec_from_file_location("v6gen_probe", target)
    module = importlib.util.module_from_spec(spec)
    sys.modules["v6gen_probe"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def candle_data(r: dict) -> CandleData:
    return CandleData(
        open=float(r["o"]), high=float(r["h"]), low=float(r["l"]),
        close=float(r["c"]), volume=int(r["v"]), date=str(r["date"]),
    )


def load_candles() -> dict[tuple[str, str], dict]:
    out = {}
    for line in (EVIDENCE / "candles.jsonl").open():
        row = json.loads(line)
        out[(row["instrument_id"], row["as_of_date"])] = row
    return out


def images_map() -> dict[str, dict]:
    out = {}
    for line in (EVIDENCE / "attempts.jsonl").open():
        row = json.loads(line)
        out[row["id"]] = row
    return out


def build_inputs(rows, candles_map, attempts_map, v6):
    inputs = []
    for row in rows:
        att = attempts_map.get(row["attempt_id"])
        if att is None:
            continue
        key = (row.get("instrument_id"), row.get("as_of_date"))
        c_row = candles_map.get(key)
        if c_row is None:
            continue
        candles = [candle_data(c) for c in c_row["candles"]]
        tick = Decimal(str(c_row["tick_size"]))
        annotations = derive_chart_geometry(candles, tick_size=tick)
        facts = compute_structural_facts(candles, annotations.contractions, tick_size=tick)
        verdict = evaluate_structural_gates(facts)
        summary_v7 = format_candidate_summary(annotations.contractions, facts=facts)
        summary_v6 = v6.format_candidate_summary(annotations.contractions)
        detail_file = EVIDENCE / att["detail_image_file"]
        detail_b64 = base64.b64encode(detail_file.read_bytes()).decode("ascii")
        inputs.append(
            {
                "key": f"{row['symbol']}|{row['as_of_date']}",
                "symbol": row["symbol"],
                "as_of_date": row["as_of_date"],
                "attempt_id": row["attempt_id"],
                "tick_size": tick,
                "detail_b64": detail_b64,
                "summary_v6": summary_v6,
                "summary_v7": summary_v7,
                "structural_disposition": verdict.disposition,
                "structural_codes": list(verdict.codes),
            }
        )
    return inputs


WALL_CLOCK_CAP_S = 240.0


def _single_call(arm: str, item: dict, v6: object, timeout: float) -> dict:
    import queue
    import threading

    started = time.monotonic()
    q: "queue.Queue[dict]" = queue.Queue()
    thread = threading.Thread(
        target=lambda: q.put(_do_request(arm, item, v6, timeout)),
        daemon=True,
    )
    thread.start()
    try:
        result = q.get(timeout=WALL_CLOCK_CAP_S)
    except queue.Empty:
        result = {
            "ok": False,
            "error_type": "WallClockTimeout",
            "error_message": f"call exceeded {WALL_CLOCK_CAP_S:.0f}s wall-clock cap",
            "latency_s": round(time.monotonic() - started, 2),
        }
    return result


def _do_request(arm: str, item: dict, v6: object, timeout: float) -> dict:
    started = time.monotonic()
    result: dict = {}
    try:
        if arm == "flash_v6":
            body = v6.build_proposal_vision_request(
                detail_png_b64=item["detail_b64"],
                model="google/gemini-3.7-flash",
                tick_size=item["tick_size"],
                candidate_summary=item["summary_v6"],
            )
            parse = v6.parse_proposal_openrouter_response
        else:
            body = gen_v7.build_proposal_vision_request(
                detail_png_b64=item["detail_b64"],
                model="google/gemini-3.7-flash" if arm == "flash_v7" else AUDIT_MODEL,
                tick_size=item["tick_size"],
                candidate_summary=item["summary_v7"],
            )
            parse = gen_v7.parse_proposal_openrouter_response
        with httpx.Client(
            timeout=httpx.Timeout(timeout, connect=20.0, write=30.0, pool=20.0)
        ) as client:
            resp = client.post(
                os.environ.get("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions"),
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "X-Title": "SwingTraderVCP-audit-ab",
                },
                json=body,
            )
        result["http_status"] = resp.status_code
        if resp.status_code >= 400:
            data = resp.json()
            err = (data.get("error") or {}) if isinstance(data, dict) else {}
            result["ok"] = False
            result["error_code"] = err.get("code")
            result["error_message"] = str(err.get("message"))[:500]
            return result
        if time.monotonic() - started > WALL_CLOCK_CAP_S:
            raise TimeoutError("wall-clock cap exceeded")
        output, usage, cost, request_id = parse(resp.json())
        result.update(
            {
                "ok": True,
                "classification": output.classification,
                "forming_state": (
                    output.forming_state if output.classification == "forming" else None
                ),
                "pattern_type": getattr(output, "pattern_type", None),
                "primary_reason": getattr(output, "primary_reason", None),
                "progressive_tightening": output.progressive_tightening,
                "volume_dry_up": output.volume_dry_up,
                "base_quality": output.base_quality.model_dump(),
                "confidence": output.confidence,
                "llm_candidate_rows": len(output.candidate_assessments),
                "merge_rows": sum(
                    1 for r in output.candidate_assessments if r.action == "merge"
                ),
                "reject_rows": sum(
                    1 for r in output.candidate_assessments if r.action == "reject"
                ),
                "extra_windows": len(output.extra_windows),
                "cost": cost,
                "usage": usage if isinstance(usage, dict) else {},
                "request_id": request_id,
            }
        )
    except Exception as exc:  # noqa: BLE001
        result["ok"] = False
        result["error_type"] = type(exc).__name__
        result["error_message"] = str(exc)[:500]
    result["latency_s"] = round(time.monotonic() - started, 2)
    return result


def call_once(arm: str, item: dict, v6: object, timeout: float) -> dict:
    """One call with one retry (production attempt semantics) on provider
    failures — matches the deployed worker's retry policy."""
    started = time.monotonic()
    result: dict = {
        "arm": arm,
        "key": item["key"],
        "symbol": item["symbol"],
        "as_of_date": item["as_of_date"],
        "structural_disposition": item["structural_disposition"],
        "structural_codes": item["structural_codes"],
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "retries": 0,
    }
    attempt_result = _single_call(arm, item, v6, timeout=timeout)
    if (
        not attempt_result.get("ok")
        and attempt_result.get("error_type") in {
            "ProposalProviderError",
            "TimeoutError",
            "httpx.ReadTimeout",
            "httpx.ConnectTimeout",
            "httpx.RemoteProtocolError",
        }
        and time.monotonic() - started < WALL_CLOCK_CAP_S
    ):
        time.sleep(4)
        attempt_result = _single_call(arm, item, v6, timeout=timeout)
        result["retries"] = 1
    result.update(attempt_result)
    result["latency_s"] = round(time.monotonic() - started, 2)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=["dev", "holdout", "all"])
    ap.add_argument("--arms", default="flash_v6,flash_v7,strong_v7")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--missing",
        action="store_true",
        help="only re-run (arm,key) pairs that are absent or failed",
    )
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--offset", type=int, default=0)
    args = ap.parse_args()

    if args.split == "all":
        rows = [json.loads(l) for l in (EVIDENCE / "unique_inputs.jsonl").open()]
    else:
        rows = [json.loads(l) for l in (EVIDENCE / f"split_{args.split}.jsonl").open()]
    candles_map = load_candles()
    attempts_map = images_map()
    v6 = load_v6_module()
    items = build_inputs(rows, candles_map, attempts_map, v6)
    if args.limit:
        items = items[: args.limit]
    arms = [a.strip() for a in args.arms.split(",")]

    ok_pairs: set[tuple[str, str]] = set()
    if args.missing and (EVIDENCE / "reaudit_ab.jsonl").exists():
        for line in (EVIDENCE / "reaudit_ab.jsonl").open():
            call = json.loads(line)
            if call.get("ok"):
                ok_pairs.add((call["arm"], call["key"]))

    runs: list[tuple[str, dict]] = []
    for item in items:
        for arm in arms:
            if args.missing and (arm, item["key"]) in ok_pairs:
                continue
            runs.append((arm, item))
    if args.offset:
        runs = runs[args.offset :]
    if not runs:
        print("no (arm,key) pairs to run")
        return 0
    out_path = EVIDENCE / "reaudit_ab.jsonl"
    print(
        f"inputs={len(items)} arms={arms} runs={len(runs)} "
        f"model_strong={AUDIT_MODEL} out={out_path}",
        flush=True,
    )
    total = len(runs)
    done = 0
    import concurrent.futures
    import threading as _threading

    write_lock = _threading.Lock()

    def run_one(arm: str, item: dict) -> None:
        nonlocal done
        print(f"start {arm} {item['symbol']} {item['as_of_date']}", flush=True)
        timeout = 180.0 if arm == "strong_v7" else 90.0
        call = call_once(arm, item, v6, timeout=timeout)
        with write_lock:
            with out_path.open("a") as fh:
                fh.write(json.dumps(call) + "\n")
            done += 1
            tag = call.get("classification", call.get("error_type", "ERR"))
            print(
                f"[{done}/{total}] {arm} {item['symbol']} {item['as_of_date']} "
                f"-> {tag} {call.get('latency_s')}s",
                flush=True,
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_one, arm, item) for arm, item in runs]
        for future in concurrent.futures.as_completed(futures):
            exc = future.exception()
            if exc is not None:
                print(f"worker error: {exc}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
