-- P10 geometry v4: R:R-adjusted chase ceiling; T2/T3 recorded as R multiples.

BEGIN;

ALTER TABLE trade_proposals
    ALTER COLUMN geometry_version
        SET DEFAULT 'p10_geometry_rr_adjusted_chase_v4';

ALTER TABLE proposal_attempts
    ALTER COLUMN geometry_version
        SET DEFAULT 'p10_geometry_rr_adjusted_chase_v4';

COMMIT;
