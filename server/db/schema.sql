CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TABLE instruments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    exchange text NOT NULL,
    segment text NOT NULL,
    symbol text NOT NULL,
    trading_symbol text NOT NULL,
    fyers_symbol text NOT NULL UNIQUE,
    isin text UNIQUE,
    name text,
    lot_size integer NOT NULL DEFAULT 1 CHECK (lot_size > 0),
    tick_size numeric(12, 6) NOT NULL DEFAULT 0.05 CHECK (tick_size > 0),
    active boolean NOT NULL DEFAULT true,
    active_from date,
    active_to date,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT instruments_exchange_symbol_unique UNIQUE (exchange, symbol),
    CONSTRAINT instruments_active_dates_check CHECK (
        active_to IS NULL OR active_from IS NULL OR active_to >= active_from
    )
);

CREATE TRIGGER instruments_set_updated_at
BEFORE UPDATE ON instruments
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE universe_memberships (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id uuid NOT NULL REFERENCES instruments(id),
    universe_code text NOT NULL,
    member_from date NOT NULL,
    member_to date,
    source text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT universe_memberships_dates_check CHECK (
        member_to IS NULL OR member_to >= member_from
    ),
    CONSTRAINT universe_memberships_unique_start UNIQUE (
        instrument_id,
        universe_code,
        member_from
    )
);

CREATE INDEX universe_memberships_current_idx
ON universe_memberships (universe_code, instrument_id)
WHERE member_to IS NULL;

CREATE TABLE market_candles (
    instrument_id uuid NOT NULL REFERENCES instruments(id),
    timeframe text NOT NULL,
    candle_start timestamptz NOT NULL,
    open_price numeric(18, 4) NOT NULL CHECK (open_price >= 0),
    high_price numeric(18, 4) NOT NULL CHECK (high_price >= 0),
    low_price numeric(18, 4) NOT NULL CHECK (low_price >= 0),
    close_price numeric(18, 4) NOT NULL CHECK (close_price >= 0),
    volume bigint NOT NULL DEFAULT 0 CHECK (volume >= 0),
    source text NOT NULL DEFAULT 'fyers',
    fetched_at timestamptz NOT NULL DEFAULT now(),
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (instrument_id, timeframe, candle_start),
    CONSTRAINT market_candles_timeframe_check CHECK (
        timeframe IN ('1m', '3m', '5m', '15m', '30m', '60m', '1d', '1w')
    ),
    CONSTRAINT market_candles_ohlc_check CHECK (
        high_price >= low_price
        AND high_price >= open_price
        AND high_price >= close_price
        AND low_price <= open_price
        AND low_price <= close_price
    )
) PARTITION BY RANGE (candle_start);

CREATE TABLE market_candles_default
PARTITION OF market_candles DEFAULT;

CREATE INDEX market_candles_symbol_range_idx
ON market_candles (instrument_id, timeframe, candle_start DESC);

CREATE TABLE market_ticks (
    tick_id uuid NOT NULL DEFAULT gen_random_uuid(),
    instrument_id uuid NOT NULL REFERENCES instruments(id),
    tick_ts timestamptz NOT NULL,
    last_price numeric(18, 4) NOT NULL CHECK (last_price >= 0),
    last_quantity integer CHECK (last_quantity IS NULL OR last_quantity >= 0),
    volume bigint CHECK (volume IS NULL OR volume >= 0),
    bid_price numeric(18, 4) CHECK (bid_price IS NULL OR bid_price >= 0),
    ask_price numeric(18, 4) CHECK (ask_price IS NULL OR ask_price >= 0),
    source text NOT NULL DEFAULT 'fyers_ws',
    source_sequence text,
    received_at timestamptz NOT NULL DEFAULT now(),
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (tick_ts, tick_id)
) PARTITION BY RANGE (tick_ts);

CREATE TABLE market_ticks_default
PARTITION OF market_ticks DEFAULT;

CREATE INDEX market_ticks_symbol_range_idx
ON market_ticks (instrument_id, tick_ts DESC);

CREATE INDEX market_ticks_received_idx
ON market_ticks (received_at DESC);

CREATE TABLE scan_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    universe_code text NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    triggered_by text NOT NULL,
    technical_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    llm_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    started_at timestamptz,
    completed_at timestamptz,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT scan_runs_status_check CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')
    ),
    CONSTRAINT scan_runs_dates_check CHECK (
        completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at
    )
);

CREATE INDEX scan_runs_created_idx
ON scan_runs (created_at DESC);

