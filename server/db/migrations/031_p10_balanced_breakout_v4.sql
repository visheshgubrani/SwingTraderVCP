-- P10 balanced_breakout_v4 entry trigger policy (AGENTS.md §5.4 / §6.1).
--
-- v4 keeps the initial leg armed for five NSE sessions (D1..D5), triggers on
-- a completed 5-minute close inside the buy zone (trigger < close <= chase
-- ceiling) with bar RVOL >= 1.25x OR session-cumulative RVOL >= 1.2x, and
-- re-arms after a chase / price-reversal rejection. Older policy rows stay
-- frozen; only the allowed-value list changes here.

BEGIN;

ALTER TABLE trade_proposals
    DROP CONSTRAINT IF EXISTS trade_proposals_entry_trigger_policy_version_check;
ALTER TABLE trade_proposals
    ADD CONSTRAINT trade_proposals_entry_trigger_policy_version_check CHECK (
        entry_trigger_policy_version IN (
            'cumulative_two_bar_v1',
            'breakout_bar_signal_v2',
            'balanced_breakout_v3',
            'balanced_breakout_v4'
        )
    );

COMMIT;
