-- P9: deterministic EOD market context, sector strength, and stop-streak guard.
-- Apply after 017_p10_review_hardening.sql.

BEGIN;

CREATE TABLE market_context_policies (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version text NOT NULL UNIQUE,
    mode text NOT NULL DEFAULT 'shadow' CHECK (mode IN ('shadow', 'enforced', 'retired')),
    config jsonb NOT NULL,
    replay_report_hash text,
    replay_membership_mode text CHECK (
        replay_membership_mode IS NULL OR replay_membership_mode IN (
            'point_in_time', 'current_membership_survivorship_biased'
        )
    ),
    approved_at timestamptz,
    approved_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT market_context_policy_enforcement_check CHECK (
        mode <> 'enforced'
        OR (replay_report_hash ~ '^[0-9a-f]{64}$'
            AND replay_membership_mode IS NOT NULL
            AND approved_at IS NOT NULL AND approved_by IS NOT NULL)
    )
);

CREATE TRIGGER market_context_policies_set_updated_at
BEFORE UPDATE ON market_context_policies
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE UNIQUE INDEX market_context_single_enforced_uidx
ON market_context_policies ((true)) WHERE mode = 'enforced';

INSERT INTO market_context_policies (version, mode, config)
VALUES (
    'market_context_v1',
    'shadow',
    '{
      "trend_symbols": ["NSE:NIFTY50-INDEX", "NSE:NIFTY500-INDEX", "NSE:NIFTYMIDCAP150-INDEX"],
      "breadth_green_above_pct": 50,
      "breadth_red_below_pct": 25,
      "distribution_window": 25,
      "distribution_down_pct": 0.5,
      "distribution_green_max": 3,
      "distribution_red_min": 6,
      "sector_formula": "63_126_60_40",
      "sector_tier_fraction": 0.30,
      "lagging_confirmation_sessions": 2,
      "multipliers": {"green": 1.0, "yellow": 0.5, "red": 0.0}
    }'::jsonb
)
ON CONFLICT (version) DO NOTHING;

ALTER TABLE market_regime_snapshots
    ADD COLUMN market_context_policy_id uuid REFERENCES market_context_policies(id),
    ADD COLUMN market_light text CHECK (
        market_light IS NULL OR market_light IN ('green', 'yellow', 'red', 'unavailable')
    ),
    ADD COLUMN exposure_multiplier numeric(4, 2) CHECK (
        exposure_multiplier IS NULL OR (exposure_multiplier >= 0 AND exposure_multiplier <= 1)
    ),
    ADD COLUMN trend_state text,
    ADD COLUMN breadth_state text,
    ADD COLUMN distribution_state text,
    ADD COLUMN source_hash text,
    ADD COLUMN data_quality jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE UNIQUE INDEX market_regime_p9_identity_uidx
ON market_regime_snapshots (reference_eod_date, market_context_policy_id, source_hash)
WHERE market_context_policy_id IS NOT NULL AND source_hash IS NOT NULL;

CREATE TABLE sector_strength_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reference_eod_date date NOT NULL,
    market_context_policy_id uuid NOT NULL REFERENCES market_context_policies(id),
    benchmark_symbol text NOT NULL DEFAULT 'NSE:NIFTY500-INDEX',
    taxonomy_version text NOT NULL,
    formula_version text NOT NULL,
    source_hash text NOT NULL,
    membership_mode text NOT NULL CHECK (
        membership_mode IN ('point_in_time', 'current_membership_survivorship_biased')
    ),
    status text NOT NULL CHECK (status IN ('complete', 'unavailable')),
    challenger_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    data_quality jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (reference_eod_date, market_context_policy_id, source_hash)
);

CREATE TABLE sector_strength_results (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES sector_strength_runs(id) ON DELETE CASCADE,
    sector_code text NOT NULL,
    sector_name text NOT NULL,
    index_instrument_id uuid REFERENCES instruments(id),
    index_symbol text NOT NULL,
    excess_return_42 numeric(18, 8),
    excess_return_63 numeric(18, 8),
    excess_return_126 numeric(18, 8),
    blended_score numeric(18, 8),
    ordinal_rank integer CHECK (ordinal_rank IS NULL OR ordinal_rank > 0),
    rs_rating integer CHECK (rs_rating IS NULL OR (rs_rating >= 1 AND rs_rating <= 99)),
    raw_tier text NOT NULL CHECK (raw_tier IN ('leading', 'neutral', 'lagging', 'unavailable')),
    gate_tier text NOT NULL CHECK (gate_tier IN ('leading', 'neutral', 'lagging', 'unavailable')),
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, sector_code)
);

