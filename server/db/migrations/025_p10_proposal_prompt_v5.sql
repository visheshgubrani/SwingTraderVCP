-- P10 proposal prompt/schema v5: chart-only Gemini VCP read.
-- Drop dated OHLCV anchors, confidence, and contradicts_scanner from the
-- live contract. Persist 'partial' as an attempt status.

BEGIN;

ALTER TABLE trade_proposals
    ALTER COLUMN prompt_version SET DEFAULT 'p10_vcp_proposal_v5';

ALTER TABLE proposal_attempts
    ALTER COLUMN prompt_version SET DEFAULT 'p10_vcp_proposal_v5';

ALTER TABLE trade_proposals
    ALTER COLUMN schema_version SET DEFAULT 'gemini_vcp_proposal_output_v5';

ALTER TABLE proposal_attempts
    ALTER COLUMN schema_version SET DEFAULT 'gemini_vcp_proposal_output_v5';

ALTER TABLE proposal_attempts
    DROP CONSTRAINT IF EXISTS proposal_attempts_status_check;

ALTER TABLE proposal_attempts
    ADD CONSTRAINT proposal_attempts_status_check
    CHECK (status IN ('running', 'valid', 'invalid', 'uncertain', 'partial', 'failed', 'timed_out'));

COMMIT;
