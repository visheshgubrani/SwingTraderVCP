import type { LearnGuide } from "@/lib/learn/types"

export const volumeDryUpGuide: LearnGuide = {
  slug: "volume-dry-up",
  title: "Volume dry-up",
  metaTitle: "Volume dry-up pattern explained",
  description:
    "What volume dry-up means in swing bases, how scanners approximate supply contraction, examples of use, and false positives to avoid.",
  h1: "Volume dry-up: when supply quiets inside a base",
  definition:
    "Volume dry-up describes a decline in trading activity as a base matures — a hint that aggressive selling may be easing, not proof that buyers are about to push price higher.",
  status: "live-linked",
  statusLabel: "Used as a factor on the live Minervini board",
  publishedAt: "2026-08-01",
  reviewedAt: "2026-08-08",
  checklist: [
    "Compare recent base volume with the stock’s longer average volume.",
    "Prefer dry-up that coincides with tighter price contractions.",
    "Flag heavy down-day volume inside the base as a conflicting signal.",
    "On breakout attempts, look for volume expansion rather than continued silence.",
  ],
  screenable: [
    "Recent volume versus a multi-week average (dry-up ratio).",
    "Combined volume + ATR/Bollinger contraction gates.",
    "Liquidity floors so thin names do not dominate the board.",
  ],
  humanJudgment: [
    "Holiday weeks and index events that distort volume.",
    "Delivery versus speculative turnover context on Indian equities.",
    "Whether dry-up is constructive coiling or simply a dead stock.",
  ],
  failureModes: [
    "Treating any low-volume day as a VCP dry-up.",
    "Ignoring that illiquid names always look “dry.”",
    "Expecting dry-up alone to predict direction.",
  ],
  sections: [
    {
      id: "supply",
      heading: "Supply contraction, not a crystal ball",
      paragraphs: [
        "When volume fades while price holds a tight range under resistance, many discretionary traders read it as reduced supply. Sellers are less eager; the remaining float is quieter.",
        "That reading can be wrong. Volume can fade because interest disappeared. Context from trend, RS, and the shape of the base still matters.",
      ],
    },
    {
      id: "measure",
      heading: "How measurement usually works",
      paragraphs: [
        "Screens often divide recent average volume in the base by a longer lookback average. Ratios meaningfully below 1.0 suggest dry-up. Exact cutoffs are template choices and should be versioned.",
        "Swyingify exposes dry-up-style ratios on its Minervini board as one component among several. It is an independent approximation for education and screening.",
      ],
    },
    {
      id: "false",
      heading: "False positives",
      paragraphs: [
        "Low ADTV names, suspension aftermaths, and quiet holiday sessions can all print “dry” without forming a useful base. Always pair volume with price structure and liquidity filters.",
      ],
    },
  ],
  sources: [
    {
      title: "Trade Like a Stock Market Wizard (Mark Minervini)",
      detail: "Discusses volume behaviour through contractions and breakout attempts.",
    },
    {
      title: "How to Make Money in Stocks (William J. O’Neil)",
      detail: "Classic emphasis on volume as confirmation around bases and breakouts.",
    },
  ],
  relatedSlugs: ["vcp-pattern", "minervini-trend-template", "william-oneil-can-slim"],
  liveScannerCta: true,
}
