import type { Metadata } from "next"

import {
  absoluteUrl,
  getGoogleSiteVerification,
  getSiteUrl,
  isSeoIndexingEnabled,
  SITE_LOCALE,
  SITE_NAME,
  SITE_TAGLINE,
} from "@/lib/seo/config"

type BuildMetadataInput = {
  title: string
  description: string
  path: string
  /** Override robots. Defaults follow site indexing flag. */
  robots?: Metadata["robots"]
  /** Open Graph type */
  ogType?: "website" | "article"
  publishedTime?: string
  modifiedTime?: string
  noIndex?: boolean
}

export function buildPageMetadata({
  title,
  description,
  path,
  robots,
  ogType = "website",
  publishedTime,
  modifiedTime,
  noIndex = false,
}: BuildMetadataInput): Metadata {
  const canonical = absoluteUrl(path)
  const indexingOn = isSeoIndexingEnabled()
  const shouldIndex = indexingOn && !noIndex

  const resolvedRobots: Metadata["robots"] =
    robots ??
    (shouldIndex
      ? { index: true, follow: true }
      : { index: false, follow: noIndex ? true : false })

  return {
    title,
    description,
    alternates: { canonical: path === "/" ? absoluteUrl("/") : canonical },
    robots: resolvedRobots,
    openGraph: {
      type: ogType,
      locale: SITE_LOCALE,
      url: canonical,
      siteName: SITE_NAME,
      title: `${title} · ${SITE_NAME}`,
      description,
      ...(publishedTime ? { publishedTime } : {}),
      ...(modifiedTime ? { modifiedTime } : {}),
    },
    twitter: {
      card: "summary_large_image",
      title: `${title} · ${SITE_NAME}`,
      description,
    },
  }
}

export function rootMetadataBase(): Metadata {
  const verification = getGoogleSiteVerification()
  const indexingOn = isSeoIndexingEnabled()

  return {
    metadataBase: new URL(getSiteUrl()),
    title: {
      default: `${SITE_NAME} · ${SITE_TAGLINE}`,
      template: `%s · ${SITE_NAME}`,
    },
    description:
      "Independent rule-based swing-trading scanners for Indian equities. Browse the Minervini VCP shortlist for the Nifty 500 after every market close.",
    applicationName: SITE_NAME,
    authors: [{ name: "Swyingify Editorial" }],
    creator: SITE_NAME,
    publisher: SITE_NAME,
    formatDetection: { telephone: false, email: false, address: false },
    robots: indexingOn
      ? { index: true, follow: true }
      : { index: false, follow: false },
    openGraph: {
      type: "website",
      locale: SITE_LOCALE,
      siteName: SITE_NAME,
      title: `${SITE_NAME} · ${SITE_TAGLINE}`,
      description:
        "Independent rule-based swing-trading scanners for Indian equities. Educational only — not SEBI-registered.",
    },
    twitter: {
      card: "summary_large_image",
      title: `${SITE_NAME} · ${SITE_TAGLINE}`,
      description:
        "Independent rule-based swing-trading scanners for Indian equities. Educational only — not SEBI-registered.",
    },
    ...(verification
      ? { verification: { google: verification } }
      : {}),
  }
}

/** Filter query keys that turn a scanner URL into a noindex variant. */
export const SCANNER_FILTER_QUERY_KEYS = [
  "q",
  "search",
  "sort",
  "sector",
  "grade",
  "move",
  "direction",
  "minRs",
  "maxHigh",
  "minAdtv",
  "minScore",
] as const

export function scannerQueryIsFiltered(
  searchParams: Record<string, string | string[] | undefined>,
): boolean {
  return SCANNER_FILTER_QUERY_KEYS.some((key) => {
    const value = searchParams[key]
    if (Array.isArray(value)) return value.some((item) => Boolean(item))
    return Boolean(value)
  })
}
