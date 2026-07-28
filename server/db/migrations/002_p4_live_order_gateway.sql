-- P4: async live CNC entry correlation and idempotent order-gateway events.
-- Apply after 001_p3_paper_trading.sql.

BEGIN;

ALTER TABLE order_intents
    ADD COLUMN fyers_async_id text,
    ADD COLUMN exchange_order_id text,
    ADD COLUMN broker_requested_at timestamptz,
    ADD COLUMN broker_responded_at timestamptz;

ALTER TABLE order_intents
    DROP CONSTRAINT order_intents_status_check,
    ADD CONSTRAINT order_intents_status_check CHECK (
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
    );

CREATE UNIQUE INDEX order_intents_fyers_async_unique_idx
ON order_intents (fyers_async_id)
WHERE fyers_async_id IS NOT NULL;

CREATE UNIQUE INDEX order_intents_exchange_order_unique_idx
ON order_intents (exchange_order_id)
WHERE exchange_order_id IS NOT NULL;

ALTER TABLE position_events
    DROP CONSTRAINT position_events_trigger_source_check,
    ADD CONSTRAINT position_events_trigger_source_check CHECK (
        trigger_source IN (
            'api',
            'execution_engine',
            'order_gateway',
            'position_monitor',
            'reconciliation',
            'manual_import'
        )
    );

ALTER TABLE order_events
    ADD COLUMN broker_event_key text,
    ADD COLUMN fyers_async_id text,
    ADD COLUMN exchange_order_id text;

-- Existing P3 installations have no live gateway events. This still keeps
-- the migration safe if local paper/debug rows were inserted manually.
UPDATE order_events
SET broker_event_key = encode(
    digest(
        order_intent_id::text || ':' || id::text || ':' || event_type,
        'sha256'
    ),
    'hex'
)
WHERE broker_event_key IS NULL;

ALTER TABLE order_events
    ALTER COLUMN broker_event_key SET NOT NULL;

CREATE UNIQUE INDEX order_events_broker_event_unique_idx
ON order_events (broker_event_key);

COMMIT;
