import { CANONICAL_SCANNER_PATH } from "@/lib/seo/config"
import { LEARN_GUIDE_SLUGS } from "@/lib/learn/slugs"

export type IndexableRoute = {
  path: string
  /** Used by sitemap changeFrequency hints */
  changeFrequency: "daily" | "weekly" | "monthly"
  priority: number
  /** Optional lastmod ISO date */
  lastModified?: string
}

/** Public marketing / product / education routes eligible for the sitemap when indexing is on. */
export const INDEXABLE_ROUTES: IndexableRoute[] = [
  { path: "/", changeFrequency: "weekly", priority: 1 },
  { path: "/scanners", changeFrequency: "weekly", priority: 0.9 },
  { path: CANONICAL_SCANNER_PATH, changeFrequency: "daily", priority: 0.95 },
  { path: "/learn", changeFrequency: "weekly", priority: 0.85 },
  ...LEARN_GUIDE_SLUGS.map((slug) => ({
    path: `/learn/${slug}`,
    changeFrequency: "monthly" as const,
    priority: 0.75,
  })),
  { path: "/about", changeFrequency: "monthly", priority: 0.5 },
  { path: "/methodology", changeFrequency: "monthly", priority: 0.55 },
  { path: "/disclaimer", changeFrequency: "monthly", priority: 0.4 },
]

/** Paths that must never appear in the sitemap. */
export const SITEMAP_EXCLUDED_PREFIXES = [
  "/api",
  "/sign-in",
  "/sign-up",
  "/stocks",
  "/scanner", // legacy — redirects only
] as const

export const ROBOTS_DISALLOW = ["/api/", "/sign-in", "/sign-up"] as const
