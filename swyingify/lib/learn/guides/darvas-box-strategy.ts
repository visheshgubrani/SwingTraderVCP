import type { LearnGuide } from "@/lib/learn/types"

export const darvasBoxGuide: LearnGuide = {
  slug: "darvas-box-strategy",
  title: "Darvas box strategy",
  metaTitle: "Darvas box scanner and strategy guide",
  description:
    "How Darvas boxes are constructed, how breakout and volume logic is usually taught, and Swyingify’s research-only status for a future Darvas scanner.",
  h1: "Darvas box theory for swing study (educational)",
  definition:
    "Nicolas Darvas described trading stocks that formed price “boxes” — ranges with defined tops and bottoms — and focusing on upside breakouts, often with volume confirmation, while using the box structure for risk context.",
  status: "research",
  statusLabel: "Research status — no live Darvas scanner",
  publishedAt: "2026-08-01",
  reviewedAt: "2026-08-08",
  checklist: [
    "Identify a clear range with repeated reactions at similar highs and lows.",
    "Require the stock to already show strength before elevating a box to a study list.",
    "Treat the box top as a reference, not an automatic buy stop in software.",
    "Use the box bottom / breakdown area as an invalidation study point.",
  ],
  screenable: [
    "Horizontal range detection and breakout distance.",
    "Volume expansion versus the box average on upside breaks.",
    "Universe and liquidity gates.",
  ],
  humanJudgment: [
    "Whether the box is orderly or a noisy chop zone.",
    "News catalysts that invalidate geometric levels.",
    "Difference between a Darvas-style momentum box and a dead sideways market.",
  ],
  failureModes: [
    "Drawing boxes on every congestion and calling it Darvas theory.",
    "Ignoring volume and trend context.",
    "Expecting a future template to reproduce 1950s tape conditions literally.",
  ],
  sections: [
    {
      id: "construction",
      heading: "Box construction",
      paragraphs: [
        "A Darvas box is a practical range: a top where rallies stall and a bottom where pullbacks find support. New boxes can form as price advances and establishes higher shelves.",
        "The educational value is the discipline of defined levels. The risk is overfitting rectangles onto random noise.",
      ],
    },
    {
      id: "breakout",
      heading: "Breakout and volume logic",
      paragraphs: [
        "Classic teaching pays attention when price leaves the top of the box, often preferring expanding volume as confirmation that demand showed up. Failures back into the box are part of the study set, not anomalies to ignore.",
      ],
    },
    {
      id: "status",
      heading: "Swyingify status",
      paragraphs: [
        "Darvas remains on the research roadmap. There is no live Darvas scanner today. The Minervini VCP board is the only public scanner — an independent approximation, not endorsed by Nicolas Darvas’s estate or any named trader.",
      ],
    },
  ],
  sources: [
    {
      title: "How I Made $2,000,000 in the Stock Market (Nicolas Darvas)",
      detail: "Primary narrative source for Darvas’s box approach.",
    },
  ],
  relatedSlugs: ["qullamaggie-breakout-strategy", "vcp-pattern", "jesse-livermore-pivotal-points"],
  liveScannerCta: false,
}
