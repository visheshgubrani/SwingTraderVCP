#!/usr/bin/env python3
"""Export deployed P10 proposal evidence for offline evaluation (read-only).

Pulls every prompt-v5/v6 proposal attempt (metadata, structured output,
error details, both chart PNGs) plus the exact 252-session frozen candle
windows those attempts used, from the deployed Postgres.

Transport: runs a tiny asyncpg program *inside* the deployed API container
via `docker exec` (so DB credentials never leave the host and psql's
long-line formatting quirks are avoided). No DB writes, nothing stored on
the host beyond stdout.

Outputs under <out_dir>/:
  attempts.jsonl      one JSON object per attempt (no inline images)
  images/<id>.detail.png / <id>.context.png
  candles.jsonl       one JSON object per (instrument, as_of_date) with the
                      252-session freeze in ascending date order
  manifest.json       counts and per-file hashes for the freeze

Usage:
  python scripts/export_proposal_evidence.py \
      --host 80.225.207.109 --key ~/ssh_key_test.key --user ubuntu \
      --out evidence/p10
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ATTEMPTS_SQL = """
SELECT encode(convert_to(json_build_object(
  'id', pa.id::text,
  'automation_run_id', pa.automation_run_id::text,
  'screening_result_id', pa.screening_result_id::text,
  'instrument_id', pa.instrument_id::text,
  'symbol', pa.symbol,
  'attempt_number', pa.attempt_number,
  'status', pa.status,
  'source_hash', pa.source_hash,
  'renderer_version', pa.renderer_version,
  'prompt_version', pa.prompt_version,
  'schema_version', pa.schema_version,
  'geometry_version', pa.geometry_version,
  'prompt_hash', pa.prompt_hash,
  'input_hash', pa.input_hash,
  'model', pa.model,
  'risk_policy_version', pa.risk_policy_version,
  'context_image_hash', pa.context_image_hash,
  'detail_image_hash', pa.detail_image_hash,
  'detail_image_b64', encode(pa.detail_image, 'base64'),
  'context_image_b64', encode(pa.context_image, 'base64'),
  'provider_request_id', pa.provider_request_id,
  'provider_usage', pa.provider_usage::text,
  'provider_cost', pa.provider_cost::text,
  'structured_output', pa.structured_output::text,
  'error_type', pa.error_type,
  'error_message', pa.error_message,
  'error_details', pa.error_details::text,
  'started_at', pa.started_at::text,
  'completed_at', pa.completed_at::text,
  'as_of_date', sr.as_of_date::text,
  'scan_run_id', ar.scan_run_id::text
)::text, 'UTF8'), 'base64')
FROM proposal_attempts pa
JOIN automation_runs ar ON ar.id = pa.automation_run_id
JOIN scan_runs sr ON sr.id = ar.scan_run_id
WHERE pa.prompt_version IN ('p10_vcp_proposal_v5', 'p10_vcp_proposal_v6')
  AND pa.status <> 'running'
