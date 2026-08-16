-- P10: System-Generated, Human-Approved Trade Automation
-- Schema migration for EOD proposal pipeline, approval checkpoints,
-- intraday entry supervisor, multi-leg add progression, and allocation ledger.

BEGIN;

-- 1. Automation Runs (EOD proposal batch runs)
CREATE TABLE IF NOT EXISTS automation_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_run_id uuid NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'running' CHECK (
        status IN ('running', 'completed', 'timed_out', 'failed')
    ),
    candidates_total integer NOT NULL DEFAULT 0,
    candidates_processed integer NOT NULL DEFAULT 0,
    proposals_generated integer NOT NULL DEFAULT 0,
    proposals_rejected integer NOT NULL DEFAULT 0,
    proposals_uncertain integer NOT NULL DEFAULT 0,
    proposals_failed integer NOT NULL DEFAULT 0,
    batch_deadline timestamptz NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    error_code text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER automation_runs_set_updated_at
BEFORE UPDATE ON automation_runs
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE INDEX IF NOT EXISTS automation_runs_scan_run_idx ON automation_runs(scan_run_id);
CREATE INDEX IF NOT EXISTS automation_runs_status_created_idx ON automation_runs(status, created_at DESC);


-- 2. Trade Proposals (Immutable system-generated trade plans)
CREATE TABLE IF NOT EXISTS trade_proposals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    automation_run_id uuid REFERENCES automation_runs(id) ON DELETE SET NULL,
    screening_result_id uuid NOT NULL REFERENCES screening_results(id) ON DELETE CASCADE,
    instrument_id uuid NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    symbol text NOT NULL,
    as_of_date date NOT NULL,
    status text NOT NULL DEFAULT 'pending_approval' CHECK (
        status IN ('pending_approval', 'approved', 'rejected', 'expired_unapproved')
    ),
    approval_deadline timestamptz NOT NULL, -- 09:00 IST on D1
    entry_session_date date NOT NULL,      -- D1 session
    proposal_hash text NOT NULL,
    source_hash text NOT NULL,
    renderer_version text NOT NULL,
    model text NOT NULL,
    confidence numeric(5, 4) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    entry_template text NOT NULL CHECK (
        entry_template IN ('single', 'two_leg', 'three_leg_front', 'three_leg_balanced')
    ),
    pivot_price numeric(18, 4) NOT NULL CHECK (pivot_price > 0),
    initial_stop numeric(18, 4) NOT NULL CHECK (initial_stop > 0),
    stop_distance_pct numeric(8, 4) NOT NULL CHECK (stop_distance_pct > 0 AND stop_distance_pct <= 8.0),
    chase_ceiling numeric(18, 4) NOT NULL CHECK (chase_ceiling >= pivot_price),
    t1 numeric(18, 4) NOT NULL CHECK (t1 > pivot_price),
    t2 numeric(18, 4) NOT NULL CHECK (t2 > t1),
    t3 numeric(18, 4) NOT NULL CHECK (t3 > t2),
    risk_budget_pct numeric(8, 4) NOT NULL DEFAULT 1.0 CHECK (risk_budget_pct > 0),
    leg_count integer NOT NULL CHECK (leg_count IN (1, 2, 3)),
    leg_risk_allocations jsonb NOT NULL,
    relative_volume_threshold numeric(6, 2) NOT NULL CHECK (relative_volume_threshold > 0),
    gemini_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    geometry jsonb NOT NULL DEFAULT '{}'::jsonb,
    context_image_hash text,
    detail_image_hash text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT trade_proposals_leg_allocations_check CHECK (jsonb_typeof(leg_risk_allocations) = 'array')
);

CREATE TRIGGER trade_proposals_set_updated_at
BEFORE UPDATE ON trade_proposals
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE INDEX IF NOT EXISTS trade_proposals_status_deadline_idx ON trade_proposals(status, approval_deadline);
CREATE INDEX IF NOT EXISTS trade_proposals_symbol_as_of_date_idx ON trade_proposals(symbol, as_of_date);
CREATE INDEX IF NOT EXISTS trade_proposals_screening_result_idx ON trade_proposals(screening_result_id);