CREATE INDEX sector_strength_runs_date_idx
ON sector_strength_runs (reference_eod_date DESC, created_at DESC);

CREATE INDEX sector_strength_results_run_rank_idx
ON sector_strength_results (run_id, ordinal_rank);

ALTER TABLE screening_results
    ADD COLUMN sector_strength_result_id uuid REFERENCES sector_strength_results(id),
    ADD COLUMN contextual_selection_rank integer CHECK (
        contextual_selection_rank IS NULL OR contextual_selection_rank > 0
    );

ALTER TABLE allocation_ledger
    ADD COLUMN market_regime_snapshot_id uuid REFERENCES market_regime_snapshots(id),
    ADD COLUMN sector_strength_result_id uuid REFERENCES sector_strength_results(id),
    ADD COLUMN market_context_policy_id uuid REFERENCES market_context_policies(id),
    ADD COLUMN market_context_mode text CHECK (
        market_context_mode IS NULL OR market_context_mode IN ('shadow', 'enforced')
    ),
    ADD COLUMN context_multiplier numeric(4, 2) CHECK (
        context_multiplier IS NULL OR (context_multiplier >= 0 AND context_multiplier <= 1)
    ),
    ADD COLUMN context_adjusted_risk_ceiling numeric(18, 4) CHECK (
        context_adjusted_risk_ceiling IS NULL OR context_adjusted_risk_ceiling >= 0
    ),
    ADD COLUMN context_gate_reasons jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE risk_policies
    ADD COLUMN consecutive_stop_limit integer NOT NULL DEFAULT 3 CHECK (
        consecutive_stop_limit > 0
    );

CREATE TABLE risk_stop_streak_state (
    execution_mode text PRIMARY KEY CHECK (execution_mode IN ('paper', 'live')),
    activation_watermark timestamptz NOT NULL DEFAULT now(),
    consecutive_count integer NOT NULL DEFAULT 0 CHECK (consecutive_count >= 0),
    tripped boolean NOT NULL DEFAULT false,
    tripped_at timestamptz,
    trip_position_id uuid REFERENCES positions(id),
    last_evaluated_closed_at timestamptz,
    last_evaluated_position_id uuid REFERENCES positions(id),
    owner_reset_at timestamptz,
    owner_reset_by text,
    owner_reset_reason text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT risk_stop_streak_trip_check CHECK (
        NOT tripped OR (tripped_at IS NOT NULL AND trip_position_id IS NOT NULL)
    )
);

INSERT INTO risk_stop_streak_state (execution_mode) VALUES ('paper'), ('live')
ON CONFLICT (execution_mode) DO NOTHING;

CREATE TABLE risk_stop_streak_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_mode text NOT NULL CHECK (execution_mode IN ('paper', 'live')),
    position_id uuid NOT NULL UNIQUE REFERENCES positions(id),
    closed_at timestamptz NOT NULL,
    classification text NOT NULL CHECK (classification IN ('increment', 'reset', 'ignored')),
    exit_purposes jsonb NOT NULL,
    estimated_net_pnl numeric(18, 4),
    charge_policy_version text,
    previous_count integer NOT NULL CHECK (previous_count >= 0),
    new_count integer NOT NULL CHECK (new_count >= 0),
    tripped boolean NOT NULL DEFAULT false,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX risk_stop_streak_events_mode_closed_idx
ON risk_stop_streak_events (execution_mode, closed_at, created_at);

