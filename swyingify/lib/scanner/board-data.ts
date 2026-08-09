import { getPreviewResults } from "@/lib/scanner/fixtures"
import type { ScannerPreset, ScannerResultPreview } from "@/lib/scanner/types"

export type ScannerBoardData = {
  preset: ScannerPreset
  results: ScannerResultPreview[]
  asOfDate: string
  /** False while fixture/preview data is shown. Enables CollectionPage JSON-LD only when true. */
  isLiveData: boolean
}

/**
 * Board data for the public Minervini scanner.
 * Until the SaaS backend supplies dated global runs, this returns fixture rows with isLiveData=false.
 */
export function getScannerBoardData(preset: ScannerPreset = "standard"): ScannerBoardData {
  const results = getPreviewResults(preset)
  return {
    preset,
    results,
    asOfDate: results[0]?.asOfDate ?? "1970-01-01",
    isLiveData: false,
  }
}

export function normalizeStockSymbol(raw: string): string {
  return raw.trim().toLowerCase()
}

export function stockPath(symbol: string): string {
  return `/stocks/${normalizeStockSymbol(symbol)}`
}
