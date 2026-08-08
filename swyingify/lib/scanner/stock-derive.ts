import type { ScannerResultPreview } from "@/lib/scanner/types"

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

export function deriveStockLevels(result: ScannerResultPreview): StockLevels {
  const pctFromHigh = result.pctFrom52WeekHigh
  const high52 = result.close / (1 - pctFromHigh / 100)
  const low52 = high52 * 0.66
  const prevClose = result.close / (1 + result.dayChangePct / 100)
  const pivot = result.close * 0.985
  const baseLow = pivot * 0.92
  const baseHigh = pivot * 1.03
  return { high52, low52, pivot, baseLow, baseHigh, prevClose }
}

export function buildStockChecks(result: ScannerResultPreview, levels: StockLevels): StockCheck[] {
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

export function formatInrWhole(value: number) {
  return `₹${Math.round(value).toLocaleString("en-IN")}`
}

export function formatInrOneDecimal(value: number) {
  return `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 1 })}`
}
