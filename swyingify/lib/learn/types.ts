import type { LearnGuideSlug } from "@/lib/learn/slugs"
import type { EditorialDates } from "@/lib/seo/editorial"

export type GuideStatus = "live-linked" | "educational" | "research"

export type GuideSource = {
  title: string
  detail: string
}

export type GuideSection = {
  id: string
  heading: string
  paragraphs: string[]
  bullets?: string[]
}

export type LearnGuide = EditorialDates & {
  slug: LearnGuideSlug
  title: string
  /** Concise unique <title> without site suffix */
  metaTitle: string
  description: string
  /** Primary H1 */
  h1: string
  /** One-line definition under the H1 */
  definition: string
  status: GuideStatus
  statusLabel: string
  checklist: string[]
  screenable: string[]
  humanJudgment: string[]
  failureModes: string[]
  sections: GuideSection[]
  sources: GuideSource[]
  relatedSlugs: LearnGuideSlug[]
  /** Link to live scanner when relevant */
  liveScannerCta?: boolean
}

export const GUIDE_STATUS_COPY: Record<GuideStatus, string> = {
  "live-linked": "Linked to the live Minervini VCP scanner",
  educational: "Educational guide — no dedicated live scanner",
  research: "Research status — future scanner family, not live",
}
