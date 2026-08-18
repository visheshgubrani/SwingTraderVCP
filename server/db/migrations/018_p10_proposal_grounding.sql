-- P10 proposal grounding: invalidate reuse of the pixel-only request versions.

BEGIN;

ALTER TABLE trade_proposals
    ALTER COLUMN prompt_version SET DEFAULT 'p10_vcp_proposal_v3',
    ALTER COLUMN schema_version SET DEFAULT 'gemini_vcp_proposal_output_v3';

ALTER TABLE proposal_attempts
    ALTER COLUMN prompt_version SET DEFAULT 'p10_vcp_proposal_v3',
    ALTER COLUMN schema_version SET DEFAULT 'gemini_vcp_proposal_output_v3';

COMMIT;
