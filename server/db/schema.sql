CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Swyingify Better Auth tables. These are intentionally separate from the
-- personal money-path tables below; Better Auth writes them from Next.js.
CREATE TABLE "user" (
    id text PRIMARY KEY,
    name text NOT NULL,
    email text NOT NULL UNIQUE,
    "emailVerified" boolean NOT NULL DEFAULT false,
    image text,
    role text NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    "createdAt" timestamptz NOT NULL DEFAULT now(),
    "updatedAt" timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE session (
    id text PRIMARY KEY,
    "expiresAt" timestamptz NOT NULL,
    token text NOT NULL UNIQUE,
    "createdAt" timestamptz NOT NULL DEFAULT now(),
    "updatedAt" timestamptz NOT NULL DEFAULT now(),
    "ipAddress" text,
    "userAgent" text,
    "userId" text NOT NULL REFERENCES "user" (id) ON DELETE CASCADE
);

CREATE INDEX session_user_id_idx ON session ("userId");

CREATE TABLE account (
    id text PRIMARY KEY,
    "accountId" text NOT NULL,
    "providerId" text NOT NULL,
    "userId" text NOT NULL REFERENCES "user" (id) ON DELETE CASCADE,
    "accessToken" text,
    "refreshToken" text,
    "idToken" text,
    "accessTokenExpiresAt" timestamptz,
    "refreshTokenExpiresAt" timestamptz,
    scope text,
    password text,
    "createdAt" timestamptz NOT NULL DEFAULT now(),
    "updatedAt" timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX account_user_id_idx ON account ("userId");

CREATE TABLE verification (
    id text PRIMARY KEY,
    identifier text NOT NULL,
    value text NOT NULL,
    "expiresAt" timestamptz NOT NULL,
    "createdAt" timestamptz NOT NULL DEFAULT now(),
    "updatedAt" timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX verification_identifier_idx ON verification (identifier);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TABLE saas_subscriptions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id text NOT NULL REFERENCES "user" (id) ON DELETE CASCADE,
    provider text NOT NULL CHECK (provider IN ('manual', 'razorpay')),
    plan_code text NOT NULL CHECK (plan_code IN ('pro')),
    status text NOT NULL DEFAULT 'pending' CHECK (
        status IN (
            'pending', 'trialing', 'active', 'past_due', 'paused',
            'cancelled', 'expired'
        )
    ),
    provider_customer_id text,
    provider_subscription_id text,
    current_period_start timestamptz,
    current_period_end timestamptz,
    cancel_at_period_end boolean NOT NULL DEFAULT false,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX saas_subscriptions_user_status_idx
ON saas_subscriptions (user_id, status, current_period_end DESC);

CREATE UNIQUE INDEX saas_subscriptions_provider_reference_uidx
ON saas_subscriptions (provider, provider_subscription_id)
WHERE provider_subscription_id IS NOT NULL;

CREATE TRIGGER saas_subscriptions_set_updated_at
BEFORE UPDATE ON saas_subscriptions
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

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

-- Benchmark / RS index symbols (not in Nifty 500 equity membership).
-- Synced by historical EOD alongside the equity universe.
INSERT INTO instruments (
    exchange, segment, symbol, trading_symbol, fyers_symbol, name,
    lot_size, tick_size, active, metadata
)
VALUES
    (
        'NSE', 'INDEX', 'NIFTY50', 'NIFTY50-INDEX', 'NSE:NIFTY50-INDEX',
        'Nifty 50 Index', 1, 0.05, true,
        '{"role": "benchmark", "p8_regime": true}'::jsonb
    ),
    (
        'NSE', 'INDEX', 'NIFTY500', 'NIFTY500-INDEX', 'NSE:NIFTY500-INDEX',
        'Nifty 500 Index', 1, 0.05, true,
        '{"role": "rs_benchmark", "pipeline": "vcp_score_v3"}'::jsonb
    )
ON CONFLICT (fyers_symbol) DO NOTHING;

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

CREATE TABLE scan_templates (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    family text NOT NULL,
    code text NOT NULL,
    display_name text NOT NULL,
    access_tier text NOT NULL DEFAULT 'public',
    version integer NOT NULL DEFAULT 1,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT scan_templates_access_tier_check CHECK (
        access_tier IN ('public', 'auth', 'paid')
    ),
    CONSTRAINT scan_templates_family_code_version_unique UNIQUE (family, code, version)
);

CREATE INDEX scan_templates_active_idx
ON scan_templates (family, code)
WHERE is_active = true;

CREATE TABLE scan_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    universe_code text NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    triggered_by text NOT NULL,
    technical_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    llm_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    template_id uuid REFERENCES scan_templates(id),
    visibility text NOT NULL DEFAULT 'personal',
    owner_user_id text,
    as_of_date date,
    started_at timestamptz,
    completed_at timestamptz,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT scan_runs_status_check CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')
    ),
    CONSTRAINT scan_runs_visibility_check CHECK (
        visibility IN ('global', 'user', 'personal')
    ),
    CONSTRAINT scan_runs_dates_check CHECK (
        completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at
    )
);

CREATE INDEX scan_runs_created_idx
ON scan_runs (created_at DESC);

CREATE INDEX scan_runs_global_template_as_of_idx
ON scan_runs (template_id, as_of_date DESC, created_at DESC)
WHERE visibility = 'global';

CREATE UNIQUE INDEX scan_runs_global_template_as_of_succeeded_uidx
ON scan_runs (template_id, as_of_date)
WHERE visibility = 'global' AND status = 'succeeded' AND template_id IS NOT NULL;

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
    source_url text,
    filing_date timestamptz,
    revision_date timestamptz,
    reporting_period date,
    taxonomy_version text,
    parser_version text,
    fetch_status text NOT NULL DEFAULT 'succeeded',
    provider_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fundamental_snapshots_statement_type_check CHECK (
        statement_type IN ('consolidated', 'standalone', 'not_applicable')
    ),
    CONSTRAINT fundamental_snapshots_fetch_status_check CHECK (
        fetch_status IN ('succeeded', 'ambiguous', 'failed')
    )
);

