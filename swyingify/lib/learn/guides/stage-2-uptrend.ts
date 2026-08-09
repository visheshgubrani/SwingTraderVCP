import type { LearnGuide } from "@/lib/learn/types"

export const stage2UptrendGuide: LearnGuide = {
  slug: "stage-2-uptrend",
  title: "Stage 2 uptrend",
  metaTitle: "Stage 2 stocks and uptrends",
  description:
    "How Stage 2 uptrends are read from moving-average structure, what scanners can measure, and where human judgment still matters.",
  h1: "Stage 2 stocks: reading an uptrend from structure",
  definition:
    "A Stage 2 uptrend is the advancing phase of a stock’s cycle — price supported by rising intermediate and long-term averages, with pullbacks that tend to hold above key trend lines.",
  status: "live-linked",
  statusLabel: "Used as a gate inside the live Minervini scanner",
  publishedAt: "2026-08-01",
  reviewedAt: "2026-08-08",
  checklist: [
    "Sketch the 50-, 150-, and 200-day averages on a daily chart.",
    "Ask whether the long-term average is rising, flat, or falling.",
    "Check whether price is holding above the stack after pullbacks.",
    "Separate Stage 2 continuation from Stage 1 breakouts that have not proven follow-through.",
  ],
  screenable: [
    "Moving-average relationships and slopes.",
    "Price versus averages on the as-of close.",
    "Universe filters such as Nifty 500 membership and liquidity.",
  ],
  humanJudgment: [
    "Whether the trend is late-stage and extended.",
    "Macro or sector regime shifts that invalidate a mechanical stack.",
    "Gaps and one-day wonders that pass a snapshot but fail structure the next week.",
  ],
  failureModes: [
    "Labelling every stock above its 200-day average as Stage 2 leadership.",
    "Ignoring a rolling-over 200-day while chasing near-term strength.",
    "Assuming Stage 2 alone is a complete trade plan.",
  ],
  sections: [
    {
      id: "structure",
      heading: "Moving-average structure",
      paragraphs: [
        "Traders using Weinstein/Minervini-style stage analysis lean on averages as a map of trend health. In Stage 2, the longer averages typically slope up and price spends most of its time above them.",
        "A scanner can encode those relationships as boolean gates. It cannot tell you if the advance is being driven by a one-off contract win, a speculative frenzy, or durable demand.",
      ],
    },
    {
      id: "interpretation",
      heading: "How to interpret Stage 2",
      paragraphs: [
        "Stage 2 means “the path of least resistance has been up.” It does not mean “buy now.” Many Stage 2 stocks are extended, forming messy bases, or about to transition into Stage 3.",
        "The useful question is: given Stage 2, is there a constructive base and a defined risk point — or am I forcing a story onto a noisy chart?",
      ],
    },
    {
      id: "limits",
      heading: "Scanner limitations",
      paragraphs: [
        "Swyingify evaluates Stage 2-style conditions on end-of-day Nifty 500 data. Intraday breaks, index reconstitutions, and corporate actions can change the picture between closes.",
        "Our labels are educational approximations for screening. They are not a certified stage-analysis service and not investment advice.",
      ],
    },
  ],
  sources: [
    {
      title: "Stan Weinstein’s stage analysis tradition",
      detail: "Widely taught four-stage framework that Minervini-style trend work builds upon.",
    },
    {
      title: "Trade Like a Stock Market Wizard (Mark Minervini)",
      detail: "Applies Stage 2 discipline before VCP pattern work.",
    },
  ],
  relatedSlugs: ["minervini-trend-template", "vcp-pattern", "relative-strength-rating"],
  liveScannerCta: true,
}
