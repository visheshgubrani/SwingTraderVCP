import "server-only"

import { auth } from "@/lib/auth"
import {
  FEATURES,
  type AccessContext,
  type AccessTier,
  type Feature,
} from "@/lib/access-types"
import { db } from "@/lib/db"

export type { AccessContext, AccessTier, Feature } from "@/lib/access-types"

const ANONYMOUS_FEATURES = new Set<Feature>([
  "scanner.standard",
  "scanner.strict.preview",
])

const FREE_FEATURES = new Set<Feature>([
  ...ANONYMOUS_FEATURES,
  "scanner.history.recent",
  "watchlists.basic",
])

function featureMap(tier: AccessTier): Record<Feature, boolean> {
  const all = tier === "pro" || tier === "admin" || tier === "developer"
  const enabled = tier === "free" ? FREE_FEATURES : ANONYMOUS_FEATURES
  return Object.fromEntries(
    FEATURES.map((feature) => [feature, all || enabled.has(feature)]),
  ) as Record<Feature, boolean>
}

function parseAdminEmails(): Set<string> {
  return new Set(
    (process.env.SWYINGIFY_ADMIN_EMAILS ?? "")
      .split(",")
      .map((email) => email.trim().toLowerCase())
      .filter(Boolean),
  )
}

function fullAccess(
  tier: "developer" | "admin",
  user: { id: string; email: string } | null,
): AccessContext {
  return {
    tier,
    isAuthenticated: Boolean(user),
    isBypassed: true,
    bypassReason: tier === "developer" ? "development" : "admin",
    userId: user?.id ?? null,
    email: user?.email ?? null,
    features: featureMap(tier),
    limits: {
      historySessions: null,
      variantRunsPerDay: 5,
      watchlists: 10,
      watchlistSymbols: 250,
    },
  }
}

export async function resolveAccess(requestHeaders: Headers): Promise<AccessContext> {
  if (process.env.NODE_ENV !== "production") {
    return fullAccess("developer", null)
  }

  const session = await auth.api.getSession({ headers: requestHeaders })
  const user = session?.user ?? null
  const role = user && "role" in user ? String(user.role) : "user"
  const adminEmails = parseAdminEmails()
  const isAdmin = Boolean(
    user && (role === "admin" || adminEmails.has(user.email.toLowerCase())),
  )
  if (isAdmin && user) {
    return fullAccess("admin", { id: user.id, email: user.email })
  }

  let isPro = false
  if (user) {
    const result = await db.query<{ has_access: boolean }>(
      `
        SELECT EXISTS (
          SELECT 1
          FROM saas_subscriptions
          WHERE user_id = $1
            AND status IN ('trialing', 'active')
            AND (current_period_end IS NULL OR current_period_end > now())
        ) AS has_access
      `,
      [user.id],
    )
    isPro = Boolean(result.rows[0]?.has_access)
  }

  const tier: AccessTier = isPro ? "pro" : user ? "free" : "anonymous"
  return {
    tier,
    isAuthenticated: Boolean(user),
    isBypassed: false,
    bypassReason: null,
    userId: user?.id ?? null,
    email: user?.email ?? null,
    features: featureMap(tier),
    limits: isPro
      ? {
          historySessions: null,
          variantRunsPerDay: 5,
          watchlists: 10,
          watchlistSymbols: 250,
        }
      : {
          historySessions: 20,
          variantRunsPerDay: 0,
          watchlists: user ? 1 : 0,
          watchlistSymbols: user ? 25 : 0,
        },
  }
}

export function hasFeature(access: AccessContext, feature: Feature): boolean {
  return access.features[feature]
}

export async function isRecentHistoryDate(
  asOfDate: string,
  sessionLimit: number,
): Promise<boolean> {
  const result = await db.query<{ as_of_date: string }>(
    `
      SELECT DISTINCT r.as_of_date::text AS as_of_date
      FROM scan_runs r
      JOIN scan_templates t ON t.id = r.template_id
      WHERE r.visibility = 'global'
        AND r.status = 'succeeded'
        AND t.family = 'minervini'
        AND t.code = 'standard'
        AND r.as_of_date IS NOT NULL
      ORDER BY r.as_of_date DESC
      LIMIT $1
    `,
    [sessionLimit],
  )
  return result.rows.some((row) => row.as_of_date === asOfDate)
}

export async function isLatestStandardDate(asOfDate: string): Promise<boolean> {
  const result = await db.query<{ as_of_date: string | null }>(
    `
      SELECT r.as_of_date::text AS as_of_date
      FROM scan_runs r
      JOIN scan_templates t ON t.id = r.template_id
      WHERE r.visibility = 'global'
        AND r.status = 'succeeded'
        AND t.family = 'minervini'
        AND t.code = 'standard'
        AND r.as_of_date IS NOT NULL
      ORDER BY r.as_of_date DESC, r.created_at DESC
      LIMIT 1
    `,
  )
  return result.rows[0]?.as_of_date === asOfDate
}

export async function listStandardHistory(
  sessionLimit: number | null,
): Promise<Array<{ asOfDate: string; resultCount: number; completedAt: string | null }>> {
  const result = await db.query<{
    as_of_date: string
    result_count: number
    completed_at: Date | null
  }>(
    `
      SELECT
        r.as_of_date::text AS as_of_date,
        COUNT(s.id)::int AS result_count,
        MAX(r.completed_at) AS completed_at
      FROM scan_runs r
      JOIN scan_templates t ON t.id = r.template_id
      LEFT JOIN screening_results s ON s.scan_run_id = r.id
      WHERE r.visibility = 'global'
        AND r.status = 'succeeded'
        AND t.family = 'minervini'
        AND t.code = 'standard'
        AND r.as_of_date IS NOT NULL
      GROUP BY r.as_of_date
      ORDER BY r.as_of_date DESC
      ${sessionLimit === null ? "" : "LIMIT $1"}
    `,
    sessionLimit === null ? [] : [sessionLimit],
  )
  return result.rows.map((row) => ({
    asOfDate: row.as_of_date,
    resultCount: row.result_count,
    completedAt: row.completed_at?.toISOString() ?? null,
  }))
}
