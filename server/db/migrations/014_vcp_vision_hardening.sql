-- Harden VCP vision analyses created from migration 013: persist the actual frozen
-- OHLCV packet and freeze all model-generation settings in the reuse key.
BEGIN;

ALTER TABLE vcp_visual_analyses
    ADD COLUMN IF NOT EXISTS frozen_ohlcv jsonb,
    ADD COLUMN IF NOT EXISTS reasoning_effort text,
    ADD COLUMN IF NOT EXISTS max_tokens integer;

-- Rows created by the original 013 migration retain an empty legacy marker.
-- Runtime code rebuilds those rows only when the current source still matches
-- source_hash. Every newly-created analysis writes a non-empty immutable packet.
UPDATE vcp_visual_analyses
SET frozen_ohlcv = COALESCE(frozen_ohlcv, '[]'::jsonb),
    reasoning_effort = COALESCE(reasoning_effort, 'medium'),
    max_tokens = COALESCE(max_tokens, 2400);

ALTER TABLE vcp_visual_analyses
    ALTER COLUMN frozen_ohlcv SET NOT NULL,
    ALTER COLUMN reasoning_effort SET NOT NULL,
    ALTER COLUMN max_tokens SET NOT NULL;

ALTER TABLE vcp_visual_analyses
    DROP CONSTRAINT IF EXISTS vcp_visual_analyses_frozen_ohlcv_check,
    ADD CONSTRAINT vcp_visual_analyses_frozen_ohlcv_check CHECK (
        jsonb_typeof(frozen_ohlcv) = 'array'
    ),
    DROP CONSTRAINT IF EXISTS vcp_visual_analyses_max_tokens_check,
    ADD CONSTRAINT vcp_visual_analyses_max_tokens_check CHECK (max_tokens > 0),
    DROP CONSTRAINT IF EXISTS vcp_visual_analyses_reasoning_effort_check,
    ADD CONSTRAINT vcp_visual_analyses_reasoning_effort_check CHECK (
        reasoning_effort IN ('low', 'medium', 'high', 'xhigh')
    );

DROP INDEX IF EXISTS vcp_visual_analyses_reuse_uidx;

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

COMMENT ON COLUMN vcp_visual_analyses.frozen_ohlcv IS
'Immutable canonical OHLCV array used for chart capture, prompt grounding, and deterministic derived metrics. Empty arrays identify pre-014 legacy rows only.';

COMMIT;
