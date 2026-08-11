export const FEATURES = [
  "scanner.standard",
  "scanner.strict.preview",
  "scanner.strict",
  "scanner.custom",
  "scanner.history.recent",
  "scanner.history.full",
  "scanner.export",
  "watchlists.basic",
  "watchlists.pro",
  "alerts.custom",
  "legends.paid",
] as const

export type Feature = (typeof FEATURES)[number]
export type AccessTier = "anonymous" | "free" | "pro" | "admin" | "developer"

export type AccessContext = {
  tier: AccessTier
  isAuthenticated: boolean
  isBypassed: boolean
  bypassReason: "development" | "admin" | null
  userId: string | null
  email: string | null
  features: Record<Feature, boolean>
  limits: {
    historySessions: number | null
    variantRunsPerDay: number
    watchlists: number
    watchlistSymbols: number
  }
}
