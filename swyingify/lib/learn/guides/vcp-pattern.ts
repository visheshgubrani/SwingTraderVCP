import type { LearnGuide } from "@/lib/learn/types"

export const vcpPatternGuide: LearnGuide = {
  slug: "vcp-pattern",
  title: "Volatility Contraction Pattern (VCP)",
  metaTitle: "What is the VCP pattern?",
  description:
    "A plain-English guide to the volatility contraction pattern: contractions, the pivot, volume behaviour, examples, and common failure cases for Indian swing traders.",
  h1: "What is the volatility contraction pattern (VCP)?",
  definition:
    "A VCP is a Stage 2 base where each pullback contracts in depth and usually in volume, until price coils under a clear pivot — a decision area, not a buy signal by itself.",
  status: "live-linked",
  statusLabel: "Live on Swyingify as an independent rule-based approximation",
  publishedAt: "2026-08-01",
  reviewedAt: "2026-08-08",
  checklist: [
    "Confirm the stock is already in a Stage 2 uptrend before studying the base.",
    "Identify at least two contractions where later pullbacks are shallower than earlier ones.",
    "Mark a clear pivot — usually the high of the base or a well-defined resistance shelf.",
    "Check whether volume dried up into the tighter contractions.",
    "Treat a breakout through the pivot as a study event, not an automatic order.",
  ],
  screenable: [
    "Price location versus rising moving averages (Stage 2 structure).",
    "Proximity to a 52-week high or recent pivot zone.",
    "Volatility compression proxies such as ATR or Bollinger width.",
    "Relative volume dry-up during the base.",
    "Relative strength versus the broader market group.",
  ],
  humanJudgment: [
    "Whether the base is constructive or simply a pause in a weak chart.",
    "News, earnings, or sector shocks that a price screen cannot see.",
    "Position size, risk, and whether the setup fits your process.",
    "False breakouts and immediate failures after the pivot is cleared.",
  ],
  failureModes: [
    "Calling every tight range a VCP even when the larger trend is broken.",
    "Ignoring expanding volume on down days inside the base (distribution).",
    "Using a vague pivot so every bounce looks like a breakout.",
    "Treating scanner rank as proof that the pattern will continue.",
  ],
  sections: [
    {
      id: "definition",
      heading: "A simple definition",
      paragraphs: [
        "Mark Minervini popularised the volatility contraction pattern as a way to describe constructive bases inside powerful uptrends. The idea is not that stocks stop moving — it is that the depth of pullbacks shrinks while demand quietly absorbs supply.",
        "In practice you look for a stock that has already proven strength, then forms a series of contractions: first a deeper pullback, then a shallower one, then an even tighter coil under resistance. That coil sits under a pivot. Clearing the pivot with constructive behaviour is when many swing traders start paying attention.",
      ],
    },
    {
      id: "contractions",
      heading: "How contractions work",
      paragraphs: [
        "A contraction is a pullback or sideways pause measured from a swing high to the subsequent swing low (or from the left side of a shelf to its quietest area). Later contractions should generally be smaller than earlier ones.",
        "You do not need a textbook three-contraction sketch on every chart. Two clear tightenings under a Stage 2 advance can still be useful. What matters is that volatility is settling while the larger trend remains intact.",
      ],
      bullets: [
        "Earlier pullbacks can be deeper and noisier.",
        "Later pullbacks should make higher lows or a tighter range.",
        "Time can vary — some bases resolve in weeks, others take longer.",
      ],
    },
    {
      id: "pivot",
      heading: "The pivot",
      paragraphs: [
        "The pivot is the price level that defines the top of the current base — often the high of the pattern or a clear resistance line traders are watching. It is a reference for study, not a guaranteed entry.",
        "Swyingify’s Minervini scanner approximates pivot context with near-high and contraction rules. It does not know your brokerage, risk limit, or whether tomorrow’s open gaps through the level.",
      ],
    },
    {
      id: "volume",
      heading: "Volume behaviour",
      paragraphs: [
        "Healthy VCPs often show volume dry-up as the base matures: fewer shares changing hands while price coils. A breakout attempt is generally more constructive when volume expands as price clears the pivot — but volume rules are still approximations.",
        "Heavy volume on down days inside the base is a warning. Screens can flag dry-up ratios; they cannot narrate every institutional footprint.",
      ],
    },
    {
      id: "limits",
      heading: "What Swyingify does and does not claim",
      paragraphs: [
        "Our live board is an independent rule-based approximation of Stage 2 / VCP-style conditions on the Nifty 500. It is educational software, not SEBI-registered advice, and it is not endorsed by Mark Minervini.",
        "A high score means the rules matched. It does not mean the stock will rise, that the pattern is “correct,” or that you should buy it.",
      ],
    },
  ],
  sources: [
    {
      title: "Trade Like a Stock Market Wizard (Mark Minervini)",
      detail: "Primary public source for Minervini’s Stage 2 and VCP teaching in book form.",
    },
    {
      title: "Think & Trade Like a Champion (Mark Minervini)",
      detail: "Further discussion of pivots, risk, and constructive bases.",
    },
  ],
  relatedSlugs: [
    "minervini-trend-template",
    "stage-2-uptrend",
    "volume-dry-up",
    "relative-strength-rating",
  ],
  liveScannerCta: true,
}