CREATE TABLE fundamental_snapshots (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id uuid NOT NULL REFERENCES instruments(id),
    provider text NOT NULL,
    statement_type text NOT NULL DEFAULT 'consolidated',
    fetched_at timestamptz NOT NULL DEFAULT now(),
    latest_annual_period text,
    latest_quarterly_period text,
    raw_payload jsonb NOT NULL,
    normalized_facts jsonb NOT NULL,
    content_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fundamental_snapshots_statement_type_check CHECK (
        statement_type IN ('consolidated', 'standalone')
    )
);

CREATE INDEX fundamental_snapshots_instrument_fetched_idx
ON fundamental_snapshots (instrument_id, provider, statement_type, fetched_at DESC);

CREATE INDEX fundamental_snapshots_content_hash_idx
ON fundamental_snapshots (content_hash);

CREATE TABLE screening_results (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_run_id uuid NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    instrument_id uuid NOT NULL REFERENCES instruments(id),
    result_rank integer CHECK (result_rank IS NULL OR result_rank > 0),
    technical_passed boolean NOT NULL DEFAULT true,
    vcp_detected boolean NOT NULL DEFAULT false,
    close_price numeric(18, 4) CHECK (close_price IS NULL OR close_price >= 0),
    sma_50 numeric(18, 4) CHECK (sma_50 IS NULL OR sma_50 >= 0),
    sma_200 numeric(18, 4) CHECK (sma_200 IS NULL OR sma_200 >= 0),
    avg_volume_20 bigint CHECK (avg_volume_20 IS NULL OR avg_volume_20 >= 0),
    pct_from_52w_high numeric(8, 4),
    technical_metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    llm_status text NOT NULL DEFAULT 'not_requested',
    llm_verdict text,
    llm_flags jsonb NOT NULL DEFAULT '{}'::jsonb,
    llm_checked_at timestamptz,
    fundamental_snapshot_id uuid REFERENCES fundamental_snapshots(id),
    reviewer_status text NOT NULL DEFAULT 'pending',
    reviewer_notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT screening_results_unique_symbol UNIQUE (scan_run_id, instrument_id),
    CONSTRAINT screening_results_llm_status_check CHECK (
        llm_status IN ('not_requested', 'queued', 'running', 'succeeded', 'failed', 'skipped')
    ),
    CONSTRAINT screening_results_llm_verdict_check CHECK (
        llm_verdict IS NULL OR llm_verdict IN ('pass', 'fail', 'uncertain')
    ),
    CONSTRAINT screening_results_reviewer_status_check CHECK (
        reviewer_status IN ('pending', 'watchlisted', 'rejected', 'trade_planned')
    )
);

CREATE INDEX screening_results_scan_rank_idx
ON screening_results (scan_run_id, result_rank);

CREATE INDEX screening_results_instrument_created_idx
ON screening_results (instrument_id, created_at DESC);

CREATE INDEX screening_results_fundamental_snapshot_idx
ON screening_results (fundamental_snapshot_id)
WHERE fundamental_snapshot_id IS NOT NULL;

CREATE TABLE watchlists (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL UNIQUE,
    description text,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER watchlists_set_updated_at
BEFORE UPDATE ON watchlists
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE watchlist_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    watchlist_id uuid NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
    instrument_id uuid NOT NULL REFERENCES instruments(id),
    screening_result_id uuid REFERENCES screening_results(id),
    notes text,
    added_at timestamptz NOT NULL DEFAULT now(),
    removed_at timestamptz,
    CONSTRAINT watchlist_items_dates_check CHECK (
        removed_at IS NULL OR removed_at >= added_at
    )
);

CREATE UNIQUE INDEX watchlist_items_active_unique_idx
ON watchlist_items (watchlist_id, instrument_id)
WHERE removed_at IS NULL;

