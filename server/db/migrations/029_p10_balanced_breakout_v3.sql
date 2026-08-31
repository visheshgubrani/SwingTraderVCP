-- P10 balanced_breakout_v3 trigger policy and price reversal eligibility outcome.

BEGIN;

-- Add balanced_breakout_v3 to entry_trigger_policy_version check constraint.
-- Preserves the fail-safe DEFAULT 'cumulative_two_bar_v1'.
ALTER TABLE trade_proposals
    DROP CONSTRAINT IF EXISTS trade_proposals_entry_trigger_policy_version_check;
ALTER TABLE trade_proposals
    ADD CONSTRAINT trade_proposals_entry_trigger_policy_version_check CHECK (
        entry_trigger_policy_version IN (
            'cumulative_two_bar_v1',
            'breakout_bar_signal_v2',
            'balanced_breakout_v3'
        )
    );

-- Add rejected_price_reversal to entry_eligibility_outcome check constraint.
ALTER TABLE trigger_events
    DROP CONSTRAINT IF EXISTS trigger_events_entry_eligibility_outcome_check;
ALTER TABLE trigger_events
    ADD CONSTRAINT trigger_events_entry_eligibility_outcome_check CHECK (
        entry_eligibility_outcome IS NULL OR entry_eligibility_outcome IN (
            'pending',
            'eligible',
            'rejected_chase',
            'rejected_price_reversal',
            'rejected_capacity',
            'rejected_preflight',
            'rejected_other'
        )
    );

COMMIT;
