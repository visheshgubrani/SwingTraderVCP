-- P10 hardening: immutable approval payloads, audited vision attempts,
-- durable bar state, and dedicated automation controls.

BEGIN;

ALTER TABLE order_intents
    DROP CONSTRAINT IF EXISTS order_intents_intent_type_check;
ALTER TABLE order_intents
    ADD CONSTRAINT order_intents_intent_type_check CHECK (
        intent_type IN (
            'entry', 'stop_loss_exit', 'target_exit', 'trailing_exit',
            'manual_exit', 'risk_reduction_exit', 'invalid_fill_exit'
        )
    );

ALTER TABLE trade_proposals
    ADD COLUMN IF NOT EXISTS risk_policy_id uuid REFERENCES risk_policies(id),
    ADD COLUMN IF NOT EXISTS risk_policy_version integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS approved_risk_budget_amount numeric(18, 4),
    ADD COLUMN IF NOT EXISTS prompt_version text NOT NULL DEFAULT 'p10_vcp_proposal_v2',
    ADD COLUMN IF NOT EXISTS schema_version text NOT NULL DEFAULT 'gemini_vcp_proposal_output_v2',
    ADD COLUMN IF NOT EXISTS geometry_version text NOT NULL DEFAULT 'p10_geometry_three_windows_v2',
    ADD COLUMN IF NOT EXISTS live_eligible boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS generated_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS context_image bytea,
    ADD COLUMN IF NOT EXISTS detail_image bytea,
    ADD COLUMN IF NOT EXISTS provider_request_id text,
    ADD COLUMN IF NOT EXISTS provider_usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS provider_cost numeric(18, 8) NOT NULL DEFAULT 0;

DROP INDEX IF EXISTS trade_proposals_input_version_uidx;
CREATE UNIQUE INDEX trade_proposals_input_version_uidx
ON trade_proposals (
    screening_result_id,
    source_hash,
    model,
    prompt_version,
    schema_version,
    renderer_version,
    geometry_version,
    risk_policy_version
);

CREATE UNIQUE INDEX IF NOT EXISTS proposal_decisions_one_per_proposal_uidx
ON proposal_decisions (proposal_id);

CREATE UNIQUE INDEX IF NOT EXISTS risk_policies_single_active_uidx
ON risk_policies ((true)) WHERE is_active = true;

ALTER TABLE entry_legs
    ALTER COLUMN trigger_price DROP NOT NULL,
    ALTER COLUMN chase_ceiling DROP NOT NULL,
    ALTER COLUMN eligible_session_start DROP NOT NULL,
    ALTER COLUMN eligible_session_end DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS first_filled_at timestamptz,
    ADD COLUMN IF NOT EXISTS signal_bar_timestamp timestamptz;

ALTER TABLE capacity_conflicts
    ADD COLUMN IF NOT EXISTS executed_at timestamptz;

CREATE UNIQUE INDEX IF NOT EXISTS trigger_events_leg_bar_type_uidx
ON trigger_events (leg_id, bar_timestamp, bar_type);

ALTER TABLE five_minute_bars
    ADD COLUMN IF NOT EXISTS cumulative_volume bigint NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS reconciliation_status text NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS reconciled_at timestamptz,
    ADD COLUMN IF NOT EXISTS reconciliation_details jsonb NOT NULL DEFAULT '{}'::jsonb;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'five_minute_bars_reconciliation_status_check'
    ) THEN
        ALTER TABLE five_minute_bars
            ADD CONSTRAINT five_minute_bars_reconciliation_status_check CHECK (
                reconciliation_status IN ('pending', 'verified', 'drifted', 'failed')
            );
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS proposal_attempts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    automation_run_id uuid NOT NULL REFERENCES automation_runs(id) ON DELETE CASCADE,
    screening_result_id uuid NOT NULL REFERENCES screening_results(id) ON DELETE CASCADE,
    instrument_id uuid NOT NULL REFERENCES instruments(id),
    symbol text NOT NULL,
    attempt_number integer NOT NULL CHECK (attempt_number BETWEEN 1 AND 2),
    status text NOT NULL CHECK (
        status IN ('running', 'valid', 'invalid', 'uncertain', 'failed', 'timed_out')
    ),
    source_hash text NOT NULL,
    renderer_version text NOT NULL,
    prompt_version text NOT NULL,
    schema_version text NOT NULL,
    geometry_version text NOT NULL,
    prompt_hash text NOT NULL,
    input_hash text NOT NULL,
    model text NOT NULL,
    risk_policy_version integer NOT NULL,
    context_image_hash text NOT NULL,
    detail_image_hash text NOT NULL,
    context_image bytea NOT NULL,
    detail_image bytea NOT NULL,
    provider_request_id text,
    provider_usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    provider_cost numeric(18, 8) NOT NULL DEFAULT 0,
    structured_output jsonb,
    error_type text,
    error_message text,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (automation_run_id, screening_result_id, attempt_number)
);

CREATE INDEX IF NOT EXISTS proposal_attempts_run_status_idx
ON proposal_attempts (automation_run_id, status, started_at);

CREATE OR REPLACE FUNCTION enforce_trade_proposal_immutability()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF (to_jsonb(NEW) - ARRAY['status', 'updated_at'])
       IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['status', 'updated_at']) THEN
        RAISE EXCEPTION 'approved trade proposal payload is immutable';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trade_proposals_immutable_payload ON trade_proposals;
CREATE TRIGGER trade_proposals_immutable_payload
BEFORE UPDATE ON trade_proposals
FOR EACH ROW
EXECUTE FUNCTION enforce_trade_proposal_immutability();

INSERT INTO system_controls (control_key, enabled, reason, changed_by)
VALUES
    ('proposal_processing_paused', false, 'Default: proposal processing enabled when configured.', 'migration'),
    ('new_entries_paused', false, 'Default: approved initial and add legs may execute.', 'migration')
ON CONFLICT (control_key) DO NOTHING;

COMMIT;