CREATE TABLE trade_instructions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id uuid NOT NULL REFERENCES instruments(id),
    screening_result_id uuid REFERENCES screening_results(id),
    side text NOT NULL,
    quantity integer NOT NULL CHECK (quantity > 0),
    product_type text NOT NULL DEFAULT 'CNC',
    entry_order_type text NOT NULL,
    planned_entry_price numeric(18, 4) NOT NULL CHECK (planned_entry_price > 0),
    entry_limit_price numeric(18, 4) CHECK (
        entry_limit_price IS NULL OR entry_limit_price >= 0
    ),
    initial_stop_loss numeric(18, 4) NOT NULL CHECK (initial_stop_loss >= 0),
    initial_target numeric(18, 4) CHECK (initial_target IS NULL OR initial_target >= 0),
    trailing_rule jsonb NOT NULL DEFAULT '{}'::jsonb,
    risk_amount numeric(18, 4) CHECK (risk_amount IS NULL OR risk_amount >= 0),
    status text NOT NULL DEFAULT 'draft',
    manual_confirmed_at timestamptz,
    submitted_at timestamptz,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT trade_instructions_side_check CHECK (side IN ('buy', 'sell')),
    CONSTRAINT trade_instructions_entry_order_type_check CHECK (
        entry_order_type IN ('market', 'limit', 'stop', 'stop_limit')
    ),
    CONSTRAINT trade_instructions_product_type_check CHECK (
        product_type IN ('CNC')
    ),
    CONSTRAINT trade_instructions_status_check CHECK (
        status IN ('draft', 'confirmed', 'submitted', 'cancelled', 'rejected')
    ),
    CONSTRAINT trade_instructions_manual_checkpoint_check CHECK (
        status IN ('draft', 'cancelled') OR manual_confirmed_at IS NOT NULL
    )
);

CREATE TRIGGER trade_instructions_set_updated_at
BEFORE UPDATE ON trade_instructions
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE INDEX trade_instructions_screening_result_idx
ON trade_instructions (screening_result_id)
WHERE screening_result_id IS NOT NULL;

CREATE TABLE positions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id uuid NOT NULL REFERENCES instruments(id),
    trade_instruction_id uuid REFERENCES trade_instructions(id),
    screening_result_id uuid REFERENCES screening_results(id),
    state text NOT NULL DEFAULT 'pending_entry',
    side text NOT NULL,
    quantity integer NOT NULL CHECK (quantity > 0),
    open_quantity integer NOT NULL CHECK (open_quantity >= 0),
    product_type text NOT NULL DEFAULT 'CNC',
    average_entry_price numeric(18, 4) CHECK (
        average_entry_price IS NULL OR average_entry_price >= 0
    ),
    current_stop_loss numeric(18, 4) CHECK (
        current_stop_loss IS NULL OR current_stop_loss >= 0
    ),
    current_target numeric(18, 4) CHECK (
        current_target IS NULL OR current_target >= 0
    ),
    trailing_rule jsonb NOT NULL DEFAULT '{}'::jsonb,
    realized_pnl numeric(18, 4) NOT NULL DEFAULT 0,
    opened_at timestamptz,
    closed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT positions_state_check CHECK (
        state IN (
            'pending_entry',
            'open',
            'trailing_active',
            'exit_pending',
            'closed',
            'cancelled'
        )
    ),
    CONSTRAINT positions_side_check CHECK (side IN ('long', 'short')),
    CONSTRAINT positions_product_type_check CHECK (product_type IN ('CNC')),
    CONSTRAINT positions_open_quantity_lte_quantity_check CHECK (open_quantity <= quantity),
    CONSTRAINT positions_dates_check CHECK (
        closed_at IS NULL OR opened_at IS NULL OR closed_at >= opened_at
    )
);

CREATE TRIGGER positions_set_updated_at
BEFORE UPDATE ON positions
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE INDEX positions_open_idx
ON positions (state, instrument_id)
WHERE state <> 'closed';

CREATE INDEX positions_screening_result_idx
ON positions (screening_result_id)
WHERE screening_result_id IS NOT NULL;

CREATE UNIQUE INDEX positions_trade_instruction_unique_idx
ON positions (trade_instruction_id)
WHERE trade_instruction_id IS NOT NULL;

CREATE TABLE position_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    position_id uuid NOT NULL REFERENCES positions(id),
    event_ts timestamptz NOT NULL DEFAULT now(),
    event_type text NOT NULL,
    from_state text,
    to_state text,
    trigger_source text NOT NULL,
    observed_price numeric(18, 4) CHECK (
        observed_price IS NULL OR observed_price >= 0
    ),
    stop_loss_price numeric(18, 4) CHECK (
        stop_loss_price IS NULL OR stop_loss_price >= 0
    ),
    target_price numeric(18, 4) CHECK (
        target_price IS NULL OR target_price >= 0
    ),
    trailing_rule jsonb NOT NULL DEFAULT '{}'::jsonb,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT position_events_trigger_source_check CHECK (
        trigger_source IN (
            'api',
            'execution_engine',
            'order_gateway',
            'position_monitor',
            'reconciliation',
            'manual_import'
        )
    )
);