CREATE INDEX fundamental_snapshots_instrument_fetched_idx
ON fundamental_snapshots (instrument_id, provider, statement_type, fetched_at DESC);

CREATE INDEX fundamental_snapshots_content_hash_idx
ON fundamental_snapshots (content_hash);

CREATE INDEX fundamental_snapshots_provider_period_idx
ON fundamental_snapshots (instrument_id, provider, reporting_period DESC, filing_date DESC);

CREATE TABLE screening_results (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_run_id uuid NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    instrument_id uuid NOT NULL REFERENCES instruments(id),
    result_rank integer CHECK (result_rank IS NULL OR result_rank > 0),
    technical_passed boolean NOT NULL DEFAULT true,
    technical_score numeric(5, 2) CHECK (
        technical_score IS NULL
        OR (technical_score >= 0 AND technical_score <= 100)
    ),
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
    fundamental_status text NOT NULL DEFAULT 'not_requested',
    fundamental_verdict text,
    fundamental_scorecard jsonb NOT NULL DEFAULT '{}'::jsonb,
    ai_status text NOT NULL DEFAULT 'not_requested',
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
    CONSTRAINT screening_results_fundamental_status_check CHECK (
        fundamental_status IN (
            'not_requested', 'queued', 'running', 'completed', 'failed', 'skipped'
        )
    ),
    CONSTRAINT screening_results_ai_status_check CHECK (
        ai_status IN (
            'not_requested', 'queued', 'running', 'succeeded', 'cached',
            'failed', 'paused', 'budget_exhausted', 'skipped'
        )
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

CREATE TABLE screening_result_fundamental_snapshots (
    screening_result_id uuid NOT NULL REFERENCES screening_results(id) ON DELETE CASCADE,
    snapshot_id uuid NOT NULL REFERENCES fundamental_snapshots(id),
    role text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (screening_result_id, snapshot_id, role),
    CONSTRAINT screening_result_fundamental_snapshots_role_check CHECK (
        role IN ('primary', 'promoter_pledge', 'leverage')
    )
);

CREATE INDEX screening_result_fundamental_snapshots_result_idx
ON screening_result_fundamental_snapshots (screening_result_id, role);

-- P7 durable, ordered processing state. See migration 006_p7_reliable_fundamentals.sql.
CREATE TABLE fundamental_analysis_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), scan_run_id uuid NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'queued', mode text NOT NULL DEFAULT 'retry_incomplete', queue_job_id text UNIQUE,
    config jsonb NOT NULL DEFAULT '{}'::jsonb, current_rank integer, current_symbol text, provider_requests integer NOT NULL DEFAULT 0,
    input_tokens integer NOT NULL DEFAULT 0, reasoning_tokens integer NOT NULL DEFAULT 0, output_tokens integer NOT NULL DEFAULT 0,
    cached_tokens integer NOT NULL DEFAULT 0, total_cost numeric(18, 8) NOT NULL DEFAULT 0, error_message text,
    started_at timestamptz, heartbeat_at timestamptz, completed_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE fundamental_analysis_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), analysis_run_id uuid NOT NULL REFERENCES fundamental_analysis_runs(id) ON DELETE CASCADE,
    screening_result_id uuid NOT NULL REFERENCES screening_results(id) ON DELETE CASCADE, rank integer NOT NULL,
    status text NOT NULL DEFAULT 'queued', snapshot_id uuid REFERENCES fundamental_snapshots(id), analysis_key text,
    provider_requests integer NOT NULL DEFAULT 0, input_tokens integer NOT NULL DEFAULT 0, reasoning_tokens integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0, cached_tokens integer NOT NULL DEFAULT 0, cost numeric(18, 8) NOT NULL DEFAULT 0,
    error_code text, error_message text, started_at timestamptz, completed_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (analysis_run_id, screening_result_id)
);