INSERT INTO instruments (
    exchange, segment, symbol, trading_symbol, fyers_symbol, name,
    lot_size, tick_size, active, metadata
)
VALUES
    ('NSE','INDEX','NIFTYMIDCAP150','NIFTYMIDCAP150-INDEX','NSE:NIFTYMIDCAP150-INDEX','Nifty Midcap 150 Index',1,0.05,true,'{"role":"p9_trend_benchmark"}'::jsonb),
    ('NSE','INDEX','NIFTYAUTO','NIFTYAUTO-INDEX','NSE:NIFTYAUTO-INDEX','Nifty Auto',1,0.05,true,'{"role":"p9_sector_index","sector_code":"auto"}'::jsonb),
    ('NSE','INDEX','NIFTYBANK','NIFTYBANK-INDEX','NSE:NIFTYBANK-INDEX','Nifty Bank',1,0.05,true,'{"role":"p9_sector_index","sector_code":"bank"}'::jsonb),
    ('NSE','INDEX','NIFTYCHEMICALS','NIFTYCHEMICALS-INDEX','NSE:NIFTYCHEMICALS-INDEX','Nifty Chemicals',1,0.05,true,'{"role":"p9_sector_index","sector_code":"chemicals"}'::jsonb),
    ('NSE','INDEX','NIFTYCONSRDURBL','NIFTYCONSRDURBL-INDEX','NSE:NIFTYCONSRDURBL-INDEX','Nifty Consumer Durables',1,0.05,true,'{"role":"p9_sector_index","sector_code":"consumer_durables"}'::jsonb),
    ('NSE','INDEX','FINNIFTY','FINNIFTY-INDEX','NSE:FINNIFTY-INDEX','Nifty Financial Services',1,0.05,true,'{"role":"p9_sector_index","sector_code":"financial_services"}'::jsonb),
    ('NSE','INDEX','NIFTYFMCG','NIFTYFMCG-INDEX','NSE:NIFTYFMCG-INDEX','Nifty FMCG',1,0.05,true,'{"role":"p9_sector_index","sector_code":"fmcg"}'::jsonb),
    ('NSE','INDEX','NIFTYHEALTHCARE','NIFTYHEALTHCARE-INDEX','NSE:NIFTYHEALTHCARE-INDEX','Nifty Healthcare',1,0.05,true,'{"role":"p9_sector_index","sector_code":"healthcare"}'::jsonb),
    ('NSE','INDEX','NIFTYIT','NIFTYIT-INDEX','NSE:NIFTYIT-INDEX','Nifty IT',1,0.05,true,'{"role":"p9_sector_index","sector_code":"it"}'::jsonb),
    ('NSE','INDEX','NIFTYMEDIA','NIFTYMEDIA-INDEX','NSE:NIFTYMEDIA-INDEX','Nifty Media',1,0.05,true,'{"role":"p9_sector_index","sector_code":"media"}'::jsonb),
    ('NSE','INDEX','NIFTYMETAL','NIFTYMETAL-INDEX','NSE:NIFTYMETAL-INDEX','Nifty Metal',1,0.05,true,'{"role":"p9_sector_index","sector_code":"metal"}'::jsonb),
    ('NSE','INDEX','NIFTYOILANDGAS','NIFTYOILANDGAS-INDEX','NSE:NIFTYOILANDGAS-INDEX','Nifty Oil & Gas',1,0.05,true,'{"role":"p9_sector_index","sector_code":"oil_gas"}'::jsonb),
    ('NSE','INDEX','NIFTYPHARMA','NIFTYPHARMA-INDEX','NSE:NIFTYPHARMA-INDEX','Nifty Pharma',1,0.05,true,'{"role":"p9_sector_index","sector_code":"pharma"}'::jsonb),
    ('NSE','INDEX','NIFTYPOWER','NIFTYPOWER-INDEX','NSE:NIFTYENERGY-INDEX','Nifty Power',1,0.05,true,'{"role":"p9_sector_index","sector_code":"power"}'::jsonb),
    ('NSE','INDEX','NIFTYREALTY','NIFTYREALTY-INDEX','NSE:NIFTYREALTY-INDEX','Nifty Realty',1,0.05,true,'{"role":"p9_sector_index","sector_code":"realty"}'::jsonb),
    ('NSE','INDEX','NIFTYINFRA','NIFTYINFRA-INDEX','NSE:NIFTYINFRA-INDEX','Nifty Infrastructure',1,0.05,true,'{"role":"p9_sector_index","sector_code":"infrastructure"}'::jsonb),
    ('NSE','INDEX','NIFTYSERVICESECTOR','NIFTYSERVICESECTOR-INDEX','NSE:NIFTYSERVSECTOR-INDEX','Nifty Services Sector',1,0.05,true,'{"role":"p9_sector_index","sector_code":"services"}'::jsonb)
ON CONFLICT (fyers_symbol) DO UPDATE SET
    active = true,
    metadata = instruments.metadata || EXCLUDED.metadata;

COMMIT;
