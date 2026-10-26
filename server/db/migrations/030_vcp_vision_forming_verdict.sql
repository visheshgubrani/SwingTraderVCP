-- VCP vision validator v2: shared VCP rubric with the P10 proposal reader and
-- a 'forming' verdict (structure intact but incomplete), so the advisory
-- validator and the proposal path classify on one definition.

BEGIN;

ALTER TABLE vcp_visual_analyses
    DROP CONSTRAINT IF EXISTS vcp_visual_analyses_ai_verdict_check;
ALTER TABLE vcp_visual_analyses
    ADD CONSTRAINT vcp_visual_analyses_ai_verdict_check CHECK (
        ai_verdict IS NULL OR ai_verdict IN ('valid', 'forming', 'invalid', 'uncertain')
    );

ALTER TABLE vcp_visual_analyses
    DROP CONSTRAINT IF EXISTS vcp_visual_analyses_human_verdict_check;
ALTER TABLE vcp_visual_analyses
    ADD CONSTRAINT vcp_visual_analyses_human_verdict_check CHECK (
        human_verdict IS NULL OR human_verdict IN ('valid', 'forming', 'invalid', 'uncertain')
    );

COMMIT;
