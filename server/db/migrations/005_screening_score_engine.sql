-- VCP technical score engine v2: durable score for ranked manual-review setups.
-- Apply after 004_p8_journal_ai.sql.

BEGIN;

ALTER TABLE screening_results
    ADD COLUMN technical_score numeric(5, 2) CHECK (
        technical_score IS NULL
        OR (technical_score >= 0 AND technical_score <= 100)
    );

COMMENT ON COLUMN screening_results.technical_score IS
'Deterministic 0-100 VCP technical score. NULL identifies legacy pre-v2 results.';

COMMIT;
