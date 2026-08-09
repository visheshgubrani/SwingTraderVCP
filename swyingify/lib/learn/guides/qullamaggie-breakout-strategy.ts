import type { LearnGuide } from "@/lib/learn/types"

export const qullamaggieBreakoutGuide: LearnGuide = {
  slug: "qullamaggie-breakout-strategy",
  title: "Qullamaggie breakout strategy",
  metaTitle: "Qullamaggie breakout strategy guide",
  description:
    "Educational guide to Qullamaggie-style momentum bases and tight-pullback breakouts. Research status only — no live Qullamaggie scanner on Swyingify yet.",
  h1: "Qullamaggie-style momentum breakouts (educational)",
  definition:
    "Traders studying Kristjan Kullamägi’s public teaching often focus on powerful momentum names, clean bases or tight pullbacks, and breakouts with defined risk — a discretionary momentum craft more than a single rigid formula.",
  status: "research",
  statusLabel: "Research status — no live Qullamaggie scanner",
  publishedAt: "2026-08-01",
  reviewedAt: "2026-08-08",
  checklist: [
    "Start from stocks already showing exceptional momentum, not repair candidates.",
    "Prefer simple bases or tight flags after a vertical move.",
    "Define invalidation under the pullback or base low before studying entry.",
    "Expect higher volatility — momentum breakouts can fail quickly.",
  ],
  screenable: [
    "Large prior range expansion and relative strength.",
    "Tightening ranges after a run-up.",
    "Volume expansion on breakout attempts (future templates).",
  ],
  humanJudgment: [
    "Whether the theme is crowded or still expanding.",
    "Gap risk and earnings dates on high-beta names.",
    "Personal tolerance for wide daily ranges.",
  ],
  failureModes: [
    "Forcing a Qullamaggie label onto slow Stage 2 grinders.",
    "Entering mid-base without a breakout trigger or risk point.",
    "Assuming a future Swyingify preset will match any one trader’s discretionary tape reading.",
  ],
  sections: [
    {
      id: "technique",
      heading: "Momentum-base technique in brief",
      paragraphs: [
        "Public Qullamaggie-style discussions often highlight stocks that have already moved sharply, then pause in a tight consolidation before attempting another leg. The pause is the study area; the breakout is the decision area.",
        "Risk is typically framed tightly under the consolidation. That is discretionary craft — screens can only approximate the geometry.",
      ],
    },
    {
      id: "status",
      heading: "Research status on Swyingify",
      paragraphs: [
        "No live Qullamaggie scanner ships in V1. If we add one later, it will be an independent rule-based approximation with versioned config — not an endorsement.",
        "Today, study the live Minervini board for Stage 2 / VCP-style names, and treat this page as education only.",
      ],
    },
  ],
  sources: [
    {
      title: "Public Qullamaggie teaching and interviews",
      detail: "Momentum breakout process discussed in publicly available trader interviews and educational material.",
    },
  ],
  relatedSlugs: ["vcp-pattern", "darvas-box-strategy", "william-oneil-can-slim"],
  liveScannerCta: false,
}
