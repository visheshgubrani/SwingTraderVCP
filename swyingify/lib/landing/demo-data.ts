export type CheckKey = "stage2" | "rs" | "high" | "atr" | "bands" | "vol"

export type ScanDemoItem = {
  sym: string
  name: string
  sector: string
  score: number
  rs: number
  high: number
  adtv: string
  cut: number
  stage: string
  c: Record<CheckKey, 1 | 2 | 3>
}

/** Hero aside shortlist — illustrative preview with real symbols */
export const HERO_PREVIEW = [
  { sym: "KAYNES", stage: "Breakout confirmed", score: 92 },
  { sym: "SRF", stage: "Breakout day", score: 89 },
  { sym: "POLYCAB", stage: "At the pivot", score: 87 },
  { sym: "CIPLA", stage: "Dry-up forming", score: 84 },
  { sym: "DIXON", stage: "Base tightening", score: 82 },
  { sym: "TRENT", stage: "Holding the pivot", score: 79 },
] as const

export const LANDING_SCANS: ScanDemoItem[] = [
  {
    sym: "KAYNES",
    name: "Kaynes Technology",
    sector: "Capital Goods",
    score: 92,
    rs: 96,
    high: 3.2,
    adtv: "184 Cr",
    cut: 23,
    stage: "Breakout confirmed",
    c: { stage2: 1, rs: 1, high: 1, atr: 1, bands: 1, vol: 1 },
  },
  {
    sym: "SRF",
    name: "SRF",
    sector: "Chemicals",
    score: 89,
    rs: 94,
    high: 4.7,
    adtv: "96 Cr",
    cut: 21,
    stage: "Breakout day",
    c: { stage2: 1, rs: 1, high: 1, atr: 1, bands: 1, vol: 1 },
  },
  {
    sym: "POLYCAB",
    name: "Polycab India",
    sector: "Capital Goods",
    score: 87,
    rs: 91,
    high: 6.1,
    adtv: "210 Cr",
    cut: 20,
    stage: "At the pivot",
    c: { stage2: 1, rs: 1, high: 1, atr: 1, bands: 1, vol: 1 },
  },
  {
    sym: "CIPLA",
    name: "Cipla",
    sector: "Healthcare",
    score: 84,
    rs: 88,
    high: 7.4,
    adtv: "405 Cr",
    cut: 18,
    stage: "Dry-up forming",
    c: { stage2: 1, rs: 1, high: 1, atr: 1, bands: 2, vol: 1 },
  },
  {
    sym: "DIXON",
    name: "Dixon Technologies",
    sector: "Consumer Durables",
    score: 82,
    rs: 86,
    high: 8.8,
    adtv: "520 Cr",
    cut: 16,
    stage: "Contracting",
    c: { stage2: 1, rs: 1, high: 1, atr: 1, bands: 2, vol: 1 },
  },
  {
    sym: "TIINDIA",
    name: "Tube Investments",
    sector: "Automobiles",
    score: 80,
    rs: 84,
    high: 10.2,
    adtv: "85 Cr",
    cut: 14,
    stage: "Early base",
    c: { stage2: 1, rs: 1, high: 1, atr: 1, bands: 2, vol: 2 },
  },
]

export const LANDING_CHECKS: { k: CheckKey; l: string }[] = [
  { k: "stage2", l: "Stage 2 trend" },
  { k: "rs", l: "Relative strength" },
  { k: "high", l: "Near 52-week high" },
  { k: "atr", l: "ATR contraction" },
  { k: "bands", l: "Bollinger contraction" },
  { k: "vol", l: "Volume dry-up" },
]

export const STATUS_LABELS: Record<1 | 2 | 3, [string, string]> = {
  1: ["STRONG", "s-strong"],
  2: ["SUPPORTING", "s-support"],
  3: ["WATCH", "s-watch"],
}

/** Normalized VCP daily candles [o,h,l,c,vol]. Pivot = 119.2 */
export const VCP_CANDLES: [number, number, number, number, number][] = [
  [59, 60.1, 56.9, 58, 900000],
  [62, 64.2, 60.8, 63, 940000],
  [67, 69.3, 65.7, 68, 980000],
  [73, 74.4, 70.6, 72, 1020000],
  [77, 79.5, 75.5, 78, 1060000],
  [83, 85.5, 81.5, 84, 1100000],
  [92, 93.6, 89.4, 91, 1140000],
  [96, 98.7, 94.3, 97, 1180000],
  [103, 105.8, 101.2, 104, 1220000],
  [113, 114.9, 110.1, 112, 1260000],
  [115, 117.8, 113.2, 116, 520000],
  [116.5, 119.2, 114.8, 117.5, 410000],
  [117.2, 118.8, 114.6, 116.2, 360000],
  [112, 114.5, 110.5, 113, 330000],
  [114, 116.3, 112.7, 115, 290000],
  [115.2, 116.3, 113.1, 114.2, 265000],
  [111.5, 113.5, 110.5, 112.5, 240000],
  [112.5, 114.3, 111.7, 113.5, 225000],
  [112.8, 113.5, 111.1, 111.8, 215000],
  [111.4, 113.0, 110.9, 112.4, 210000],
  [117.6, 119.6, 116.6, 118.6, 880000],
  [122.2, 123.4, 120.0, 121.2, 760000],
  [122.4, 124.8, 121.1, 123.4, 1050000],
  [124.1, 126.6, 122.6, 125.1, 990000],
]

export const VCP_PIVOT = 119.2

export const METHOD_STEPS = [
  {
    num: "01",
    name: "The uptrend",
    copy: "Price holds above a rising 50-, 150- and 200-day average. A base only matters inside a Stage 2 advance.",
  },
  {
    num: "02",
    name: "The pivot",
    copy: "Price rests within a few percent of its 52-week high. A base forms under supply — near resistance, not in the middle of nowhere.",
  },
  {
    num: "03",
    name: "The contraction",
    copy: "Each pullback makes a shallower low. ATR and Bollinger width compress as the base matures and supply thins.",
  },
  {
    num: "04",
    name: "The dry-up",
    copy: "Volume fades into the base. A quiet base means nobody is distributing — the pattern holds because it is not being sold.",
  },
  {
    num: "05",
    name: "The breakout",
    copy: "Price clears the pivot on rising volume. The pattern resolves. The decision is yours to make.",
  },
] as const

export const ROADMAP_ROWS = [
  {
    name: "Minervini",
    desc: "Stage 2 · Volatility Contraction Pattern. Live today on the Nifty 500.",
    status: "Live",
    live: true,
  },
  {
    name: "O'Neil",
    desc: "CAN SLIM-style trend and earnings leadership.",
    status: "In research",
    live: false,
  },
  {
    name: "Kullamägi",
    desc: "Momentum bases and tight-pullback breakouts.",
    status: "In research",
    live: false,
  },
  {
    name: "Darvas",
    desc: "Box theory and volume breakouts from a defined shelf.",
    status: "In research",
    live: false,
  },
  {
    name: "Livermore",
    desc: "Pivotal points and follow-through confirmation.",
    status: "Queued",
    live: false,
  },
] as const