CREATE INDEX position_events_position_ts_idx
ON position_events (position_id, event_ts, id);

CREATE TABLE order_intents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key text NOT NULL UNIQUE,
    trade_instruction_id uuid REFERENCES trade_instructions(id),
    position_id uuid REFERENCES positions(id),
    intent_type text NOT NULL,
    side text NOT NULL,
    quantity integer NOT NULL CHECK (quantity > 0),
    product_type text NOT NULL DEFAULT 'CNC',
    order_type text NOT NULL,
    limit_price numeric(18, 4) CHECK (limit_price IS NULL OR limit_price >= 0),
    trigger_price numeric(18, 4) CHECK (trigger_price IS NULL OR trigger_price >= 0),
    status text NOT NULL DEFAULT 'created',
    execution_mode text NOT NULL DEFAULT 'paper',
    fyers_async_id text,
    fyers_order_id text UNIQUE,
    exchange_order_id text,
    broker_requested_at timestamptz,
    broker_responded_at timestamptz,
    requested_by_component text NOT NULL,
    reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT order_intents_intent_type_check CHECK (
        intent_type IN ('entry', 'stop_loss_exit', 'target_exit', 'trailing_exit', 'manual_exit')
    ),
    CONSTRAINT order_intents_side_check CHECK (side IN ('buy', 'sell')),
    CONSTRAINT order_intents_order_type_check CHECK (
        order_type IN ('market', 'limit', 'stop', 'stop_limit')
    ),
    CONSTRAINT order_intents_product_type_check CHECK (
        product_type IN ('CNC')
    ),
    CONSTRAINT order_intents_execution_mode_check CHECK (
        execution_mode IN ('paper', 'live')
    ),
    CONSTRAINT order_intents_status_check CHECK (
        status IN (
            'created',
            'submission_pending',
            'submission_unknown',
            'submitted',
            'acknowledged',
            'partially_filled',
            'filled',
            'rejected',
            'cancel_requested',
            'cancelled'
        )
    ),
    CONSTRAINT order_intents_component_check CHECK (
        requested_by_component IN ('execution_engine')
    )
);

CREATE TRIGGER order_intents_set_updated_at
BEFORE UPDATE ON order_intents
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE INDEX order_intents_position_idx
ON order_intents (position_id, created_at DESC)
WHERE position_id IS NOT NULL;

CREATE UNIQUE INDEX order_intents_fyers_async_unique_idx
ON order_intents (fyers_async_id)
WHERE fyers_async_id IS NOT NULL;

CREATE UNIQUE INDEX order_intents_exchange_order_unique_idx
ON order_intents (exchange_order_id)
WHERE exchange_order_id IS NOT NULL;

CREATE UNIQUE INDEX order_intents_entry_instruction_unique_idx
ON order_intents (trade_instruction_id)
WHERE trade_instruction_id IS NOT NULL AND intent_type = 'entry';

CREATE TABLE order_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_intent_id uuid NOT NULL REFERENCES order_intents(id),
    event_ts timestamptz NOT NULL DEFAULT now(),
    event_type text NOT NULL,
    broker_event_key text NOT NULL,
    fyers_async_id text,
    fyers_order_id text,
    exchange_order_id text,
    fyers_status text,
    filled_quantity integer CHECK (filled_quantity IS NULL OR filled_quantity >= 0),
    average_price numeric(18, 4) CHECK (average_price IS NULL OR average_price >= 0),
    broker_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX order_events_intent_ts_idx
ON order_events (order_intent_id, event_ts, id);

CREATE UNIQUE INDEX order_events_broker_event_unique_idx
ON order_events (broker_event_key);

CREATE TABLE order_fills (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_intent_id uuid NOT NULL REFERENCES order_intents(id),
    fyers_trade_id text UNIQUE,
    filled_at timestamptz NOT NULL,
    quantity integer NOT NULL CHECK (quantity > 0),
    price numeric(18, 4) NOT NULL CHECK (price >= 0),
    charges jsonb NOT NULL DEFAULT '{}'::jsonb,
    broker_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX order_fills_intent_ts_idx
ON order_fills (order_intent_id, filled_at);

CREATE TABLE job_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type text NOT NULL,
    job_key text,
    triggered_by text NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    started_at timestamptz,
    completed_at timestamptz,
    input_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    result_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT job_runs_status_check CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')
    ),
    CONSTRAINT job_runs_dates_check CHECK (
        completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at
    )
);

CREATE INDEX job_runs_type_created_idx
ON job_runs (job_type, created_at DESC);

