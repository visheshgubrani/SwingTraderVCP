import type { DailyCandle, ScannerResultPreview } from "@/lib/scanner/types"

export type StockLevels = {
  high52: number
  low52: number
  pivot: number
  baseLow: number
  baseHigh: number
  prevClose: number
}

export type StockCheck = {
  key: string
  label: string
  pass: boolean
  note: string
}

const MIN_CANDLES_FOR_RANGE_LEVELS = 20

function levelsFromPctFallback(result: ScannerResultPreview): Pick<StockLevels, "high52" | "low52" | "prevClose"> {
  const pctFromHigh = result.pctFrom52WeekHigh
  const high52 = result.close / (1 - pctFromHigh / 100)
  const low52 = high52 * 0.66
  const prevClose = result.close / (1 + result.dayChangePct / 100)
  return { high52, low52, prevClose }
}

export function deriveStockLevels(
  result: ScannerResultPreview,
  candles: DailyCandle[] = result.candles ?? [],
): StockLevels {
  const pivot = result.close * 0.985
  const baseLow = pivot * 0.92
  const baseHigh = pivot * 1.03

  if (candles.length >= MIN_CANDLES_FOR_RANGE_LEVELS) {
    let high52 = candles[0]!.high
    let low52 = candles[0]!.low
    for (const bar of candles) {
      if (bar.high > high52) high52 = bar.high
      if (bar.low < low52) low52 = bar.low
    }
    const prevClose =
      candles.length >= 2 ? candles[candles.length - 2]!.close : result.close / (1 + result.dayChangePct / 100)
    return { high52, low52, pivot, baseLow, baseHigh, prevClose }
  }

  const fallback = levelsFromPctFallback(result)
  return { ...fallback, pivot, baseLow, baseHigh }
}

export function buildStockChecks(result: ScannerResultPreview): StockCheck[] {
  const components = result.fingerprint?.components ?? []
  if (components.length === 0) {
    return legacyBuildStockChecks(result)
  }

  return components.map((component) => ({
    key: component.key,
    label: component.label,
    pass: component.status !== "watch",
    note: component.summary,
  }))
}

/** Preview fixtures when fingerprint is empty. */
function legacyBuildStockChecks(result: ScannerResultPreview): StockCheck[] {
  const pctFromHigh = result.pctFrom52WeekHigh
  const bbPct = Math.max(6, Math.round(48 - result.technicalScore * 0.4))

  return [
    {
      key: "stage",
      label: "Stage 2 uptrend",
      pass: true,
      note: "Price holds above its 50-, 150- and 200-day averages. The base forms inside an established uptrend, not a falling knife.",
    },
    {
      key: "proximity",
      label: "Near the 52-week high",
      pass: pctFromHigh <= 20,
      note: `Close sits ${pctFromHigh.toFixed(1)}% below the 52-week high — inside the 20% proximity band the scan allows.`,
    },
    {
      key: "atr",
      label: "ATR contraction",
      pass: result.atrRatio <= 1.2,
      note: `ATR(10) runs at ${result.atrRatio.toFixed(2)}× ATR(50). Volatility is coiling as the base tightens.`,
    },
    {
      key: "bb",
      label: "Bollinger squeeze",
      pass: result.technicalScore >= 86,
      note: `Bollinger width is in the bottom ${bbPct}% of its one-year range — the squeeze that precedes the move.`,
    },
    {
      key: "volume",
      label: "Volume dry-up",
      pass: result.volumeDryUpRatio <= 0.52,
      note: `10-day volume runs at ${result.volumeDryUpRatio.toFixed(2)}× the 50-day average. Sellers have stopped showing up.`,
    },
  ]
}

export function formatStockDate(d = new Date()) {
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
  const day = (d.getDate() < 10 ? "0" : "") + d.getDate()
  return `${day} ${months[d.getMonth()]} ${d.getFullYear()}`
}

export function formatStockDateFromIso(iso: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso.trim())
  if (!match) return formatStockDate()
  const year = Number(match[1])
  const month = Number(match[2]) - 1
  const day = Number(match[3])
  return formatStockDate(new Date(year, month, day))
}

export function formatInrWhole(value: number) {
  return `₹${Math.round(value).toLocaleString("en-IN")}`
}

export function formatInrOneDecimal(value: number) {
  return `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 1 })}`
}
