-- P10 paper broker ledger, rollout stage lock, and position execution_mode.

ALTER TABLE positions
    ADD COLUMN IF NOT EXISTS execution_mode text;

UPDATE positions p
SET execution_mode = COALESCE(
    (
        SELECT oi.execution_mode
        FROM order_intents oi
        WHERE oi.position_id = p.id
        ORDER BY oi.created_at DESC
        LIMIT 1
    ),
    'paper'
)
WHERE p.execution_mode IS NULL;

ALTER TABLE positions
    ALTER COLUMN execution_mode SET DEFAULT 'paper';

ALTER TABLE positions
    ALTER COLUMN execution_mode SET NOT NULL;

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
    'migration',
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
