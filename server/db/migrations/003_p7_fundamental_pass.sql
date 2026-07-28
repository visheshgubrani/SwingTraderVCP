-- P7: durable survivor-only fundamental snapshots and LLM traceability.
-- Apply after 002_p4_live_order_gateway.sql.

BEGIN;

CREATE TABLE fundamental_snapshots (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id uuid NOT NULL REFERENCES instruments(id),
    provider text NOT NULL,
    statement_type text NOT NULL DEFAULT 'consolidated',
    fetched_at timestamptz NOT NULL DEFAULT now(),
    latest_annual_period text,
    latest_quarterly_period text,
    raw_payload jsonb NOT NULL,
    normalized_facts jsonb NOT NULL,
    content_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fundamental_snapshots_statement_type_check CHECK (
        statement_type IN ('consolidated', 'standalone')
    )
);

CREATE INDEX fundamental_snapshots_instrument_fetched_idx
ON fundamental_snapshots (instrument_id, provider, statement_type, fetched_at DESC);

CREATE INDEX fundamental_snapshots_content_hash_idx
ON fundamental_snapshots (content_hash);

ALTER TABLE screening_results
    ADD COLUMN fundamental_snapshot_id uuid REFERENCES fundamental_snapshots(id);

CREATE INDEX screening_results_fundamental_snapshot_idx
ON screening_results (fundamental_snapshot_id)
WHERE fundamental_snapshot_id IS NOT NULL;

COMMENT ON TABLE fundamental_snapshots IS
'Read-only provider payloads and deterministic normalized facts fetched only for persisted technical survivors.';

COMMIT;
