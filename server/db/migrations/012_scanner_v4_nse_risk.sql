-- Personal scanner v4: NSE read-only enrichment provenance and multi-source links.
BEGIN;

ALTER TABLE fundamental_snapshots
    DROP CONSTRAINT IF EXISTS fundamental_snapshots_statement_type_check;

ALTER TABLE fundamental_snapshots
    ADD CONSTRAINT fundamental_snapshots_statement_type_check CHECK (
        statement_type IN ('consolidated', 'standalone', 'not_applicable')
    ),
    ADD COLUMN source_url text,
    ADD COLUMN filing_date timestamptz,
    ADD COLUMN revision_date timestamptz,
    ADD COLUMN reporting_period date,
    ADD COLUMN taxonomy_version text,
    ADD COLUMN parser_version text,
    ADD COLUMN fetch_status text NOT NULL DEFAULT 'succeeded',
    ADD COLUMN provider_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD CONSTRAINT fundamental_snapshots_fetch_status_check CHECK (
        fetch_status IN ('succeeded', 'ambiguous', 'failed')
    );

CREATE INDEX fundamental_snapshots_provider_period_idx
ON fundamental_snapshots (instrument_id, provider, reporting_period DESC, filing_date DESC);

CREATE TABLE screening_result_fundamental_snapshots (
    screening_result_id uuid NOT NULL REFERENCES screening_results(id) ON DELETE CASCADE,
    snapshot_id uuid NOT NULL REFERENCES fundamental_snapshots(id),
    role text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (screening_result_id, snapshot_id, role),
    CONSTRAINT screening_result_fundamental_snapshots_role_check CHECK (
        role IN ('primary', 'promoter_pledge', 'leverage')
    )
);

CREATE INDEX screening_result_fundamental_snapshots_result_idx
ON screening_result_fundamental_snapshots (screening_result_id, role);

COMMENT ON TABLE screening_result_fundamental_snapshots IS
'Reproducible links from one screening result to its primary Upstox snapshot and optional official NSE enrichment snapshots.';

COMMIT;
