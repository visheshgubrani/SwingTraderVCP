-- P10 breakout-bar RVOL: versioned trigger semantics, durable reset state,
-- and explicit intraday volume/entry-eligibility evidence.

BEGIN;

ALTER TABLE trade_proposals
    ADD COLUMN IF NOT EXISTS entry_trigger_policy_version text
        NOT NULL DEFAULT 'cumulative_two_bar_v1';

ALTER TABLE trade_proposals
    DROP CONSTRAINT IF EXISTS trade_proposals_entry_trigger_policy_version_check;
ALTER TABLE trade_proposals
    ADD CONSTRAINT trade_proposals_entry_trigger_policy_version_check CHECK (
        entry_trigger_policy_version IN (
            'cumulative_two_bar_v1',
            'breakout_bar_signal_v2'
        )
    );

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
    risk_policy_version,
    entry_trigger_policy_version
);

ALTER TABLE entry_legs
    DROP CONSTRAINT IF EXISTS entry_legs_status_check;
ALTER TABLE entry_legs
    ADD CONSTRAINT entry_legs_status_check CHECK (
        status IN (
            'planned',
            'armed',
            'trigger_observed',
            'waiting_for_reset',
            'intent_created',
            'submitted',
            'partially_filled',
            'filled',
            'expired',
            'cancelled',
            'submission_unknown'
        )
    );

ALTER TABLE trigger_events
    ADD COLUMN IF NOT EXISTS expected_bar_volume bigint,
    ADD COLUMN IF NOT EXISTS bar_relative_volume numeric(8, 4),
    ADD COLUMN IF NOT EXISTS session_cumulative_relative_volume numeric(8, 4),
    ADD COLUMN IF NOT EXISTS recent_base_median_volume numeric(18, 4),
    ADD COLUMN IF NOT EXISTS volume_vs_recent_base numeric(8, 4),
    ADD COLUMN IF NOT EXISTS price_gate_passed boolean,
    ADD COLUMN IF NOT EXISTS volume_gate_passed boolean,
    ADD COLUMN IF NOT EXISTS trigger_outcome text,
    ADD COLUMN IF NOT EXISTS entry_eligibility_outcome text,
    ADD COLUMN IF NOT EXISTS entry_rejection_reason text;

ALTER TABLE trigger_events
    DROP CONSTRAINT IF EXISTS trigger_events_bar_type_check,
    DROP CONSTRAINT IF EXISTS trigger_events_trigger_outcome_check,
    DROP CONSTRAINT IF EXISTS trigger_events_entry_eligibility_outcome_check;

ALTER TABLE trigger_events
    ADD CONSTRAINT trigger_events_bar_type_check CHECK (
        bar_type IN ('signal_bar', 'confirmation_bar', 'reset_bar')
    ),
    ADD CONSTRAINT trigger_events_trigger_outcome_check CHECK (
        trigger_outcome IS NULL OR trigger_outcome IN (
            'signal_rejected',
            'waiting_confirmation',
            'confirmation_rejected',
            'confirmed',
            'reset'
        )
    ),
    ADD CONSTRAINT trigger_events_entry_eligibility_outcome_check CHECK (
        entry_eligibility_outcome IS NULL OR entry_eligibility_outcome IN (
            'pending',
            'eligible',
            'rejected_chase',
            'rejected_capacity',
            'rejected_preflight',
            'rejected_other'
        )
    );

COMMIT;
