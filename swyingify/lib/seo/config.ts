/**
 * Swyingify SEO environment + site identity.
 * SITE_URL is required for production canonicals; indexing stays off until launch gate.
 */

export const SITE_NAME = "Swyingify"
export const SITE_TAGLINE = "Swing trading stock scanner for Indian stocks"
export const SITE_LOCALE = "en-IN"
export const TWITTER_HANDLE = undefined as string | undefined

/** Canonical path for the live Minervini VCP board. */
export const CANONICAL_SCANNER_PATH = "/scanners/minervini-vcp"

export function getSiteUrl(): string {
  const fromEnv = process.env.SITE_URL?.trim()
  if (fromEnv) return fromEnv.replace(/\/$/, "")

  const vercel = process.env.VERCEL_URL?.trim()
  if (vercel) return `https://${vercel.replace(/\/$/, "")}`

  return "http://localhost:3000"
}

/** Production indexing is opt-in after real data + final domain. Default: false. */
export function isSeoIndexingEnabled(): boolean {
  return process.env.SEO_INDEXING_ENABLED === "true"
}

export function getGoogleSiteVerification(): string | undefined {
  const token = process.env.GOOGLE_SITE_VERIFICATION?.trim()
  return token || undefined
}

export function absoluteUrl(path = "/"): string {
  const base = getSiteUrl()
  if (!path || path === "/") return base
  return `${base}${path.startsWith("/") ? path : `/${path}`}`
}
