-- P10 vision rework: forming-pattern watch + confidence 0-100.

CREATE TABLE IF NOT EXISTS p10_forming_patterns (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id uuid NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    screening_result_id uuid REFERENCES screening_results(id) ON DELETE SET NULL,
    symbol text NOT NULL,
    first_seen_as_of date NOT NULL,
    last_as_of date NOT NULL,
    forming_state text NOT NULL CHECK (forming_state IN ('developing', 'breaking_down')),
    status text NOT NULL DEFAULT 'watching' CHECK (
        status IN ('watching', 'promoted', 'broken_down', 'expired')
    ),
    next_check_date date NOT NULL,
    llm_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    python_candidates jsonb NOT NULL DEFAULT '[]'::jsonb,
    last_attempt_id uuid REFERENCES proposal_attempts(id) ON DELETE SET NULL,
    promoted_proposal_id uuid REFERENCES trade_proposals(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER p10_forming_patterns_set_updated_at
BEFORE UPDATE ON p10_forming_patterns
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE UNIQUE INDEX p10_forming_patterns_watching_instrument_idx
ON p10_forming_patterns (instrument_id)
WHERE status = 'watching';

CREATE INDEX p10_forming_patterns_status_next_check_idx
ON p10_forming_patterns (status, next_check_date);

ALTER TABLE trade_proposals
    DROP CONSTRAINT IF EXISTS trade_proposals_confidence_check;

ALTER TABLE trade_proposals
    ALTER COLUMN confidence TYPE numeric(5, 2);

ALTER TABLE trade_proposals
    ADD CONSTRAINT trade_proposals_confidence_range_check
    CHECK (confidence >= 0 AND confidence <= 100);