ORDER BY pa.started_at, pa.id
"""

CANDLES_SQL = """
WITH pair AS (
  SELECT DISTINCT pa.instrument_id, sr.as_of_date
  FROM proposal_attempts pa
  JOIN automation_runs ar ON ar.id = pa.automation_run_id
  JOIN scan_runs sr ON sr.id = ar.scan_run_id
  WHERE pa.prompt_version IN ('p10_vcp_proposal_v5', 'p10_vcp_proposal_v6')
    AND pa.status <> 'running'
)
SELECT encode(convert_to(json_build_object(
  'instrument_id', p.instrument_id::text,
  'as_of_date', p.as_of_date::text,
  'symbol', i.fyers_symbol,
  'tick_size', i.tick_size::text,
  'lot_size', i.lot_size::text,
  'candles', (
    SELECT json_agg(json_build_object(
      'date', to_char(mc.candle_start AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD'),
      'o', mc.open_price, 'h', mc.high_price, 'l', mc.low_price,
      'c', mc.close_price, 'v', mc.volume
    ) ORDER BY mc.candle_start)
    FROM (
      SELECT candle_start, open_price, high_price, low_price, close_price, volume
      FROM market_candles
      WHERE instrument_id = p.instrument_id
        AND timeframe = '1d'
        AND (candle_start AT TIME ZONE 'Asia/Kolkata')::date <= p.as_of_date
      ORDER BY candle_start DESC
      LIMIT 252
    ) mc
  )
)::text, 'UTF8'), 'base64')
FROM pair p
JOIN instruments i ON i.id = p.instrument_id
ORDER BY p.as_of_date, i.fyers_symbol
"""

REMOTE_PY_TEMPLATE = """\
import asyncio, base64, json, os, sys
import asyncpg

SQL = {sql!r}

async def main() -> None:
    conn = await asyncpg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ["POSTGRES_USER"],
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        database=os.environ["POSTGRES_DB"],
    )
    try:
        rows = await conn.fetch(SQL)
        out = sys.stdout.buffer
        for row in rows:
            value = row[0]
            if value is None:
                continue
            # encode(...,'base64') wraps output at 76 chars with real
            # newlines; strip them to get one contiguous base64 row.
            out.write(value.replace("\\n", "").encode("ascii") + b"\\n")
    finally:
        await conn.close()

asyncio.run(main())
"""


def run_remote_python(args: argparse.Namespace, sql: str) -> bytes:
    py_src = REMOTE_PY_TEMPLATE.format(sql=sql)
    remote = [
        "ssh", "-F", "/dev/null", "-i", args.key,
        "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
        f"{args.user}@{args.host}",
        "docker", "exec", "-i", args.container,
        "python", "-",
    ]
    proc = subprocess.run(
        remote, input=py_src.encode("utf-8"),
        capture_output=True, timeout=3600,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace")[-4000:])
        raise RuntimeError(f"remote python exited {proc.returncode}")
    return proc.stdout


def parse_rows(raw: bytes) -> list[dict]:
    rows: list[dict] = []
    for line in raw.splitlines():
        if not line:
            continue
        blob = base64.b64decode(line, validate=True)
        rows.append(json.loads(blob.decode("utf-8")))
    return rows


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--user", default="ubuntu")
    ap.add_argument("--out", default="evidence/p10")
    ap.add_argument("--container", default="swingtradervcp-api-1")
    args = ap.parse_args()

    out_dir = Path(args.out)
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    print("exporting attempts ...", flush=True)
    attempt_rows = parse_rows(run_remote_python(args, ATTEMPTS_SQL))
    attempts_out = []
    images = 0
    for row in attempt_rows:
        for kind in ("detail", "context"):
            b64 = row.pop(f"{kind}_image_b64", None)
            if b64:
                png = base64.b64decode(b64)
                png_path = images_dir / f"{row['id']}.{kind}.png"
                png_path.write_bytes(png)
                row[f"{kind}_image_file"] = str(png_path.relative_to(out_dir))
                images += 1
        attempts_out.append(row)

    with (out_dir / "attempts.jsonl").open("w") as fh:
        for row in attempts_out:
            fh.write(json.dumps(row) + "\n")

    print("exporting frozen candles ...", flush=True)
    candle_rows = parse_rows(run_remote_python(args, CANDLES_SQL))
    with (out_dir / "candles.jsonl").open("w") as fh:
        for row in candle_rows:
            fh.write(json.dumps(row) + "\n")

    manifest = {
        "attempts": len(attempt_rows),
        "attempts_file": sha256_file(out_dir / "attempts.jsonl"),
        "images": images,
        "candle_files": len(candle_rows),
        "candles_file": sha256_file(out_dir / "candles.jsonl"),
        "prompt_versions": sorted(
            {r["prompt_version"] for r in attempts_out if r.get("prompt_version")}
        ),
        "statuses": sorted({r["status"] for r in attempts_out}),
    }
    with (out_dir / "manifest.json").open("w") as fh:
        json.dump(manifest, fh, indent=2)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
