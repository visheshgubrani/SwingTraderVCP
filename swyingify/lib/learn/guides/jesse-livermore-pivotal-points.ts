import type { LearnGuide } from "@/lib/learn/types"

export const jesseLivermoreGuide: LearnGuide = {
  slug: "jesse-livermore-pivotal-points",
  title: "Jesse Livermore pivotal points",
  metaTitle: "Livermore pivotal points strategy guide",
  description:
    "Educational guide to Jesse Livermore’s pivotal points: confirmation, human judgment, and why Swyingify treats a Livermore scanner as research-only for now.",
  h1: "Livermore pivotal points (educational)",
  definition:
    "In Livermore’s recorded teaching, pivotal points are critical price levels where a market confirms a new advance or decline — moments that demand confirmation and discretion, not blind automation.",
  status: "research",
  statusLabel: "Research status — no live Livermore scanner",
  publishedAt: "2026-08-01",
  reviewedAt: "2026-08-08",
  checklist: [
    "Distinguish pivotal confirmation from ordinary noise around round numbers.",
    "Require follow-through after a level break before elevating conviction.",
    "Study both upside and downside pivotal behaviour — Livermore traded both directions in his era.",
    "Accept that much of the craft is discretionary tape reading.",
  ],
  screenable: [
    "Breaks of multi-week highs/lows with follow-through closes.",
    "Volume expansion near break events (approximate).",
    "Trend filters to avoid signalling inside pure chop.",
  ],
  humanJudgment: [
    "Whether a break is genuine confirmation or a stop run.",
    "Macro context Livermore would have absorbed from the tape and news flow.",
    "Personal rules for pyramiding — easy to misuse in software.",
  ],
  failureModes: [
    "Coding every 52-week high as a Livermore pivotal point.",
    "Removing human confirmation and calling the result “Livermore.”",
    "Romanticising historical traders while ignoring modern market structure.",
  ],
  sections: [
    {
      id: "points",
      heading: "Pivotal points in plain language",
      paragraphs: [
        "Livermore described waiting for the market to prove a move through key levels rather than predicting turns from thin air. A pivotal point is less a drawing tool and more a confirmation philosophy.",
        "That philosophy resists full automation. Any future scanner would be a coarse approximation of break-and-follow-through behaviour.",
      ],
    },
    {
      id: "judgment",
      heading: "Why human judgment dominates",
      paragraphs: [
        "Livermore’s own accounts emphasise patience, emotional control, and reading confirmation. Those are process skills. A website table cannot supply them.",
      ],
    },
    {
      id: "status",
      heading: "Research status",
      paragraphs: [
        "Livermore is queued on the Swyingify roster after earlier legend families. There is no live Livermore scanner. Use the Minervini VCP board for today’s public scans, and treat this page as historical education — not endorsement and not investment advice.",
      ],
    },
  ],
  sources: [
    {
      title: "Reminiscences of a Stock Operator ( Edwin Lefèvre )",
      detail: "Classic narrative account closely associated with Livermore’s trading life.",
    },
    {
      title: "How to Trade in Stocks (Jesse Livermore)",
      detail: "Livermore’s own later writing on pivotal points and speculative practice.",
    },
  ],
  relatedSlugs: ["darvas-box-strategy", "vcp-pattern", "stage-2-uptrend"],
  liveScannerCta: false,
}
