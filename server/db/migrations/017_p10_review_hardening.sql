-- Review hardening for installations that already applied P10 migrations.

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
    ADD COLUMN IF NOT EXISTS geometry_version text NOT NULL
        DEFAULT 'p10_geometry_three_windows_v2';
ALTER TABLE trade_proposals
    ALTER COLUMN prompt_version SET DEFAULT 'p10_vcp_proposal_v2',
    ALTER COLUMN schema_version SET DEFAULT 'gemini_vcp_proposal_output_v2',
    ALTER COLUMN geometry_version SET DEFAULT 'p10_geometry_three_windows_v2';

ALTER TABLE proposal_attempts
    ADD COLUMN IF NOT EXISTS geometry_version text NOT NULL
        DEFAULT 'p10_geometry_three_windows_v2';
ALTER TABLE proposal_attempts
    ALTER COLUMN geometry_version SET DEFAULT 'p10_geometry_three_windows_v2';
ALTER TABLE proposal_attempts
    ADD COLUMN IF NOT EXISTS prompt_hash text,
    ADD COLUMN IF NOT EXISTS input_hash text;

ALTER TABLE capacity_conflicts
    ADD COLUMN IF NOT EXISTS executed_at timestamptz;

DROP INDEX IF EXISTS trade_proposals_input_version_uidx;
CREATE UNIQUE INDEX trade_proposals_input_version_uidx
ON trade_proposals (
    screening_result_id, source_hash, model, prompt_version, schema_version,
    renderer_version, geometry_version, risk_policy_version
);

CREATE UNIQUE INDEX IF NOT EXISTS risk_policies_single_active_uidx
ON risk_policies ((true)) WHERE is_active = true;

COMMIT;
