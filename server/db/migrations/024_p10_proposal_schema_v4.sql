-- P10 provider schema v4: Gemini-compatible json_schema (inlined $ref, no Decimal anyOf).

BEGIN;

ALTER TABLE trade_proposals
    ALTER COLUMN schema_version SET DEFAULT 'gemini_vcp_proposal_output_v4';

ALTER TABLE proposal_attempts
    ALTER COLUMN schema_version SET DEFAULT 'gemini_vcp_proposal_output_v4';

COMMIT;
