-- On-demand VCP vision validator: advisory chart-image analysis storage.
-- Personal-app-only annotation surface. Never touches ranks, reviewer status,
-- watchlists, trade instructions, positions, or orders.
BEGIN;

CREATE TABLE vcp_visual_analyses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    screening_result_id uuid NOT NULL REFERENCES screening_results(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'awaiting_capture' CHECK (
        status IN ('awaiting_capture', 'queued', 'running', 'succeeded', 'failed')
    ),
    -- Canonical frozen candle source (scan run, as_of_date, session windows).
    chart_source jsonb NOT NULL DEFAULT '{}'::jsonb,
    frozen_ohlcv jsonb NOT NULL,
    -- Retained standardized chart PNGs (TOAST handles out-of-line storage;
    -- summary queries must never select these columns).
    context_image bytea,
    context_image_hash text,
    detail_image bytea,
    detail_image_hash text,
    -- Reuse identity: cached analyses match on all of these.
    source_hash text NOT NULL,
    renderer_version text NOT NULL,
    model text NOT NULL,
    reasoning_effort text NOT NULL CHECK (
        reasoning_effort IN ('low', 'medium', 'high', 'xhigh')
    ),
    max_tokens integer NOT NULL CHECK (max_tokens > 0),
    prompt_version text NOT NULL,
    schema_version text NOT NULL,
    input_hash text,
    -- Structured, sanitized AI result (no reasoning_details ever).
    result jsonb,
    ai_verdict text CHECK (
        ai_verdict IS NULL OR ai_verdict IN ('valid', 'invalid', 'uncertain')
    ),
    error_code text,
    error_message text,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    cost numeric(18, 8) NOT NULL DEFAULT 0 CHECK (cost >= 0),
    -- Human feedback: evaluation data only, never prompt-training input.
    human_verdict text CHECK (
        human_verdict IS NULL OR human_verdict IN ('valid', 'invalid', 'uncertain')
    ),
    human_note text,
    human_reviewed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT vcp_visual_analyses_chart_source_check CHECK (
        jsonb_typeof(chart_source) = 'object'
    ),
    CONSTRAINT vcp_visual_analyses_frozen_ohlcv_check CHECK (
        jsonb_typeof(frozen_ohlcv) = 'array'
    )
);

CREATE TRIGGER vcp_visual_analyses_set_updated_at
BEFORE UPDATE ON vcp_visual_analyses
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- One reusable non-failed analysis per result and reuse identity.
CREATE UNIQUE INDEX vcp_visual_analyses_reuse_uidx
ON vcp_visual_analyses (
    screening_result_id,
    source_hash,
    renderer_version,
    model,
    reasoning_effort,
    max_tokens,
    prompt_version,
    schema_version
)
WHERE status IN ('awaiting_capture', 'queued', 'running', 'succeeded');

CREATE INDEX vcp_visual_analyses_result_created_idx
ON vcp_visual_analyses (screening_result_id, created_at DESC);

CREATE TABLE vcp_visual_attempts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_id uuid NOT NULL REFERENCES vcp_visual_analyses(id) ON DELETE CASCADE,
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    status text NOT NULL DEFAULT 'started' CHECK (
        status IN ('started', 'succeeded', 'invalid_response', 'provider_error', 'transport_unknown')
    ),
    model text NOT NULL,
    reasoning_effort text NOT NULL,
    prompt_version text NOT NULL,
    response_schema text NOT NULL,
    input_hash text NOT NULL,
    -- Sanitized provider payloads: reasoning/details stripped before write.
    request_payload jsonb NOT NULL,
    response_payload jsonb,
    http_status integer CHECK (
        http_status IS NULL OR (http_status >= 100 AND http_status <= 599)
    ),
    request_id text,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    cost numeric(18, 8) NOT NULL DEFAULT 0 CHECK (cost >= 0),
    error_code text,
    error_message text,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (analysis_id, attempt_number)
);

CREATE INDEX vcp_visual_attempts_analysis_started_idx
ON vcp_visual_attempts (analysis_id, started_at);

COMMENT ON TABLE vcp_visual_analyses IS
'Advisory chart-image VCP validation per screening result. The AI verdict never changes technical rank, vcp_detected, reviewer_status, watchlists, trade drafts, or execution state.';

COMMENT ON TABLE vcp_visual_attempts IS
'Every VCP vision provider attempt with sanitized request/response, usage, cost, and error classification for audit.';

COMMIT;