-- 3. Proposal Decisions (Append-only human audit trail)
CREATE TABLE IF NOT EXISTS proposal_decisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    proposal_id uuid NOT NULL REFERENCES trade_proposals(id) ON DELETE CASCADE,
    decision text NOT NULL CHECK (decision IN ('approved', 'rejected')),
    expected_proposal_hash text NOT NULL,
    notes text,
    decided_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS proposal_decisions_proposal_idx ON proposal_decisions(proposal_id);


-- 4. Entry Legs (Multi-leg execution schedule)
CREATE TABLE IF NOT EXISTS entry_legs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    proposal_id uuid NOT NULL REFERENCES trade_proposals(id) ON DELETE CASCADE,
    leg_index integer NOT NULL CHECK (leg_index >= 1 AND leg_index <= 3),
    risk_allocation_pct numeric(6, 4) NOT NULL CHECK (risk_allocation_pct > 0 AND risk_allocation_pct <= 1.0),
    status text NOT NULL DEFAULT 'planned' CHECK (
        status IN (
            'planned',
            'armed',
            'trigger_observed',
            'intent_created',
            'submitted',
            'partially_filled',
            'filled',
            'expired',
            'cancelled',
            'submission_unknown'
        )
    ),
    trigger_type text NOT NULL CHECK (trigger_type IN ('pivot', 'base_breakout')),
    trigger_price numeric(18, 4) NOT NULL CHECK (trigger_price > 0),
    chase_ceiling numeric(18, 4) NOT NULL CHECK (chase_ceiling >= trigger_price),
    relative_volume_threshold numeric(6, 2) NOT NULL CHECK (relative_volume_threshold > 0),
    hold_required integer NOT NULL DEFAULT 0,
    base_required integer NOT NULL DEFAULT 0,
    hold_count integer NOT NULL DEFAULT 0,
    base_count integer NOT NULL DEFAULT 0,
    base_low numeric(18, 4),
    base_high numeric(18, 4),
    eligible_session_start date NOT NULL,
    eligible_session_end date NOT NULL,
    filled_shares integer NOT NULL DEFAULT 0 CHECK (filled_shares >= 0),
    filled_avg_price numeric(18, 4),
    position_id uuid REFERENCES positions(id) ON DELETE SET NULL,
    order_intent_id uuid REFERENCES order_intents(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (proposal_id, leg_index)
);

CREATE TRIGGER entry_legs_set_updated_at
BEFORE UPDATE ON entry_legs
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE INDEX IF NOT EXISTS entry_legs_status_idx ON entry_legs(status);
CREATE INDEX IF NOT EXISTS entry_legs_position_idx ON entry_legs(position_id);


-- 5. Trigger Events (Audited 5m intraday observations)
CREATE TABLE IF NOT EXISTS trigger_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    leg_id uuid NOT NULL REFERENCES entry_legs(id) ON DELETE CASCADE,
    bar_timestamp timestamptz NOT NULL,
    bar_open numeric(18, 4) NOT NULL,
    bar_high numeric(18, 4) NOT NULL,
    bar_low numeric(18, 4) NOT NULL,
    bar_close numeric(18, 4) NOT NULL,
    bar_volume bigint NOT NULL,
    cumulative_volume bigint NOT NULL,
    expected_cumulative_volume bigint NOT NULL,
    relative_volume numeric(8, 4) NOT NULL,
    bar_type text NOT NULL CHECK (bar_type IN ('signal_bar', 'confirmation_bar')),
    is_confirmed boolean NOT NULL DEFAULT false,
    chase_valid boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS trigger_events_leg_idx ON trigger_events(leg_id, bar_timestamp);


