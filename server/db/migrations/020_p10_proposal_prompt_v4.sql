-- P10 proposal prompt v4: invalidate reuse of nearby-resistance target guidance.

BEGIN;

ALTER TABLE trade_proposals
    ALTER COLUMN prompt_version SET DEFAULT 'p10_vcp_proposal_v4';

ALTER TABLE proposal_attempts
    ALTER COLUMN prompt_version SET DEFAULT 'p10_vcp_proposal_v4';

COMMIT;
