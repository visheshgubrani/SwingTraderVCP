# Database Schema

This directory contains the Postgres schema for the swing trading system.
Schema decisions live here rather than in `AGENTS.md`; `AGENTS.md` remains the
architecture and component-boundary source of truth.

## Files

- `schema.sql` - executable Postgres DDL for the initial application schema.
- `migrations/001_p3_paper_trading.sql` - upgrades a pre-P3 local database
  with the paper-execution fields and idempotency constraints.
- `migrations/002_p4_live_order_gateway.sql` - adds async-placement
  correlation IDs, submission safety states, and replay-safe gateway events.
- `migrations/003_p7_fundamental_pass.sql` - adds durable, read-only
  fundamental snapshots and links each LLM annotation to its exact input.
- `migrations/004_p8_journal_ai.sql` - adds automated journal, chart artifact,
  market-regime snapshot, and read-only AI-coach storage.
- `migrations/005_screening_score_engine.sql` - adds the nullable, constrained
  0-100 technical score while preserving legacy screening results.
- `migrations/006_p7_reliable_fundamentals.sql` - adds ordered P7 job/item
  state, deterministic scorecard projections, annotation cache, and separate
  fundamentals processing/AI pause controls.
- `migrations/007_swyingify_auth.sql` - adds the Better Auth user, session,
  account, and verification tables owned by the Swyingify Next.js app.
- `migrations/008_p7_ai_trace.sql` - records every actual OpenRouter attempt,
  links successful cached opinions to their source attempt, and constrains the
  independent fundamentals/AI status vocabularies.
- `migrations/009_nifty500_rs_benchmark.sql` - registers `NSE:NIFTY500-INDEX`
  for the vcp_score_v3 RS-line benchmark (EOD sync alongside Nifty 50).
- `migrations/010_saas_scan_templates.sql` - adds `scan_templates` and extends
  `scan_runs` with `template_id`, `visibility`, `owner_user_id`, `as_of_date`
  for Swyingify global daily Standard scans.
- `migrations/011_saas_entitlements_and_strict.sql` - adds the Better Auth
  admin role, provider-neutral SaaS subscription state, and the paid Minervini
  Strict template. Runtime enforcement remains in the Swyingify Next.js BFF.
- `migrations/012_scanner_v4_nse_risk.sql` - adds NSE pledge/leverage risk
  enrichment tables and constraints used by the deterministic P7 scorecard
  adjustments (promoter pledge / non-financial leverage).
- `migrations/013_vcp_vision.sql` - adds the advisory chart-image VCP
  validator: `vcp_visual_analyses` (frozen candle source, retained chart PNGs,
  reuse key, sanitized AI result) and `vcp_visual_attempts` (one row per
  provider call, sanitized request/response, usage, cost, error class).
- `migrations/014_vcp_vision_hardening.sql` - upgrades databases that
  already applied the original 013 migration with immutable `frozen_ohlcv`,
  frozen reasoning/max-token settings, and the complete reuse identity.
- `migrations/015_p10_automation.sql` - adds immutable proposals, entry legs,
  trigger/capacity state, risk policies, allocation ledger, and 5-minute
  profile storage.
- `migrations/016_p10_safety_hardening.sql` - adds audited serial vision
  attempts, approval immutability, reconciliation state, and P10 pause controls.
- `migrations/017_p10_review_hardening.sql` - upgrades already-migrated P10
  databases with correction intent types, conflict consumption, single-active
  policy enforcement, and complete geometry/renderer reuse identity.

## Domain Layout

The schema is organized around five data domains:

1. **Reference data**
   - `instruments`
   - `universe_memberships`

2. **Market data**
   - `market_candles`
   - `market_ticks`

3. **Screening**
   - `scan_templates` (Swyingify SaaS)
   - `scan_runs`
   - `screening_results`
   - `fundamental_snapshots`
   - `screening_result_fundamental_snapshots`
   - `watchlists`
   - `watchlist_items`

4. **Trading**
   - `trade_instructions`
   - `positions`
   - `position_events`
   - `order_intents`
   - `order_events`
   - `order_fills`

5. **Audit / operational**
   - `job_runs`
   - `reconciliation_runs`
   - `reconciliation_items`
   - `broker_auth_tokens`
   - `system_controls`
   - `system_events`

## Write Ownership

Keep table writes aligned with the component ownership from `AGENTS.md`:

- Market data tables are written by ingestion/backfill workers.
- Screening tables are written by the screening worker and reviewed by the UI.
- `fundamental_snapshots` are written only by the P7 arq job after the
  technical shortlist has been persisted. Raw provider responses and
  deterministic normalized facts are retained for audit/replay.
- Official NSE shareholding and integrated-filing XBRLs are immutable P7
  enrichment snapshots. Known risks may adjust only the deterministic
  fundamental score; their multi-source links are owned by
  `screening_result_fundamental_snapshots`. NSE failures remain visible as
  unknown diagnostics, receive no score penalty, and never invalidate the
  primary Upstox snapshot.
- Trading tables are written by the execution engine and position monitor.
- Operational/audit tables are written by scheduler, reconciliation, and system
  services.

Reconciliation (P6) writes `reconciliation_runs` / `reconciliation_items` and
may heal matched live intents through the order-gateway persistence path. It
flags external broker activity and position qty mismatches without auto-importing
positions or calling Fyers order placement APIs.

The API layer should stay thin: it can create explicit human instructions and
read state for the frontend, but it should not perform screening, monitoring,
or direct Fyers order placement.

## Partitioning

`market_candles` and `market_ticks` are range-partitioned by timestamp and
include default partitions so the schema is usable immediately. Production
migrations should create monthly partitions ahead of time, for example:

```sql
CREATE TABLE market_candles_2026_07
PARTITION OF market_candles
FOR VALUES FROM ('2026-07-01 00:00:00+00') TO ('2026-08-01 00:00:00+00');

CREATE TABLE market_ticks_2026_07
PARTITION OF market_ticks
FOR VALUES FROM ('2026-07-01 00:00:00+00') TO ('2026-08-01 00:00:00+00');
```

Daily candles can be retained indefinitely. Raw ticks should have a shorter
retention policy or be downsampled once they are no longer needed for monitor
debugging and recent trade analysis.

## Traceability

Scanner-sourced trades can be traced through:

`positions.screening_result_id -> screening_results.scan_run_id -> scan_runs`

Manual trades can leave `screening_result_id` null while still preserving the
human instruction, order intent, fills, and position event trail.

P7 annotations can be traced through:

`screening_results.fundamental_snapshot_id -> fundamental_snapshots` and
`screening_result_fundamental_snapshots -> fundamental_snapshots` and
`fundamental_analysis_items -> fundamental_ai_attempts -> fundamental_annotations`

The deterministic scorecard is authoritative. Each AI attempt retains its exact
sanitized request/response, provider IDs, usage, cost, and error metadata.
Model reasoning details and provider credentials are never stored.

## P3 Paper Execution

P3 persists `product_type = 'CNC'` and `execution_mode = 'paper'` on the
money-path records. Confirmation creates exactly one pending position and one
entry intent per trade instruction. The intent remains `created`: this means
the paper engine logged it but deliberately made no broker request.

## P4 Live Entry and Order Gateway

Migration `002_p4_live_order_gateway.sql` adds the broker correlation fields
(`fyers_async_id`, Fyers/exchange order IDs), durable submission states, and a
unique event fingerprint. The execution engine writes and commits the intent;
the order gateway owns `order_events` and `order_fills` from the single Fyers
order WebSocket. Fill aggregation moves a pending entry to `open`, including
partial fills, without depending on the API or UI process.
