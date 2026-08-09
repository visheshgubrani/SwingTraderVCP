export const EDITORIAL = {
  name: "Swyingify Editorial",
  role: "Independent rule-based research desk",
  disclaimer:
    "Educational content only. Not SEBI-registered. Not investment advice. Not endorsed by any named trader.",
} as const

export type EditorialDates = {
  publishedAt: string
  reviewedAt: string
}

export function formatEditorialDate(isoDate: string): string {
  const date = new Date(`${isoDate}T00:00:00.000Z`)
  return new Intl.DateTimeFormat("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(date)
}