-- 6. Capacity Conflicts (Simultaneous competition for portfolio capacity)
CREATE TABLE IF NOT EXISTS capacity_conflicts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bar_timestamp timestamptz NOT NULL,
    competing_leg_ids jsonb NOT NULL,
    scanner_score numeric(8, 4) NOT NULL,
    status text NOT NULL DEFAULT 'open' CHECK (
        status IN ('open', 'resolved', 'expired_skipped')
    ),
    chosen_leg_id uuid REFERENCES entry_legs(id) ON DELETE SET NULL,
    resolution_type text CHECK (
        resolution_type IS NULL OR resolution_type IN ('operator_selected', 'operator_skipped', 'auto_expired')
    ),
    decided_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT capacity_conflicts_competing_legs_check CHECK (jsonb_typeof(competing_leg_ids) = 'array')
);

CREATE TRIGGER capacity_conflicts_set_updated_at
BEFORE UPDATE ON capacity_conflicts
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE INDEX IF NOT EXISTS capacity_conflicts_status_idx ON capacity_conflicts(status);


-- 7. Risk Policies (Versioned portfolio risk and concentration constraints)
CREATE TABLE IF NOT EXISTS risk_policies (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version integer NOT NULL UNIQUE,
    name text NOT NULL DEFAULT 'Balanced',
    is_active boolean NOT NULL DEFAULT false,
    risk_per_trade_pct numeric(6, 4) NOT NULL DEFAULT 0.0100 CHECK (risk_per_trade_pct > 0),
    max_total_open_risk_pct numeric(6, 4) NOT NULL DEFAULT 0.0400 CHECK (max_total_open_risk_pct > 0),
    max_single_name_notional_pct numeric(6, 4) NOT NULL DEFAULT 0.1500 CHECK (max_single_name_notional_pct > 0),
    max_sector_notional_pct numeric(6, 4) NOT NULL DEFAULT 0.3000 CHECK (max_sector_notional_pct > 0),
    max_cluster_notional_pct numeric(6, 4) NOT NULL DEFAULT 0.3000 CHECK (max_cluster_notional_pct > 0),
    correlation_cluster_threshold numeric(4, 2) NOT NULL DEFAULT 0.80 CHECK (correlation_cluster_threshold >= 0 AND correlation_cluster_threshold <= 1.0),
    correlation_lookback_sessions integer NOT NULL DEFAULT 60 CHECK (correlation_lookback_sessions > 0),
    daily_loss_limit_pct numeric(6, 4) NOT NULL DEFAULT 0.0200 CHECK (daily_loss_limit_pct > 0),
    max_open_positions integer NOT NULL DEFAULT 8 CHECK (max_open_positions > 0),
    deployable_capital_override numeric(18, 4),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER risk_policies_set_updated_at
BEFORE UPDATE ON risk_policies
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- Seed initial Balanced policy version 1
INSERT INTO risk_policies (version, name, is_active, risk_per_trade_pct, max_total_open_risk_pct, max_single_name_notional_pct, max_sector_notional_pct, max_cluster_notional_pct, correlation_cluster_threshold, correlation_lookback_sessions, daily_loss_limit_pct, max_open_positions)
VALUES (1, 'Balanced', true, 0.0100, 0.0400, 0.1500, 0.3000, 0.3000, 0.80, 60, 0.0200, 8)
ON CONFLICT (version) DO NOTHING;


-- 8. Allocation Ledger (Preflight, sizing, and post-fill risk events under PG advisory lock)
CREATE TABLE IF NOT EXISTS allocation_ledger (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    generation integer NOT NULL,
    leg_id uuid REFERENCES entry_legs(id) ON DELETE SET NULL,
    event_type text NOT NULL CHECK (
        event_type IN (
            'preflight_check',
            'sizing_allocated',
            'order_submitted',
            'fill_recalculated',
            'tightened_stop',
            'risk_reduced_exit',
            'full_invalid_exit',
            'allocation_blocked'
        )
    ),
    broker_funds_available numeric(18, 4) NOT NULL,
    broker_snapshot_at timestamptz NOT NULL,
    open_risk_before numeric(18, 4) NOT NULL,
    open_risk_after numeric(18, 4) NOT NULL,
    allocated_shares integer NOT NULL DEFAULT 0,
    allocated_risk_amount numeric(18, 4) NOT NULL DEFAULT 0,
    allocated_notional numeric(18, 4) NOT NULL DEFAULT 0,
    rounding_residual numeric(18, 4) NOT NULL DEFAULT 0,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS allocation_ledger_leg_idx ON allocation_ledger(leg_id);
CREATE INDEX IF NOT EXISTS allocation_ledger_created_idx ON allocation_ledger(created_at DESC);


-- 9. Five Minute Bars (Intraday completed bars for trigger confirmation)
CREATE TABLE IF NOT EXISTS five_minute_bars (
    id bigserial PRIMARY KEY,
    symbol text NOT NULL,
    bar_time timestamptz NOT NULL,
    open numeric(18, 4) NOT NULL,
    high numeric(18, 4) NOT NULL,
    low numeric(18, 4) NOT NULL,
    close numeric(18, 4) NOT NULL,
    volume bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (symbol, bar_time)
);

CREATE INDEX IF NOT EXISTS five_minute_bars_symbol_time_idx ON five_minute_bars(symbol, bar_time DESC);


-- 10. Volume Profiles (30-session median cumulative 5m volume distribution)
CREATE TABLE IF NOT EXISTS volume_profiles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol text NOT NULL,
    as_of_date date NOT NULL,
    adv20_robust bigint NOT NULL,
    bucket_medians jsonb NOT NULL,
    sessions_used integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (symbol, as_of_date),
    CONSTRAINT volume_profiles_bucket_medians_check CHECK (jsonb_typeof(bucket_medians) = 'array')
);

CREATE INDEX IF NOT EXISTS volume_profiles_symbol_date_idx ON volume_profiles(symbol, as_of_date DESC);


-- 11. Extensions on existing tables (positions, order_intents, order_fills)
ALTER TABLE positions
    ADD COLUMN IF NOT EXISTS proposal_id uuid REFERENCES trade_proposals(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS entry_template text,
    ADD COLUMN IF NOT EXISTS trailing_rule_type text,
    ADD COLUMN IF NOT EXISTS high_water_mark numeric(18, 4),
    ADD COLUMN IF NOT EXISTS trailing_stop numeric(18, 4),
    ADD COLUMN IF NOT EXISTS t1_target numeric(18, 4),
    ADD COLUMN IF NOT EXISTS t2_target numeric(18, 4),
    ADD COLUMN IF NOT EXISTS t3_target numeric(18, 4),
    ADD COLUMN IF NOT EXISTS t1_shares integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS t2_shares integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS t3_shares integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS runner_shares integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS t1_filled_shares integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS t2_filled_shares integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS t3_filled_shares integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS runner_filled_shares integer NOT NULL DEFAULT 0;

ALTER TABLE order_intents
    ADD COLUMN IF NOT EXISTS proposal_id uuid REFERENCES trade_proposals(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS entry_leg_id uuid REFERENCES entry_legs(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS exit_purpose text CHECK (
        exit_purpose IS NULL OR exit_purpose IN (
            'stop_loss',
            'target_1',
            'target_2',
            'target_3',
            'runner_trail',
            'risk_reduction',
            'invalid_fill',
            'manual'
        )
    );

ALTER TABLE order_fills
    ADD COLUMN IF NOT EXISTS proposal_id uuid REFERENCES trade_proposals(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS entry_leg_id uuid REFERENCES entry_legs(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS positions_proposal_idx ON positions(proposal_id);
CREATE INDEX IF NOT EXISTS order_intents_proposal_idx ON order_intents(proposal_id);
CREATE INDEX IF NOT EXISTS order_intents_leg_idx ON order_intents(entry_leg_id);

COMMIT;
