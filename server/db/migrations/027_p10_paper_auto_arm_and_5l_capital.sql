-- Migration 027: Update paper initial capital and deployable capital override to 5L (500,000 INR).

UPDATE risk_policies
SET deployable_capital_override = 500000.0000,
    updated_at = now()
WHERE is_active = true
  AND (deployable_capital_override IS NULL OR deployable_capital_override = 100000.0000);

UPDATE paper_broker_account
SET starting_cash = 500000.0000,
    cash_available = 500000.0000,
    updated_at = now()
WHERE id = true
  AND starting_cash = 100000.0000
  AND cash_available = 100000.0000;

-- Advance rollout stage from shadow to paper if still in shadow
UPDATE p10_rollout_state
SET stage = 'paper',
    changed_by = 'migration_027',
    reason = 'Auto-promote to paper rollout stage for automated paper trade testing'
WHERE id = true
  AND stage = 'shadow';
