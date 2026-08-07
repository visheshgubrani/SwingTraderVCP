-- P7 reliability: ordered, observable fundamentals jobs and independent controls.
BEGIN;

ALTER TABLE screening_results
    ADD COLUMN IF NOT EXISTS fundamental_status text NOT NULL DEFAULT 'not_requested',
    ADD COLUMN IF NOT EXISTS fundamental_verdict text,
    ADD COLUMN IF NOT EXISTS fundamental_scorecard jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS ai_status text NOT NULL DEFAULT 'not_requested';

CREATE TABLE IF NOT EXISTS fundamental_analysis_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_run_id uuid NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'queued',
    mode text NOT NULL DEFAULT 'retry_incomplete',
    queue_job_id text UNIQUE,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    current_rank integer,
    current_symbol text,
    provider_requests integer NOT NULL DEFAULT 0,
    input_tokens integer NOT NULL DEFAULT 0,
    reasoning_tokens integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    cached_tokens integer NOT NULL DEFAULT 0,
    total_cost numeric(18, 8) NOT NULL DEFAULT 0,
    error_message text,
    started_at timestamptz,
    heartbeat_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fundamental_analysis_runs_status_check CHECK (
        status IN ('queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled')
    ),
    CONSTRAINT fundamental_analysis_runs_mode_check CHECK (
        mode IN ('retry_incomplete', 'refresh_stale')
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS fundamental_analysis_runs_one_active_scan_idx
ON fundamental_analysis_runs (scan_run_id)
WHERE status IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS fundamental_analysis_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_run_id uuid NOT NULL REFERENCES fundamental_analysis_runs(id) ON DELETE CASCADE,
    screening_result_id uuid NOT NULL REFERENCES screening_results(id) ON DELETE CASCADE,
    rank integer NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    snapshot_id uuid REFERENCES fundamental_snapshots(id),
    analysis_key text,
    provider_requests integer NOT NULL DEFAULT 0,
    input_tokens integer NOT NULL DEFAULT 0,
    reasoning_tokens integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    cached_tokens integer NOT NULL DEFAULT 0,
    cost numeric(18, 8) NOT NULL DEFAULT 0,
    error_code text,
    error_message text,
    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fundamental_analysis_items_status_check CHECK (
        status IN ('queued', 'fetching', 'scoring', 'ai_running', 'succeeded', 'rules_only', 'failed', 'cancelled', 'budget_exhausted')
    ),
    CONSTRAINT fundamental_analysis_items_unique_result UNIQUE (analysis_run_id, screening_result_id)
);

CREATE INDEX IF NOT EXISTS fundamental_analysis_items_run_rank_idx
ON fundamental_analysis_items (analysis_run_id, rank);

CREATE TABLE IF NOT EXISTS fundamental_annotations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_key text NOT NULL UNIQUE,
    status text NOT NULL DEFAULT 'succeeded',
    model text NOT NULL,
    reasoning_effort text NOT NULL,
    prompt_version text NOT NULL,
    input_hash text NOT NULL,
    payload jsonb NOT NULL,
    request_id text,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    cost numeric(18, 8) NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fundamental_annotations_status_check CHECK (status = 'succeeded')
);

INSERT INTO system_controls (control_key, enabled, reason, changed_by)
VALUES
    ('fundamentals_processing_paused', false, 'Default: P7 fundamental processing enabled when configured.', 'schema'),
    ('fundamentals_ai_paused', false, 'Default: P7 AI annotations enabled when configured.', 'schema')
ON CONFLICT (control_key) DO NOTHING;

COMMIT;
