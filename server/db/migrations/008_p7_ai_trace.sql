-- P7 auditable AI second opinions and independent source/AI state contracts.
BEGIN;

ALTER TABLE screening_results
    DROP CONSTRAINT IF EXISTS screening_results_fundamental_status_check,
    DROP CONSTRAINT IF EXISTS screening_results_ai_status_check;

ALTER TABLE screening_results
    ADD CONSTRAINT screening_results_fundamental_status_check CHECK (
        fundamental_status IN (
            'not_requested', 'queued', 'running', 'completed', 'failed', 'skipped'
        )
    ),
    ADD CONSTRAINT screening_results_ai_status_check CHECK (
        ai_status IN (
            'not_requested', 'queued', 'running', 'succeeded', 'cached',
            'failed', 'paused', 'budget_exhausted', 'skipped'
        )
    );

CREATE TABLE fundamental_ai_attempts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_item_id uuid NOT NULL
        REFERENCES fundamental_analysis_items(id) ON DELETE CASCADE,
    attempt_number integer NOT NULL,
    status text NOT NULL DEFAULT 'started',
    model text NOT NULL,
    reasoning_effort text NOT NULL,
    prompt_version text NOT NULL,
    response_schema text NOT NULL,
    input_hash text NOT NULL,
    request_payload jsonb NOT NULL,
    response_payload jsonb,
    http_status integer,
    request_id text,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    cost numeric(18, 8) NOT NULL DEFAULT 0,
    error_code text,
    error_message text,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CONSTRAINT fundamental_ai_attempts_number_check CHECK (attempt_number > 0),
    CONSTRAINT fundamental_ai_attempts_status_check CHECK (
        status IN (
            'started', 'succeeded', 'invalid_response',
            'provider_error', 'transport_unknown'
        )
    ),
    CONSTRAINT fundamental_ai_attempts_http_status_check CHECK (
        http_status IS NULL OR (http_status >= 100 AND http_status <= 599)
    ),
    CONSTRAINT fundamental_ai_attempts_cost_check CHECK (cost >= 0),
    CONSTRAINT fundamental_ai_attempts_unique_number
        UNIQUE (analysis_item_id, attempt_number)
);

CREATE INDEX fundamental_ai_attempts_item_started_idx
ON fundamental_ai_attempts (analysis_item_id, started_at);

ALTER TABLE fundamental_annotations
    ADD COLUMN source_attempt_id uuid
        REFERENCES fundamental_ai_attempts(id) ON DELETE SET NULL;

CREATE INDEX fundamental_annotations_source_attempt_idx
ON fundamental_annotations (source_attempt_id)
WHERE source_attempt_id IS NOT NULL;

COMMIT;
