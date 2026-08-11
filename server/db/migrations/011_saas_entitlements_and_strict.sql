-- Swyingify S3 foundation: production entitlements, admin role, and Strict.
-- Checkout/webhook ownership stays in the Next.js app; the Python worker only
-- reads scan templates and executes versioned screening configs.
BEGIN;

ALTER TABLE "user"
    ADD COLUMN IF NOT EXISTS role text NOT NULL DEFAULT 'user';

ALTER TABLE "user"
    DROP CONSTRAINT IF EXISTS user_role_check;

ALTER TABLE "user"
    ADD CONSTRAINT user_role_check CHECK (role IN ('user', 'admin'));

CREATE TABLE IF NOT EXISTS saas_subscriptions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id text NOT NULL REFERENCES "user" (id) ON DELETE CASCADE,
    provider text NOT NULL,
    plan_code text NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    provider_customer_id text,
    provider_subscription_id text,
    current_period_start timestamptz,
    current_period_end timestamptz,
    cancel_at_period_end boolean NOT NULL DEFAULT false,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT saas_subscriptions_provider_check CHECK (
        provider IN ('manual', 'razorpay')
    ),
    CONSTRAINT saas_subscriptions_plan_check CHECK (
        plan_code IN ('pro')
    ),
    CONSTRAINT saas_subscriptions_status_check CHECK (
        status IN (
            'pending', 'trialing', 'active', 'past_due', 'paused',
            'cancelled', 'expired'
        )
    )
);

CREATE INDEX IF NOT EXISTS saas_subscriptions_user_status_idx
ON saas_subscriptions (user_id, status, current_period_end DESC);

CREATE UNIQUE INDEX IF NOT EXISTS saas_subscriptions_provider_reference_uidx
ON saas_subscriptions (provider, provider_subscription_id)
WHERE provider_subscription_id IS NOT NULL;

DROP TRIGGER IF EXISTS saas_subscriptions_set_updated_at ON saas_subscriptions;
CREATE TRIGGER saas_subscriptions_set_updated_at
BEFORE UPDATE ON saas_subscriptions
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- Paid Minervini Strict: same deterministic engine, with explicit hard gates
-- for trend, RS, proximity, contraction, and volume dry-up.
INSERT INTO scan_templates (
    family,
    code,
    display_name,
    access_tier,
    version,
    config,
    is_active
)
VALUES (
    'minervini',
    'strict',
    'Minervini Strict',
    'paid',
    1,
    '{
      "pipeline_version": "vcp_score_v2",
      "shortlist_limit": 25,
      "fundamental_limit": 0,
      "minimum_history_days": 252,
      "liquidity_lookback_days": 20,
      "min_adtv_crore": 25.0,
      "stage2_core_checks_required": 5,
      "max_distance_52w_high_pct": 15.0,
      "high_proximity_zero_pct": 15.0,
      "high_proximity_full_pct": 8.0,
      "min_rs_rating": 80,
      "max_atr_proximity_factor": 1.20,
      "max_bb_width_percentile": 0.40,
      "max_volume_dry_up_ratio": 0.80,
      "minimum_technical_score": 80.0
    }'::jsonb,
    true
)
ON CONFLICT (family, code, version) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    access_tier = EXCLUDED.access_tier,
    config = EXCLUDED.config,
    is_active = EXCLUDED.is_active,
    updated_at = now();

COMMIT;
