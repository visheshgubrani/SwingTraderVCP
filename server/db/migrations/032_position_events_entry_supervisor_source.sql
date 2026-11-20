-- The entry supervisor writes position_events (add_leg_armed, end-of-session
-- entry retirement) as trigger_source 'entry_supervisor', which the original
-- closed list rejected, failing the whole maintenance transaction.

BEGIN;

ALTER TABLE position_events
    DROP CONSTRAINT IF EXISTS position_events_trigger_source_check;
ALTER TABLE position_events
    ADD CONSTRAINT position_events_trigger_source_check CHECK (
        trigger_source IN (
            'api',
            'execution_engine',
            'entry_supervisor',
            'order_gateway',
            'position_monitor',
            'reconciliation',
            'manual_import'
        )
    );

COMMIT;
