import type { LearnGuide } from "@/lib/learn/types"

export const minerviniTrendTemplateGuide: LearnGuide = {
  slug: "minervini-trend-template",
  title: "Minervini trend template",
  metaTitle: "Minervini trend template explained",
  description:
    "How Stage 2 and the Minervini trend template frame uptrending stocks, and how Swyingify approximates those rules for Nifty 500 screening.",
  h1: "Minervini trend template: Stage 2 structure in plain English",
  definition:
    "The trend template is a checklist for confirming that a stock is in a healthy Stage 2 advance before you spend time on bases, pivots, or breakouts.",
  status: "live-linked",
  statusLabel: "Live on Swyingify as an independent rule-based approximation",
  publishedAt: "2026-08-01",
  reviewedAt: "2026-08-08",
  checklist: [
    "Price above the 150-day and 200-day moving averages.",
    "150-day average above the 200-day average.",
    "200-day average trending up (not rolling over).",
    "Price at least somewhat above the 50-day average in a constructive advance.",
    "Relative strength and near-high context support leadership, not lagging repair.",
  ],
  screenable: [
    "Moving-average stack and slope conditions.",
    "Distance from the 52-week high.",
    "Relative strength ranking versus the universe.",
    "Combined Stage 2 + contraction gates used by Swyingify templates.",
  ],
  humanJudgment: [
    "Whether the advance is orderly or a short-covering spike.",
    "Sector leadership and fundamental durability.",
    "How much extension above the 50-day is too stretched for your style.",
  ],
  failureModes: [
    "Forcing Stage 2 labels on stocks that only briefly poked above averages.",
    "Ignoring a flattening or declining 200-day average.",
    "Confusing mean-reversion bounces in Stage 1 or Stage 4 with trend continuation.",
  ],
  sections: [
    {
      id: "why",
      heading: "Why the template exists",
      paragraphs: [
        "Minervini’s trend template exists to keep traders out of the wrong market regime for momentum bases. A beautiful tight range under a broken long-term trend is usually not the same setup as a VCP inside Stage 2.",
        "The template is deliberately mechanical on purpose: it forces you to answer “is this stock already trending?” before pattern work begins.",
      ],
    },
    {
      id: "stage-2",
      heading: "Stage 2 in one paragraph",
      paragraphs: [
        "Stage 2 is the advancing phase where price respects a rising long-term average structure and higher highs / higher lows dominate. Stage 1 is accumulation or repair; Stage 3 is topping; Stage 4 is decline. Swyingify’s public scanners focus on Stage 2-style conditions for Indian large/mid names in the Nifty 500.",
      ],
    },
    {
      id: "approximation",
      heading: "How Swyingify approximates the template",
      paragraphs: [
        "Our Wide and Standard Minervini boards combine trend-stack checks with contraction, volume, and relative-strength gates. Exact numeric thresholds are versioned in the scan template — they are independent engineering choices, not a licensed Minervini product.",
        "Wide casts a broader Stage 2-style net. Standard tightens quality. Strict (paid, later) is intended for even tighter VCP/volume conditions. None of these presets place orders.",
      ],
    },
  ],
  sources: [
    {
      title: "Trade Like a Stock Market Wizard (Mark Minervini)",
      detail: "Canonical public description of the trend template and Stage analysis.",
    },
  ],
  relatedSlugs: ["stage-2-uptrend", "vcp-pattern", "relative-strength-rating"],
  liveScannerCta: true,
}