CREATE TABLE fundamental_ai_attempts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_item_id uuid NOT NULL REFERENCES fundamental_analysis_items(id) ON DELETE CASCADE,
    attempt_number integer NOT NULL,
    status text NOT NULL DEFAULT 'started',
    model text NOT NULL,
    reasoning_effort text NOT NULL CHECK (
        reasoning_effort IN ('low', 'medium', 'high', 'xhigh')
    ),
    prompt_version text NOT NULL,
    response_schema text NOT NULL,
    input_hash text NOT NULL,
    request_payload jsonb NOT NULL,
    response_payload jsonb,
    http_status integer,
    request_id text,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    cost numeric(18, 8) NOT NULL DEFAULT 0,
    error_code text,
    error_message text,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CONSTRAINT fundamental_ai_attempts_number_check CHECK (attempt_number > 0),
    CONSTRAINT fundamental_ai_attempts_status_check CHECK (
        status IN ('started', 'succeeded', 'invalid_response', 'provider_error', 'transport_unknown')
    ),
    CONSTRAINT fundamental_ai_attempts_http_status_check CHECK (
        http_status IS NULL OR (http_status >= 100 AND http_status <= 599)
    ),
    CONSTRAINT fundamental_ai_attempts_cost_check CHECK (cost >= 0),
    UNIQUE (analysis_item_id, attempt_number)
);

CREATE INDEX fundamental_ai_attempts_item_started_idx
ON fundamental_ai_attempts (analysis_item_id, started_at);

