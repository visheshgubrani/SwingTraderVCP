# Database Schema

This directory contains the Postgres schema for the swing trading system.
Schema decisions live here rather than in `AGENTS.md`; `AGENTS.md` remains the
architecture and component-boundary source of truth.

## Files

- `schema.sql` - executable Postgres DDL for the initial application schema.

## Domain Layout

The schema is organized around five data domains:

1. **Reference data**
   - `instruments`
   - `universe_memberships`

2. **Market data**
   - `market_candles`
   - `market_ticks`

3. **Screening**
   - `scan_runs`
   - `screening_results`
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
- Trading tables are written by the execution engine and position monitor.
- Operational/audit tables are written by scheduler, reconciliation, and system
  services.

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
