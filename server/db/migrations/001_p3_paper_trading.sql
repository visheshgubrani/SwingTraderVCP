-- P3: manual trade checkpoint + paper/log execution.
-- Apply once to databases created from the pre-P3 schema:
--   docker exec -i algo-trading-postgres psql -v ON_ERROR_STOP=1 \
--     -U algo -d algo_trading < server/db/migrations/001_p3_paper_trading.sql

BEGIN;

ALTER TABLE trade_instructions
    ADD COLUMN product_type text NOT NULL DEFAULT 'CNC',
    ADD COLUMN planned_entry_price numeric(18, 4);

UPDATE trade_instructions
SET planned_entry_price = entry_limit_price
WHERE planned_entry_price IS NULL;

-- P3 had no earlier persisted instructions. Fail loudly instead of inventing
-- a reference entry if a locally-created market draft exists.
ALTER TABLE trade_instructions
    ALTER COLUMN planned_entry_price SET NOT NULL,
    ADD CONSTRAINT trade_instructions_planned_entry_price_check
        CHECK (planned_entry_price > 0),
    ADD CONSTRAINT trade_instructions_product_type_check
        CHECK (product_type IN ('CNC'));

ALTER TABLE positions
    ADD COLUMN product_type text NOT NULL DEFAULT 'CNC',
    ADD CONSTRAINT positions_product_type_check
        CHECK (product_type IN ('CNC'));

ALTER TABLE order_intents
    ADD COLUMN product_type text NOT NULL DEFAULT 'CNC',
    ADD COLUMN execution_mode text NOT NULL DEFAULT 'paper',
    ADD CONSTRAINT order_intents_product_type_check
        CHECK (product_type IN ('CNC')),
    ADD CONSTRAINT order_intents_execution_mode_check
        CHECK (execution_mode IN ('paper', 'live'));

CREATE UNIQUE INDEX positions_trade_instruction_unique_idx
ON positions (trade_instruction_id)
WHERE trade_instruction_id IS NOT NULL;

CREATE UNIQUE INDEX order_intents_entry_instruction_unique_idx
ON order_intents (trade_instruction_id)
WHERE trade_instruction_id IS NOT NULL AND intent_type = 'entry';

COMMIT;
