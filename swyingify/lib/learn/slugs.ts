export const LEARN_GUIDE_SLUGS = [
  "vcp-pattern",
  "minervini-trend-template",
  "stage-2-uptrend",
  "relative-strength-rating",
  "volume-dry-up",
  "william-oneil-can-slim",
  "qullamaggie-breakout-strategy",
  "darvas-box-strategy",
  "jesse-livermore-pivotal-points",
] as const

export type LearnGuideSlug = (typeof LEARN_GUIDE_SLUGS)[number]

export function isLearnGuideSlug(value: string): value is LearnGuideSlug {
  return (LEARN_GUIDE_SLUGS as readonly string[]).includes(value)
}
