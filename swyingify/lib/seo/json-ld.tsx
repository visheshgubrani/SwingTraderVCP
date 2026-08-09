import { absoluteUrl, SITE_NAME, SITE_TAGLINE } from "@/lib/seo/config"
import { EDITORIAL } from "@/lib/seo/editorial"

type JsonLdValue = Record<string, unknown> | Record<string, unknown>[]

/** Safe JSON-LD script. Never injects user-controlled HTML. */
export function JsonLd({ data }: { data: JsonLdValue }) {
  const payload = Array.isArray(data) ? data : [data]
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(payload) }}
    />
  )
}

export function organizationJsonLd() {
  return {
    "@context": "https://schema.org",
    "@type": "Organization",
    name: SITE_NAME,
    url: absoluteUrl("/"),
    description:
      "Independent rule-based swing-trading scanners for Indian equities. Educational software — not SEBI-registered.",
    foundingDate: "2026",
  }
}

export function websiteJsonLd() {
  return {
    "@context": "https://schema.org",
    "@type": "WebSite",
    name: SITE_NAME,
    url: absoluteUrl("/"),
    description: SITE_TAGLINE,
    inLanguage: "en-IN",
    publisher: {
      "@type": "Organization",
      name: SITE_NAME,
    },
  }
}

export function breadcrumbJsonLd(items: { name: string; path: string }[]) {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: items.map((item, index) => ({
      "@type": "ListItem",
      position: index + 1,
      name: item.name,
      item: absoluteUrl(item.path),
    })),
  }
}

export function articleJsonLd(input: {
  title: string
  description: string
  path: string
  publishedAt: string
  reviewedAt: string
}) {
  return {
    "@context": "https://schema.org",
    "@type": "Article",
    headline: input.title,
    description: input.description,
    url: absoluteUrl(input.path),
    datePublished: input.publishedAt,
    dateModified: input.reviewedAt,
    inLanguage: "en-IN",
    author: {
      "@type": "Organization",
      name: EDITORIAL.name,
    },
    publisher: {
      "@type": "Organization",
      name: SITE_NAME,
      url: absoluteUrl("/"),
    },
    mainEntityOfPage: absoluteUrl(input.path),
  }
}

/** Only call when real dated scan results are available — never for fixture/preview boards. */
export function scannerCollectionJsonLd(input: {
  name: string
  description: string
  path: string
  asOfDate: string
  items: { name: string; url: string; position: number }[]
}) {
  return [
    {
      "@context": "https://schema.org",
      "@type": "CollectionPage",
      name: input.name,
      description: input.description,
      url: absoluteUrl(input.path),
      dateModified: input.asOfDate,
      isPartOf: {
        "@type": "WebSite",
        name: SITE_NAME,
        url: absoluteUrl("/"),
      },
    },
    {
      "@context": "https://schema.org",
      "@type": "ItemList",
      name: input.name,
      numberOfItems: input.items.length,
      itemListElement: input.items.map((item) => ({
        "@type": "ListItem",
        position: item.position,
        name: item.name,
        url: absoluteUrl(item.url),
      })),
    },
  ]
}
