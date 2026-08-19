-- P10 geometry v3: resistance-zone pivot grounding and structured diagnostics.

BEGIN;

ALTER TABLE trade_proposals
    ALTER COLUMN geometry_version
        SET DEFAULT 'p10_geometry_resistance_zones_v3';

ALTER TABLE proposal_attempts
    ALTER COLUMN geometry_version
        SET DEFAULT 'p10_geometry_resistance_zones_v3',
    ADD COLUMN IF NOT EXISTS error_details jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMIT;
