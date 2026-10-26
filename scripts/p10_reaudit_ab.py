#!/usr/bin/env python3
"""Multi-arm re-audit of the frozen P10 inputs (evaluation only).

Arms (identical frozen candles; production request builders and parsers):
  flash_base   - google/gemini-3.7-flash with the prompt, response schema and
                 candidate summary of --baseline-ref (the deployed code, e.g.
                 the last v8 commit), on the exported production chart: a
                 contemporaneous replay of what production saw.
  flash_v9     - google/gemini-3.7-flash with the working-tree prompt/schema,
                 the gates-v3 summary (base context, scoped checks) and a
                 re-rendered symbol-free 126-session chart.
  strong_v9    - AUDIT_MODEL (e.g. google/gemini-3.1-pro-preview) on the
                 flash_v9 inputs.
  validator_v2 - the advisory vision validator v2 (shared rubric) on charts
                 re-rendered by the proposal renderer plus the compact OHLCV
                 table. Production validator charts come from the browser
                 renderer, so this arm measures the prompt, not the pixels.
                 Its verdict is stored as classification with
                 invalid -> not_vcp so crosstabs line up.

Reads evidence/p10 (attempts, candles, unique_inputs, splits), writes
evidence/p10/reaudit_ab.jsonl (append, one JSON per call). The OpenRouter
key is read from evidence/.secrets/openrouter.key (gitignored) and never
printed. No DB writes, no money path.

Usage (from repo root with the server venv):
  cd server && .venv/bin/python ../scripts/p10_reaudit_ab.py \
      --split dev --arms flash_base,flash_v9,validator_v2 \
      --baseline-ref <deployed commit> [--limit N]
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "server"
sys.path.insert(0, str(SERVER))
sys.path.insert(0, str(REPO / "scripts"))
EVIDENCE = REPO / "evidence" / "p10"

KEY_FILE = REPO / "evidence" / ".secrets" / "openrouter.key"
FLASH_MODEL = "google/gemini-3.7-flash"
AUDIT_MODEL = os.environ.get("AUDIT_MODEL", "google/gemini-3.1-pro-preview")
VALIDATOR_VERDICT_AS_CLASS = {
    "valid": "valid",
    "forming": "forming",
    "invalid": "not_vcp",
    "uncertain": "uncertain",
}

os.environ["APP_ENVIRONMENT"] = "development"
key = KEY_FILE.read_text().strip()
if not key:
    raise SystemExit("missing evidence/.secrets/openrouter.key")
os.environ["OPENROUTER_API_KEY"] = key

import httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.domain.p10_geometry import (  # noqa: E402
    CandleData,
    compute_structural_facts,
    derive_chart_geometry,
    evaluate_structural_gates,
    format_candidate_summary,
)
from app.services import proposal_generator as gen_v9  # noqa: E402
from app.services.openrouter_content import parse_openrouter_structured_content  # noqa: E402
from app.services.proposal_renderer import (  # noqa: E402
    candles_to_dataframe,
    render_context_chart,
    render_proposal_charts,
)
from app.services.vcp_vision import (  # noqa: E402
    OpenRouterVisionClient,
    VcpVisionResultV2,
    frozen_ohlcv_from_payload,
    validate_and_snap_result,
)
from p10_ref_modules import RefP10  # noqa: E402


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


def b64(png: bytes) -> str:
    return base64.b64encode(png).decode("ascii")


def build_inputs(rows, candles_map, attempts_map, baseline: RefP10, arms: list[str]):
    rerender = any(arm != "flash_base" for arm in arms)
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
        item = {
            "key": f"{row['symbol']}|{row['as_of_date']}",
            "symbol": row["symbol"],
            "as_of_date": row["as_of_date"],
            "attempt_id": row["attempt_id"],
            "tick_size": tick,
            "candle_rows": c_row["candles"],
            "production_detail_b64": b64((EVIDENCE / att["detail_image_file"]).read_bytes()),
            "summary_base": baseline.candidate_summary(c_row["candles"], tick),
            "summary_v9": format_candidate_summary(facts.candidates, facts=facts),
            "structural_disposition": verdict.disposition,
            "structural_codes": list(verdict.codes),
        }
        if rerender:
            charts = render_proposal_charts(candles, symbol=row["symbol"])
            item["detail_v9_b64"] = b64(charts.detail_png)
            # Blind context chart for the validator arm (the human chart names the symbol).
            item["context_blind_b64"] = b64(
                render_context_chart(candles_to_dataframe(candles).tail(252), symbol="Context")
            )
        inputs.append(item)
    return inputs


WALL_CLOCK_CAP_S = 240.0


def _single_call(arm: str, item: dict, baseline: RefP10, timeout: float) -> dict:
    import queue
    import threading

    started = time.monotonic()
    q: "queue.Queue[dict]" = queue.Queue()
    thread = threading.Thread(
        target=lambda: q.put(_do_request(arm, item, baseline, timeout)),
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


def _validator_request(item: dict) -> tuple[dict, object]:
    client = OpenRouterVisionClient(
        api_key=key,
        model=FLASH_MODEL,
        reasoning_effort=settings.vcp_vision_reasoning_effort,
        max_tokens=settings.vcp_vision_max_tokens,
    )
    rows = item["candle_rows"]
    frozen = frozen_ohlcv_from_payload(
        [
            {"date": r["date"], "open": r["o"], "high": r["h"], "low": r["l"],
             "close": r["c"], "volume": r["v"]}
            for r in rows
        ],
        symbol="",
        as_of_date=dt.date.fromisoformat(str(rows[-1]["date"])),
        context_sessions=len(rows),
        detail_sessions=min(126, len(rows)),
    )
    body = client.build_request(
        frozen=frozen,
        context_png_b64=item["context_blind_b64"],
        detail_png_b64=item["detail_v9_b64"],
    )

    def parse(data: dict):
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        parsed = parse_openrouter_structured_content(data["choices"][0], usage=usage)
        result = VcpVisionResultV2.model_validate(parsed)
        cleaned = validate_and_snap_result(result, set(frozen.candles_by_date()))
        return cleaned, usage, float(usage.get("cost", 0) or 0), data.get("id")

    return body, parse


def _do_request(arm: str, item: dict, baseline: RefP10, timeout: float) -> dict:
    started = time.monotonic()
    result: dict = {}
    try:
        if arm == "flash_base":
            body = baseline.generator.build_proposal_vision_request(
                detail_png_b64=item["production_detail_b64"],
                model=FLASH_MODEL,
                tick_size=item["tick_size"],
                candidate_summary=item["summary_base"],
            )
            parse = baseline.generator.parse_proposal_openrouter_response
        elif arm == "validator_v2":
            body, parse = _validator_request(item)
        else:
            body = gen_v9.build_proposal_vision_request(
                detail_png_b64=item["detail_v9_b64"],
                model=FLASH_MODEL if arm == "flash_v9" else AUDIT_MODEL,
                tick_size=item["tick_size"],
                candidate_summary=item["summary_v9"],
            )
            parse = gen_v9.parse_proposal_openrouter_response
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
        if arm == "validator_v2":
            result.update(
                {
                    "ok": True,
                    "validator_verdict": output["verdict"],
                    "classification": VALIDATOR_VERDICT_AS_CLASS[output["verdict"]],
                    "confidence": output["confidence"],
                    "volume_assessment": output["volume"]["assessment"],
                    "prior_uptrend": output["prior_uptrend"]["assessment"],
                    "anchors": len(output["contraction_anchors"]),
                    "cost": cost,
                    "usage": usage,
                    "request_id": request_id,
                }
            )
            result["latency_s"] = round(time.monotonic() - started, 2)
            return result
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


def call_once(arm: str, item: dict, baseline: RefP10, timeout: float) -> dict:
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
    attempt_result = _single_call(arm, item, baseline, timeout=timeout)
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
        attempt_result = _single_call(arm, item, baseline, timeout=timeout)
        result["retries"] = 1
    result.update(attempt_result)
    result["latency_s"] = round(time.monotonic() - started, 2)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=["dev", "holdout", "all"])
    ap.add_argument("--arms", default="flash_base,flash_v9,validator_v2")
    ap.add_argument("--baseline-ref", default="HEAD", help="git ref of the deployed code")
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
    arms = [a.strip() for a in args.arms.split(",")]
    unknown = set(arms) - {"flash_base", "flash_v9", "strong_v9", "validator_v2"}
    if unknown:
        raise SystemExit(f"unknown arms: {sorted(unknown)}")
    if args.limit:
        rows = rows[: args.limit]
    candles_map = load_candles()
    attempts_map = images_map()
    baseline = RefP10(args.baseline_ref)
    items = build_inputs(rows, candles_map, attempts_map, baseline, arms)

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
        f"baseline={args.baseline_ref} ({baseline.prompt_version}) "
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
        timeout = 180.0 if arm == "strong_v9" else 90.0
        call = call_once(arm, item, baseline, timeout=timeout)
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