CREATE TABLE fundamental_annotations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), analysis_key text NOT NULL UNIQUE, status text NOT NULL DEFAULT 'succeeded',
    model text NOT NULL, reasoning_effort text NOT NULL, prompt_version text NOT NULL, input_hash text NOT NULL,
    payload jsonb NOT NULL, request_id text, usage jsonb NOT NULL DEFAULT '{}'::jsonb, cost numeric(18, 8) NOT NULL DEFAULT 0,
    source_attempt_id uuid REFERENCES fundamental_ai_attempts(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX fundamental_annotations_source_attempt_idx
ON fundamental_annotations (source_attempt_id)
WHERE source_attempt_id IS NOT NULL;

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
    execution_mode text NOT NULL DEFAULT 'paper',
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
    CONSTRAINT positions_execution_mode_check CHECK (
        execution_mode IN ('paper', 'live')
    ),
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
        intent_type IN (
            'entry', 'stop_loss_exit', 'target_exit', 'trailing_exit',
            'manual_exit', 'risk_reduction_exit', 'invalid_fill_exit'
        )
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


INSERT INTO system_controls (control_key, enabled, reason, changed_by)
VALUES
    ('fundamentals_processing_paused', false, 'Default: P7 fundamental processing enabled when configured.', 'schema'),
    ('fundamentals_ai_paused', false, 'Default: P7 AI annotations enabled when configured.', 'schema')
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

CREATE TABLE market_regime_snapshots (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reference_eod_date date NOT NULL,
    classifier_version text NOT NULL,
    regime text NOT NULL,
    benchmark_symbol text NOT NULL,
    benchmark_price numeric(18, 4) CHECK (benchmark_price IS NULL OR benchmark_price >= 0),
    benchmark_price_source text NOT NULL,
    benchmark_price_at timestamptz,
    sma_50 numeric(18, 4),
    sma_200 numeric(18, 4),
    sma_50_slope_20d numeric(18, 6),
    breadth_above_sma_50_pct numeric(8, 4),
    breadth_above_sma_200_pct numeric(8, 4),
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT market_regime_snapshots_regime_check CHECK (
        regime IN ('bullish', 'bearish', 'neutral', 'unavailable')
    )
);

CREATE INDEX market_regime_snapshots_date_idx
ON market_regime_snapshots (reference_eod_date DESC);

CREATE TABLE journal_entries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    position_id uuid NOT NULL UNIQUE REFERENCES positions(id),
    instrument_id uuid NOT NULL REFERENCES instruments(id),
    execution_mode text NOT NULL,
    status text NOT NULL DEFAULT 'open',
    symbol text NOT NULL,
    entry_frozen_at timestamptz,
    first_entry_fill_at timestamptz,
    first_entry_price numeric(18, 4),
    first_entry_quantity integer CHECK (
        first_entry_quantity IS NULL OR first_entry_quantity > 0
    ),
    final_entry_quantity integer CHECK (
        final_entry_quantity IS NULL OR final_entry_quantity > 0
    ),
    weighted_entry_price numeric(18, 4),
    entry_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    exit_fills jsonb NOT NULL DEFAULT '[]'::jsonb,
    weighted_exit_price numeric(18, 4),
    closed_at timestamptz,
    hold_duration_hours numeric(18, 4),
    exit_outcome text,
    exit_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    gross_pnl numeric(18, 4),
    estimated_charges jsonb NOT NULL DEFAULT '{}'::jsonb,
    actual_charges jsonb,
    charge_quality text NOT NULL DEFAULT 'estimated',
    net_pnl numeric(18, 4),
    gross_r_multiple numeric(18, 6),
    net_r_multiple numeric(18, 6),
    risk_amount numeric(18, 4),
    pnl_mismatch boolean NOT NULL DEFAULT false,
    pnl_mismatch_delta numeric(18, 4),
    market_regime_snapshot_id uuid REFERENCES market_regime_snapshots(id),
    notes text,
    execution_rating integer CHECK (
        execution_rating IS NULL
        OR (execution_rating >= 1 AND execution_rating <= 5)
    ),
    setup_tags jsonb NOT NULL DEFAULT '[]'::jsonb,
    mistake_tags jsonb NOT NULL DEFAULT '[]'::jsonb,
    emotion_tags jsonb NOT NULL DEFAULT '[]'::jsonb,
    lessons text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT journal_entries_execution_mode_check CHECK (
        execution_mode IN ('paper', 'live')
    ),
    CONSTRAINT journal_entries_status_check CHECK (
        status IN ('open', 'closed')
    ),
    CONSTRAINT journal_entries_charge_quality_check CHECK (
        charge_quality IN ('estimated', 'reconciled')
    ),
    CONSTRAINT journal_entries_exit_outcome_check CHECK (
        exit_outcome IS NULL
        OR exit_outcome IN (
            'stop_loss',
            'target',
            'trailing',
            'manual',
            'mixed'
        )
    )
);

