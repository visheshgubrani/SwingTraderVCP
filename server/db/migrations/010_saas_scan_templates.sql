-- Swyingify S2: global scan templates + scan_runs visibility / as_of_date.
-- Personal manual scans keep visibility='personal' and null template_id.
BEGIN;

CREATE TABLE IF NOT EXISTS scan_templates (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    family text NOT NULL,
    code text NOT NULL,
    display_name text NOT NULL,
    access_tier text NOT NULL DEFAULT 'public',
    version integer NOT NULL DEFAULT 1,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT scan_templates_access_tier_check CHECK (
        access_tier IN ('public', 'auth', 'paid')
    ),
    CONSTRAINT scan_templates_family_code_version_unique UNIQUE (family, code, version)
);

CREATE INDEX IF NOT EXISTS scan_templates_active_idx
ON scan_templates (family, code)
WHERE is_active = true;

ALTER TABLE scan_runs
    ADD COLUMN IF NOT EXISTS template_id uuid REFERENCES scan_templates(id),
    ADD COLUMN IF NOT EXISTS visibility text NOT NULL DEFAULT 'personal',
    ADD COLUMN IF NOT EXISTS owner_user_id text,
    ADD COLUMN IF NOT EXISTS as_of_date date;

ALTER TABLE scan_runs
    DROP CONSTRAINT IF EXISTS scan_runs_visibility_check;

ALTER TABLE scan_runs
    ADD CONSTRAINT scan_runs_visibility_check CHECK (
        visibility IN ('global', 'user', 'personal')
    );

CREATE INDEX IF NOT EXISTS scan_runs_global_template_as_of_idx
ON scan_runs (template_id, as_of_date DESC, created_at DESC)
WHERE visibility = 'global';

CREATE UNIQUE INDEX IF NOT EXISTS scan_runs_global_template_as_of_succeeded_uidx
ON scan_runs (template_id, as_of_date)
WHERE visibility = 'global' AND status = 'succeeded' AND template_id IS NOT NULL;

-- Seed Minervini Standard (top 25, no P7). Config matches TechnicalScreeningConfig.
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
    'standard',
    'Minervini Standard',
    'public',
    1,
    '{
      "pipeline_version": "vcp_score_v2",
      "shortlist_limit": 25,
      "fundamental_limit": 0,
      "minimum_history_days": 252,
      "liquidity_lookback_days": 20,
      "min_adtv_crore": 10.0,
      "stage2_core_checks_required": 4,
      "max_distance_52w_high_pct": 25.0
    }'::jsonb,
    true
)
ON CONFLICT (family, code, version) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    access_tier = EXCLUDED.access_tier,
    config = EXCLUDED.config,
    is_active = EXCLUDED.is_active,
    updated_at = now();

COMMENT ON TABLE scan_templates IS
  'Versioned SaaS scanner templates (legend family + aggression). Snapshotted into scan_runs.technical_config at run time.';

COMMENT ON COLUMN scan_runs.visibility IS
  'global = shared SaaS daily run; user = paid variant owned by owner_user_id; personal = owner workstation scans.';

COMMENT ON COLUMN scan_runs.as_of_date IS
  'Trading date the shortlist is computed against (EOD candle date).';

COMMIT;
