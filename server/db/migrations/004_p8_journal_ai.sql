-- P8: automated trade journal, chart artifacts, fill outbox, market regime, AI coach.
-- Apply after 003_p7_fundamental_pass.sql.

BEGIN;

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

INSERT INTO instruments (
    exchange,
    segment,
    symbol,
    trading_symbol,
    fyers_symbol,
    name,
    lot_size,
    tick_size,
    active,
    metadata
)
SELECT
    'NSE',
    'INDEX',
    'NIFTY50',
    'NIFTY50-INDEX',
    'NSE:NIFTY50-INDEX',
    'Nifty 50 Index',
    1,
    0.05,
    true,
    '{"role": "benchmark", "p8_regime": true}'::jsonb
WHERE NOT EXISTS (
    SELECT 1 FROM instruments WHERE fyers_symbol = 'NSE:NIFTY50-INDEX'
);

COMMIT;
