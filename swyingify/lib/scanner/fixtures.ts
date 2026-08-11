import type {
  DailyCandle,
  QualificationComponent,
  QualificationFingerprint,
  ScannerPreset,
  ScannerResultPreview,
} from "./types"

const asOfDate = "2026-08-07"
const previewDates = [
  "2026-06-12",
  "2026-06-15",
  "2026-06-16",
  "2026-06-17",
  "2026-06-18",
  "2026-06-19",
  "2026-06-22",
  "2026-06-23",
  "2026-06-24",
  "2026-06-25",
  "2026-06-26",
  "2026-06-29",
  "2026-06-30",
  "2026-07-01",
  "2026-07-02",
  "2026-07-03",
  "2026-07-06",
  "2026-07-07",
  "2026-07-08",
  "2026-07-09",
  "2026-07-10",
  "2026-07-13",
  "2026-07-14",
  "2026-07-15",
  "2026-07-16",
  "2026-07-17",
  "2026-07-20",
  "2026-07-21",
  "2026-07-22",
  "2026-07-23",
]

function makeCandles(base: number, drift: number, seed: number): DailyCandle[] {
  return previewDates.map((time, index) => {
    const wave = Math.sin(index * 0.72 + seed) * base * 0.012
    const close = base + index * drift + wave
    const open = close - Math.cos(index * 0.52 + seed) * base * 0.007
    const range = base * (0.022 - Math.min(index, 18) * 0.00028)

    return {
      time,
      open: Number(open.toFixed(2)),
      high: Number((Math.max(open, close) + range).toFixed(2)),
      low: Number((Math.min(open, close) - range).toFixed(2)),
      close: Number(close.toFixed(2)),
      volume: Math.round(780_000 - index * 10_500 + Math.abs(Math.sin(index + seed)) * 95_000),
      sma50: Number((base * 0.91 + index * drift * 0.68).toFixed(2)),
      sma150: Number((base * 0.84 + index * drift * 0.52).toFixed(2)),
      sma200: Number((base * 0.79 + index * drift * 0.39).toFixed(2)),
    }
  })
}

function component(
  key: QualificationComponent["key"],
  label: string,
  shortLabel: string,
  score: number,
  maxScore: number,
  status: QualificationComponent["status"],
  summary: string,
): QualificationComponent {
  return { key, label, shortLabel, score, maxScore, status, summary }
}

function fingerprint(
  components: QualificationComponent[],
): QualificationFingerprint {
  return {
    strongCount: components.filter((item) => item.status === "strong").length,
    totalCount: components.length,
    components,
  }
}

const standardComponents = fingerprint([
  component("stage2", "Stage 2 trend", "Trend", 19, 20, "strong", "Price is above a rising long-term trend stack."),
  component("relativeStrength", "Relative strength", "RS", 18, 20, "strong", "The stock is outperforming the broader market group."),
  component("nearHigh", "Near 52-week high", "High", 16, 17, "strong", "Price is holding close to its recent high-water mark."),
  component("atrContraction", "ATR contraction", "ATR", 14, 15, "strong", "Daily movement has tightened while the trend stays intact."),
  component("bollingerContraction", "Bollinger contraction", "Bands", 12, 15, "supporting", "The range is narrowing into a quieter decision area."),
  component("volumeDryUp", "Volume dry-up", "Volume", 11, 13, "supporting", "Volume is easing during the base rather than showing distribution."),
])