CREATE TABLE reconciliation_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    status text NOT NULL DEFAULT 'running',
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    discrepancies_found integer NOT NULL DEFAULT 0 CHECK (discrepancies_found >= 0),
    summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_message text,
    CONSTRAINT reconciliation_runs_status_check CHECK (
        status IN ('running', 'succeeded', 'failed')
    ),
    CONSTRAINT reconciliation_runs_dates_check CHECK (
        completed_at IS NULL OR completed_at >= started_at
    )
);

CREATE TABLE reconciliation_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reconciliation_run_id uuid NOT NULL REFERENCES reconciliation_runs(id) ON DELETE CASCADE,
    domain text NOT NULL,
    local_record_id text,
    broker_record_id text,
    issue_type text NOT NULL,
    severity text NOT NULL,
    local_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    broker_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    resolution_status text NOT NULL DEFAULT 'open',
    resolved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT reconciliation_items_severity_check CHECK (
        severity IN ('info', 'warning', 'critical')
    ),
    CONSTRAINT reconciliation_items_resolution_status_check CHECK (
        resolution_status IN ('open', 'ignored', 'resolved')
    )
);

CREATE INDEX reconciliation_items_run_idx
ON reconciliation_items (reconciliation_run_id, severity);

CREATE TABLE broker_auth_tokens (
    broker text NOT NULL,
    token_scope text NOT NULL,
    access_token_encrypted text NOT NULL,
    refresh_token_encrypted text,
    expires_at timestamptz NOT NULL,
    refreshed_at timestamptz NOT NULL DEFAULT now(),
    refresh_job_run_id uuid REFERENCES job_runs(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (broker, token_scope)
);

CREATE TRIGGER broker_auth_tokens_set_updated_at
BEFORE UPDATE ON broker_auth_tokens
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE system_controls (
    control_key text PRIMARY KEY,
    enabled boolean NOT NULL,
    reason text,
    changed_by text NOT NULL DEFAULT 'system',
    changed_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO system_controls (control_key, enabled, reason, changed_by)
VALUES ('global_kill_switch', false, 'Default: trading automation enabled only when higher-level services permit it.', 'schema')
ON CONFLICT (control_key) DO NOTHING;

CREATE TABLE system_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_ts timestamptz NOT NULL DEFAULT now(),
    component text NOT NULL,
    severity text NOT NULL,
    event_type text NOT NULL,
    correlation_id uuid,
    instrument_id uuid REFERENCES instruments(id),
    position_id uuid REFERENCES positions(id),
    order_intent_id uuid REFERENCES order_intents(id),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT system_events_severity_check CHECK (
        severity IN ('debug', 'info', 'warning', 'error', 'critical')
    )
);

CREATE INDEX system_events_component_ts_idx
ON system_events (component, event_ts DESC);

CREATE INDEX system_events_correlation_idx
ON system_events (correlation_id)
WHERE correlation_id IS NOT NULL;

COMMENT ON TABLE instruments IS
'Reference data for tradable instruments. Other domains reference instruments instead of duplicating symbol metadata.';

COMMENT ON TABLE market_candles IS
'Append-mostly candle time-series data. Use monthly range partitions in production and upsert only for source/backfill corrections.';

COMMENT ON TABLE market_ticks IS
'High-volume raw tick stream from the single backend Fyers WebSocket ingestion worker. Retain short-term or downsample per policy.';

COMMENT ON TABLE screening_results IS
'Shortlist rows produced by scan runs. LLM checks apply only to technical-pass survivors.';

COMMENT ON TABLE fundamental_snapshots IS
'Read-only provider payloads and deterministic normalized facts fetched only for persisted technical survivors.';

COMMENT ON TABLE trade_instructions IS
'Human decision checkpoint: stock, size, entry, stop loss, target, and trailing rule before any order intent is submitted.';

COMMENT ON TABLE positions IS
'Mutable current-state view for open/closed positions. The ordered truth lives in position_events and order logs.';

COMMENT ON TABLE position_events IS
'Append-only position state-machine transitions and monitor/execution observations.';

COMMENT ON TABLE order_intents IS
'Idempotent order intent records owned by the execution engine before broker calls.';

COMMENT ON TABLE order_events IS
'Append-only broker order lifecycle log.';

COMMENT ON TABLE job_runs IS
'Append-only operational job history for screening, backfills, token refreshes, and scheduler-driven work.';

COMMENT ON TABLE system_controls IS
'Operational controls including the global kill switch used by execution and monitoring services.';
