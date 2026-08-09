import type { LearnGuide } from "@/lib/learn/types"

export const relativeStrengthRatingGuide: LearnGuide = {
  slug: "relative-strength-rating",
  title: "Relative strength rating",
  metaTitle: "Relative strength rating for stocks",
  description:
    "Plain-English relative strength (RS) ratings for stocks — what they measure, how they differ from RSI, and how scanners use them.",
  h1: "Relative strength rating: comparing stocks, not overbought oscillators",
  definition:
    "A relative strength rating ranks how a stock has performed versus its universe over a lookback window. It is not the RSI oscillator, even though both are sometimes abbreviated “RS.”",
  status: "live-linked",
  statusLabel: "Used as a factor on the live Minervini board",
  publishedAt: "2026-08-01",
  reviewedAt: "2026-08-08",
  checklist: [
    "Confirm whether a source means comparative RS rating or the RSI oscillator.",
    "Note the universe and lookback — RS is always relative to something.",
    "Prefer leadership that also has constructive price structure, not RS alone.",
    "Watch for RS that is high only because the stock spiked on thin news.",
  ],
  screenable: [
    "Rank or score versus Nifty 500 peers over a defined window.",
    "Filters that require minimum RS thresholds.",
    "Combining RS with Stage 2 and contraction gates.",
  ],
  humanJudgment: [
    "Whether leadership is broadening or a narrow speculative pocket.",
    "How much of the RS came from a single gap day.",
    "Whether the stock’s RS still fits after a sector rotation.",
  ],
  failureModes: [
    "Equating RSI overbought/oversold readings with comparative RS ratings.",
    "Buying the top RS name without a base or risk plan.",
    "Assuming yesterday’s RS leader remains tomorrow’s leader.",
  ],
  sections: [
    {
      id: "plain",
      heading: "Plain-English RS",
      paragraphs: [
        "Comparative relative strength asks: “How did this stock perform versus the rest of the list?” A rating near the top of the universe means the stock outperformed most peers over the measured period.",
        "Momentum practitioners often want that leadership present before they study breakouts. Weak RS inside a pretty chart can be a yellow flag.",
      ],
    },
    {
      id: "vs-rsi",
      heading: "RS rating vs RSI",
      paragraphs: [
        "RSI (Relative Strength Index) is a bounded oscillator derived from average gains and losses of a single symbol. It answers a different question: recent upside versus downside magnitude for that stock alone.",
        "An RSI reading of 70 is not the same thing as an RS rating of 90 versus the Nifty 500. Mixing the two is one of the most common beginner mistakes.",
      ],
    },
    {
      id: "swyingify",
      heading: "On the Swyingify board",
      paragraphs: [
        "Our preview and live boards surface an RS-style rating as one column among several. It is a ranking aid inside an independent rule-based approximation — not a promise of future outperformance.",
      ],
    },
  ],
  sources: [
    {
      title: "Investor’s Business Daily / O’Neil relative strength tradition",
      detail: "Popularised comparative RS ratings as a leadership screen for growth stocks.",
    },
    {
      title: "Trade Like a Stock Market Wizard (Mark Minervini)",
      detail: "Emphasises buying strength rather than weak mean-reversion candidates.",
    },
  ],
  relatedSlugs: ["william-oneil-can-slim", "minervini-trend-template", "vcp-pattern"],
  liveScannerCta: true,
}