CREATE TRIGGER journal_entries_set_updated_at
BEFORE UPDATE ON journal_entries
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE INDEX journal_entries_closed_at_idx
ON journal_entries (closed_at DESC NULLS LAST)
WHERE status = 'closed';

CREATE INDEX journal_entries_symbol_idx
ON journal_entries (symbol);

CREATE TABLE journal_chart_artifacts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    journal_entry_id uuid NOT NULL UNIQUE REFERENCES journal_entries(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'pending',
    claimed_by text,
    claimed_at timestamptz,
    lease_expires_at timestamptz,
    chart_source jsonb NOT NULL DEFAULT '{}'::jsonb,
    png_bytes bytea,
    content_hash text,
    capture_attempts integer NOT NULL DEFAULT 0 CHECK (capture_attempts >= 0),
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT journal_chart_artifacts_status_check CHECK (
        status IN ('pending', 'claimed', 'captured', 'failed')
    )
);

CREATE TRIGGER journal_chart_artifacts_set_updated_at
BEFORE UPDATE ON journal_chart_artifacts
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE INDEX journal_chart_artifacts_pending_idx
ON journal_chart_artifacts (status, created_at)
WHERE status IN ('pending', 'claimed');

CREATE TABLE journal_fill_outbox (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_fill_id uuid NOT NULL UNIQUE REFERENCES order_fills(id),
    position_id uuid NOT NULL REFERENCES positions(id),
    fill_side text NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    CONSTRAINT journal_fill_outbox_fill_side_check CHECK (
        fill_side IN ('entry', 'exit')
    ),
    CONSTRAINT journal_fill_outbox_status_check CHECK (
        status IN ('pending', 'processing', 'completed', 'failed')
    )
);

CREATE INDEX journal_fill_outbox_pending_idx
ON journal_fill_outbox (status, created_at)
WHERE status IN ('pending', 'processing');

-- On-demand VCP vision validator (advisory, personal app only).
-- See migrations 013_vcp_vision.sql and 014_vcp_vision_hardening.sql.
CREATE TABLE vcp_visual_analyses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    screening_result_id uuid NOT NULL REFERENCES screening_results(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'awaiting_capture' CHECK (
        status IN ('awaiting_capture', 'queued', 'running', 'succeeded', 'failed')
    ),
    chart_source jsonb NOT NULL DEFAULT '{}'::jsonb,
    frozen_ohlcv jsonb NOT NULL,
    context_image bytea,
    context_image_hash text,
    detail_image bytea,
    detail_image_hash text,
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
    result jsonb,
    ai_verdict text CHECK (
        ai_verdict IS NULL OR ai_verdict IN ('valid', 'invalid', 'uncertain')
    ),
    error_code text,
    error_message text,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    cost numeric(18, 8) NOT NULL DEFAULT 0 CHECK (cost >= 0),
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

CREATE TABLE journal_ai_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    status text NOT NULL DEFAULT 'queued',
    filters jsonb NOT NULL DEFAULT '{}'::jsonb,
    input_hash text NOT NULL,
    result jsonb,
    model text NOT NULL,
    request_id text,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CONSTRAINT journal_ai_runs_status_check CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed')
    )
);