const resultSeeds = [
  { symbol: "KAYNES", companyName: "Kaynes Technology", sector: "Capital Goods", close: 9840, score: 92, grade: "A" as const, rs: 96, high: 3.2, adtv: 184, ratio: 0.42, atr: 0.88, base: 730, drift: 3.5, seed: 0.2, chg: 2.4 },
  { symbol: "SRF", companyName: "SRF", sector: "Chemicals", close: 4620, score: 89, grade: "A" as const, rs: 94, high: 4.7, adtv: 96, ratio: 0.55, atr: 0.96, base: 530, drift: 2.7, seed: 1.4, chg: 1.8 },
  { symbol: "POLYCAB", companyName: "Polycab India", sector: "Capital Goods", close: 7310, score: 87, grade: "B" as const, rs: 91, high: 6.1, adtv: 210, ratio: 0.5, atr: 1.02, base: 410, drift: 2.1, seed: 2.2, chg: 2.9 },
  { symbol: "HAL", companyName: "Hindustan Aeronautics", sector: "Capital Goods", close: 5875, score: 86, grade: "B" as const, rs: 90, high: 5.5, adtv: 410, ratio: 0.48, atr: 0.94, base: 255, drift: 1.15, seed: 3.1, chg: 1.2 },
  { symbol: "CIPLA", companyName: "Cipla", sector: "Healthcare", close: 1585, score: 84, grade: "B" as const, rs: 88, high: 7.4, adtv: 405, ratio: 0.6, atr: 1.08, base: 640, drift: 2.4, seed: 4.5, chg: 0.8 },
  { symbol: "DIXON", companyName: "Dixon Technologies", sector: "Consumer Durables", close: 18240, score: 82, grade: "B" as const, rs: 86, high: 8.8, adtv: 520, ratio: 0.65, atr: 1.12, base: 320, drift: 1.35, seed: 5.2, chg: 3.1 },
  { symbol: "BEL", companyName: "Bharat Electronics", sector: "Capital Goods", close: 418, score: 81, grade: "B" as const, rs: 85, high: 9.5, adtv: 290, ratio: 0.7, atr: 1.15, base: 470, drift: 1.2, seed: 6.1, chg: 1.5 },
  { symbol: "TIINDIA", companyName: "Tube Investments", sector: "Automobile and Auto Components", close: 8120, score: 80, grade: "B" as const, rs: 84, high: 10.2, adtv: 85, ratio: 0.72, atr: 1.18, base: 165, drift: 0.68, seed: 7.4, chg: 0.6 },
  { symbol: "PERSISTENT", companyName: "Persistent Systems", sector: "Information Technology", close: 5820, score: 79, grade: "B" as const, rs: 82, high: 11.4, adtv: 95, ratio: 0.68, atr: 1.1, base: 500, drift: 2.0, seed: 0.8, chg: 1.1 },
  { symbol: "COFORGE", companyName: "Coforge", sector: "Information Technology", close: 6740, score: 77, grade: "C" as const, rs: 80, high: 13.2, adtv: 120, ratio: 0.75, atr: 1.2, base: 580, drift: 2.2, seed: 1.9, chg: 2.2 },
  { symbol: "TRENT", companyName: "Trent", sector: "Consumer Services", close: 6110, score: 76, grade: "C" as const, rs: 79, high: 12.7, adtv: 250, ratio: 0.7, atr: 1.16, base: 540, drift: 1.8, seed: 2.6, chg: -0.4 },
  { symbol: "IRFC", companyName: "Indian Railway Finance", sector: "Financial Services", close: 172, score: 74, grade: "C" as const, rs: 76, high: 15.8, adtv: 180, ratio: 0.78, atr: 1.22, base: 150, drift: 0.5, seed: 3.3, chg: 0.9 },
]

const standardResults: ScannerResultPreview[] = resultSeeds.map((seed, index) => {
  const candles = makeCandles(seed.base, seed.drift, seed.seed)
  return {
    id: `preview-standard-${seed.symbol.toLowerCase()}`,
    symbol: seed.symbol,
    companyName: seed.companyName,
    sector: seed.sector,
    preset: "standard",
    rank: index + 1,
    asOfDate,
    close: seed.close,
    technicalScore: seed.score,
    grade: seed.grade,
    rsRating: seed.rs,
    pctFrom52WeekHigh: seed.high,
    adtvCrore: seed.adtv,
    dayChangePct: seed.chg,
    sparkSeed: seed.seed,
    sparkSeries: candles.map((c) => c.close).slice(-20),
    atrRatio: seed.atr,
    volumeDryUpRatio: seed.ratio,
    fingerprint: standardComponents,
    candles,
  }
})

export const previewResults: Record<ScannerPreset, ScannerResultPreview[]> = {
  standard: standardResults,
  strict: [],
  custom: [],
}

export function getPreviewResults(preset: ScannerPreset = "standard"): ScannerResultPreview[] {
  return previewResults[preset]
}

export function getPreviewResult(symbol: string): ScannerResultPreview | undefined {
  const normalized = symbol.trim().toUpperCase()
  return standardResults.find((result) => result.symbol === normalized)
}

/** Lowercase stock slugs that exist in the fixture board (for SSG + 404 behavior). */
export function getPreviewStockSlugs(): string[] {
  return standardResults.map((row) => row.symbol.toLowerCase()).sort()
}
