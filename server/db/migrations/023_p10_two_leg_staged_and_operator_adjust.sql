-- Migration 023: Allow two_leg_staged entry template and operator price adjustments before approval

BEGIN;

-- 1. Update entry_template check constraint to include 'two_leg_staged'
ALTER TABLE trade_proposals DROP CONSTRAINT IF EXISTS trade_proposals_entry_template_check;
ALTER TABLE trade_proposals ADD CONSTRAINT trade_proposals_entry_template_check 
    CHECK (entry_template IN ('single', 'two_leg', 'two_leg_staged', 'three_leg_front', 'three_leg_balanced'));

-- 2. Update immutability trigger function so pending_approval proposals can be finalized with operator adjustments upon approval, but once approved/terminal become strictly immutable.
CREATE OR REPLACE FUNCTION enforce_trade_proposal_immutability()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status IN ('approved', 'rejected', 'expired_unapproved') THEN
        IF (to_jsonb(NEW) - ARRAY['status', 'updated_at'])
           IS DISTINCT FROM
           (to_jsonb(OLD) - ARRAY['status', 'updated_at']) THEN
            RAISE EXCEPTION 'approved or completed trade proposal payload is immutable';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

COMMIT;
