import { darvasBoxGuide } from "@/lib/learn/guides/darvas-box-strategy"
import { jesseLivermoreGuide } from "@/lib/learn/guides/jesse-livermore-pivotal-points"
import { minerviniTrendTemplateGuide } from "@/lib/learn/guides/minervini-trend-template"
import { qullamaggieBreakoutGuide } from "@/lib/learn/guides/qullamaggie-breakout-strategy"
import { relativeStrengthRatingGuide } from "@/lib/learn/guides/relative-strength-rating"
import { stage2UptrendGuide } from "@/lib/learn/guides/stage-2-uptrend"
import { vcpPatternGuide } from "@/lib/learn/guides/vcp-pattern"
import { volumeDryUpGuide } from "@/lib/learn/guides/volume-dry-up"
import { williamOneilCanSlimGuide } from "@/lib/learn/guides/william-oneil-can-slim"
import { isLearnGuideSlug, LEARN_GUIDE_SLUGS, type LearnGuideSlug } from "@/lib/learn/slugs"
import type { LearnGuide } from "@/lib/learn/types"

const GUIDE_LIST: LearnGuide[] = [
  vcpPatternGuide,
  minerviniTrendTemplateGuide,
  stage2UptrendGuide,
  relativeStrengthRatingGuide,
  volumeDryUpGuide,
  williamOneilCanSlimGuide,
  qullamaggieBreakoutGuide,
  darvasBoxGuide,
  jesseLivermoreGuide,
]

const GUIDE_MAP = Object.fromEntries(GUIDE_LIST.map((guide) => [guide.slug, guide])) as Record<
  LearnGuideSlug,
  LearnGuide
>

export function getAllGuides(): LearnGuide[] {
  return LEARN_GUIDE_SLUGS.map((slug) => GUIDE_MAP[slug])
}

export function getGuide(slug: string): LearnGuide | undefined {
  if (!isLearnGuideSlug(slug)) return undefined
  return GUIDE_MAP[slug]
}

export function getRelatedGuides(guide: LearnGuide): LearnGuide[] {
  return guide.relatedSlugs.map((slug) => GUIDE_MAP[slug]).filter(Boolean)
}

export { LEARN_GUIDE_SLUGS, isLearnGuideSlug }
export type { LearnGuideSlug }