CREATE INDEX journal_ai_runs_input_hash_idx
ON journal_ai_runs (input_hash, status, created_at DESC);

COMMENT ON TABLE journal_entries IS
'One automated journal record per app-managed position. Entry context frozen on first fill.';

COMMENT ON TABLE journal_fill_outbox IS
'Durable fill events for async journal processing. Future fills only — no backfill.';

-- P10: System-Generated, Human-Approved Trade Automation
CREATE TABLE automation_runs (
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

CREATE INDEX automation_runs_scan_run_idx ON automation_runs(scan_run_id);
CREATE INDEX automation_runs_status_created_idx ON automation_runs(status, created_at DESC);

CREATE TABLE trade_proposals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    automation_run_id uuid REFERENCES automation_runs(id) ON DELETE SET NULL,
    screening_result_id uuid NOT NULL REFERENCES screening_results(id) ON DELETE CASCADE,
    instrument_id uuid NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    symbol text NOT NULL,
    as_of_date date NOT NULL,
    status text NOT NULL DEFAULT 'pending_approval' CHECK (
        status IN ('pending_approval', 'approved', 'rejected', 'expired_unapproved')
    ),
    approval_deadline timestamptz NOT NULL,
    entry_session_date date NOT NULL,
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

CREATE INDEX trade_proposals_status_deadline_idx ON trade_proposals(status, approval_deadline);
CREATE INDEX trade_proposals_symbol_as_of_date_idx ON trade_proposals(symbol, as_of_date);
CREATE INDEX trade_proposals_screening_result_idx ON trade_proposals(screening_result_id);

CREATE TABLE proposal_decisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    proposal_id uuid NOT NULL REFERENCES trade_proposals(id) ON DELETE CASCADE,
    decision text NOT NULL CHECK (decision IN ('approved', 'rejected')),
    expected_proposal_hash text NOT NULL,
    notes text,
    decided_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX proposal_decisions_proposal_idx ON proposal_decisions(proposal_id);

CREATE TABLE entry_legs (
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

CREATE INDEX entry_legs_status_idx ON entry_legs(status);
CREATE INDEX entry_legs_position_idx ON entry_legs(position_id);

CREATE TABLE trigger_events (
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

CREATE INDEX trigger_events_leg_idx ON trigger_events(leg_id, bar_timestamp);

CREATE TABLE capacity_conflicts (
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
    executed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT capacity_conflicts_competing_legs_check CHECK (jsonb_typeof(competing_leg_ids) = 'array')
);

CREATE TRIGGER capacity_conflicts_set_updated_at
BEFORE UPDATE ON capacity_conflicts
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE INDEX capacity_conflicts_status_idx ON capacity_conflicts(status);

CREATE TABLE risk_policies (
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

CREATE UNIQUE INDEX risk_policies_single_active_uidx
ON risk_policies ((true)) WHERE is_active = true;

INSERT INTO risk_policies (
    version, name, is_active, risk_per_trade_pct, max_total_open_risk_pct,
    max_single_name_notional_pct, max_sector_notional_pct, max_cluster_notional_pct,
    correlation_cluster_threshold, correlation_lookback_sessions,
    daily_loss_limit_pct, max_open_positions, deployable_capital_override
)
VALUES (
    1, 'Balanced', true, 0.0100, 0.0400, 0.1500, 0.3000, 0.3000,
    0.80, 60, 0.0200, 8, 100000.0000
)
ON CONFLICT (version) DO NOTHING;

CREATE TABLE allocation_ledger (
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

CREATE INDEX allocation_ledger_leg_idx ON allocation_ledger(leg_id);
CREATE INDEX allocation_ledger_created_idx ON allocation_ledger(created_at DESC);

CREATE TABLE five_minute_bars (
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

CREATE INDEX five_minute_bars_symbol_time_idx ON five_minute_bars(symbol, bar_time DESC);

CREATE TABLE volume_profiles (
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

CREATE INDEX volume_profiles_symbol_date_idx ON volume_profiles(symbol, as_of_date DESC);

-- P10 relationships are added after all referenced tables exist.
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
    ADD COLUMN IF NOT EXISTS runner_filled_shares integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS execution_mode text NOT NULL DEFAULT 'paper';

ALTER TABLE order_intents
    ADD COLUMN IF NOT EXISTS proposal_id uuid REFERENCES trade_proposals(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS entry_leg_id uuid REFERENCES entry_legs(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS exit_purpose text CHECK (
        exit_purpose IS NULL OR exit_purpose IN (
            'stop_loss', 'target_1', 'target_2', 'target_3', 'runner_trail',
            'risk_reduction', 'invalid_fill', 'manual'
        )
    );

ALTER TABLE order_fills
    ADD COLUMN IF NOT EXISTS proposal_id uuid REFERENCES trade_proposals(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS entry_leg_id uuid REFERENCES entry_legs(id) ON DELETE SET NULL;

ALTER TABLE trade_proposals
    ADD COLUMN IF NOT EXISTS risk_policy_id uuid REFERENCES risk_policies(id),
    ADD COLUMN IF NOT EXISTS risk_policy_version integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS approved_risk_budget_amount numeric(18, 4),
    ADD COLUMN IF NOT EXISTS prompt_version text NOT NULL DEFAULT 'p10_vcp_proposal_v4',
    ADD COLUMN IF NOT EXISTS schema_version text NOT NULL DEFAULT 'gemini_vcp_proposal_output_v3',
    ADD COLUMN IF NOT EXISTS geometry_version text NOT NULL DEFAULT 'p10_geometry_three_windows_v2',
    ADD COLUMN IF NOT EXISTS live_eligible boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS generated_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS context_image bytea,
    ADD COLUMN IF NOT EXISTS detail_image bytea,
    ADD COLUMN IF NOT EXISTS provider_request_id text,
    ADD COLUMN IF NOT EXISTS provider_usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS provider_cost numeric(18, 8) NOT NULL DEFAULT 0;

ALTER TABLE entry_legs
    ALTER COLUMN trigger_price DROP NOT NULL,
    ALTER COLUMN chase_ceiling DROP NOT NULL,
    ALTER COLUMN eligible_session_start DROP NOT NULL,
    ALTER COLUMN eligible_session_end DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS first_filled_at timestamptz,
    ADD COLUMN IF NOT EXISTS signal_bar_timestamp timestamptz;

ALTER TABLE five_minute_bars
    ADD COLUMN IF NOT EXISTS cumulative_volume bigint NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS reconciliation_status text NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS reconciled_at timestamptz,
    ADD COLUMN IF NOT EXISTS reconciliation_details jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE proposal_attempts (
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

CREATE UNIQUE INDEX trade_proposals_input_version_uidx
ON trade_proposals (
    screening_result_id, source_hash, model, prompt_version,
    schema_version, renderer_version, geometry_version, risk_policy_version
);
CREATE UNIQUE INDEX proposal_decisions_one_per_proposal_uidx
ON proposal_decisions (proposal_id);
CREATE UNIQUE INDEX trigger_events_leg_bar_type_uidx
ON trigger_events (leg_id, bar_timestamp, bar_type);
CREATE INDEX proposal_attempts_run_status_idx
ON proposal_attempts (automation_run_id, status, started_at);
CREATE INDEX positions_proposal_idx ON positions(proposal_id);
CREATE INDEX order_intents_proposal_idx ON order_intents(proposal_id);
CREATE INDEX order_intents_leg_idx ON order_intents(entry_leg_id);

ALTER TABLE five_minute_bars
    ADD CONSTRAINT five_minute_bars_reconciliation_status_check CHECK (
        reconciliation_status IN ('pending', 'verified', 'drifted', 'failed')
    );

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

CREATE TRIGGER trade_proposals_immutable_payload
BEFORE UPDATE ON trade_proposals
FOR EACH ROW
EXECUTE FUNCTION enforce_trade_proposal_immutability();

INSERT INTO system_controls (control_key, enabled, reason, changed_by)
VALUES
    ('proposal_processing_paused', false, 'Default: proposal processing enabled when configured.', 'schema'),
    ('new_entries_paused', false, 'Default: approved initial and add legs may execute.', 'schema')
ON CONFLICT (control_key) DO NOTHING;

-- P9: deterministic EOD market context, sector strength, and stop-streak guard.
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

ALTER TABLE positions
    DROP CONSTRAINT IF EXISTS positions_execution_mode_check;
ALTER TABLE positions
    ADD CONSTRAINT positions_execution_mode_check CHECK (
        execution_mode IN ('paper', 'live')
    );
CREATE INDEX IF NOT EXISTS positions_execution_mode_state_idx
ON positions (execution_mode, state);

CREATE TABLE IF NOT EXISTS paper_broker_account (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    starting_cash numeric(18, 4) NOT NULL CHECK (starting_cash > 0),
    cash_available numeric(18, 4) NOT NULL CHECK (cash_available >= 0),
    seeded_from_policy_version integer REFERENCES risk_policies(version),
    seeded_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_broker_orders (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_intent_id uuid NOT NULL REFERENCES order_intents(id),
    fyers_async_id text NOT NULL UNIQUE,
    fyers_order_id text NOT NULL UNIQUE,
    symbol text NOT NULL,
    side text NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity integer NOT NULL CHECK (quantity > 0),
    filled_quantity integer NOT NULL DEFAULT 0 CHECK (filled_quantity >= 0),
    product_type text NOT NULL DEFAULT 'CNC' CHECK (product_type IN ('CNC')),
    status text NOT NULL,
    traded_price numeric(18, 4),
    order_tag text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS paper_broker_orders_intent_idx
ON paper_broker_orders (order_intent_id);

CREATE TABLE IF NOT EXISTS paper_broker_trades (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    paper_order_id uuid NOT NULL REFERENCES paper_broker_orders(id),
    order_intent_id uuid NOT NULL REFERENCES order_intents(id),
    trade_number text NOT NULL UNIQUE,
    symbol text NOT NULL,
    side text NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity integer NOT NULL CHECK (quantity > 0),
    price numeric(18, 4) NOT NULL CHECK (price >= 0),
    product_type text NOT NULL DEFAULT 'CNC' CHECK (product_type IN ('CNC')),
    filled_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_broker_positions (
    symbol text PRIMARY KEY,
    net_qty integer NOT NULL,
    avg_price numeric(18, 4) NOT NULL CHECK (avg_price >= 0),
    product_type text NOT NULL DEFAULT 'CNC' CHECK (product_type IN ('CNC')),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS p10_rollout_state (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    stage text NOT NULL CHECK (stage IN ('shadow', 'paper', 'reduced_live', 'full_live')),
    changed_by text NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT now(),
    reason text
);

INSERT INTO p10_rollout_state (id, stage, changed_by, reason)
VALUES (
    true,
    'shadow',
    'schema',
    'P10 starts at Shadow; approve cannot arm entries.'
)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS p10_rollout_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    from_stage text,
    to_stage text NOT NULL,
    changed_by text NOT NULL,
    reason text,
    confirmation text,
    created_at timestamptz NOT NULL DEFAULT now()
);

UPDATE risk_policies
SET deployable_capital_override = 100000.0000,
    updated_at = now()
WHERE is_active = true
  AND deployable_capital_override IS NULL;

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
