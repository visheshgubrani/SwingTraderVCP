import type { LearnGuide } from "@/lib/learn/types"

export const williamOneilCanSlimGuide: LearnGuide = {
  slug: "william-oneil-can-slim",
  title: "William O’Neil and CAN SLIM",
  metaTitle: "William O’Neil CAN SLIM strategy guide",
  description:
    "Educational overview of CAN SLIM–style growth investing: leadership, earnings, and bases — with a clear note that Swyingify has no live O’Neil scanner yet.",
  h1: "William O’Neil’s CAN SLIM approach (educational)",
  definition:
    "CAN SLIM is a growth-stock framework associated with William J. O’Neil that combines fundamental leadership with technical base-and-breakout study. Swyingify does not currently run a live O’Neil scanner.",
  status: "research",
  statusLabel: "Research status — no live O’Neil scanner",
  publishedAt: "2026-08-01",
  reviewedAt: "2026-08-08",
  checklist: [
    "Separate the educational framework from any vendor’s marketed screen.",
    "Study current earnings and sales leadership alongside chart structure.",
    "Learn classic base types (cup-with-handle, double bottom, etc.) as study tools.",
    "Remember institutional sponsorship and market direction matter in the original teaching.",
  ],
  screenable: [
    "Price bases and breakout distance from pivots (when a future template exists).",
    "Relative strength leadership versus a universe.",
    "Liquidity and universe membership.",
  ],
  humanJudgment: [
    "Quality and durability of earnings surprises.",
    "Narrative risk around growth stories.",
    "Whether a base is orderly or news-driven chaos.",
  ],
  failureModes: [
    "Assuming every high-RS stock is a CAN SLIM candidate.",
    "Ignoring overall market health when studying breakouts.",
    "Expecting Swyingify’s Minervini board to be an O’Neil clone.",
  ],
  sections: [
    {
      id: "overview",
      heading: "What CAN SLIM emphasises",
      paragraphs: [
        "In O’Neil’s public teaching, letters in CAN SLIM stand for factors such as current earnings, annual earnings, new products/management/highs, supply and demand, leaders versus laggards, institutional sponsorship, and market direction. Exact emphasis has been taught across books and Investor’s Business Daily materials over decades.",
        "The practical spirit: buy strength with improving fundamentals after constructive bases — not deep-value repair stories.",
      ],
    },
    {
      id: "status",
      heading: "Swyingify product status",
      paragraphs: [
        "V1 ships Minervini Stage 2 / VCP-style scanning only. An O’Neil family may arrive later after documented rules, a versioned template, and tests — one legend at a time.",
        "This page is educational. It is not endorsed by William O’Neil or associated publishers, and it is not SEBI-registered advice.",
      ],
    },
    {
      id: "bridge",
      heading: "What you can use today",
      paragraphs: [
        "Until an O’Neil template ships, the live Minervini VCP scanner is the only public board. Concepts like relative strength and near-high bases overlap thematically, but the rule packs are not the same.",
      ],
    },
  ],
  sources: [
    {
      title: "How to Make Money in Stocks (William J. O’Neil)",
      detail: "Primary book-length presentation of CAN SLIM ideas for a general audience.",
    },
  ],
  relatedSlugs: ["relative-strength-rating", "vcp-pattern", "qullamaggie-breakout-strategy"],
  liveScannerCta: false,
}
