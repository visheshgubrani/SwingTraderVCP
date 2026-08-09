import { getPreviewResult, getPreviewResults, getPreviewStockSlugs } from "@/lib/scanner/fixtures"
import { fetchStandardLatest, fetchStandardResults } from "@/lib/scanner/api"
import type { ScannerPreset, ScannerResultPreview } from "@/lib/scanner/types"

export type ScannerBoardData = {
  preset: ScannerPreset
  results: ScannerResultPreview[]
  asOfDate: string
  /** False while fixture/preview data is shown. Enables CollectionPage JSON-LD only when true. */
  isLiveData: boolean
  status?: string
}

function hasBackendConfigured(): boolean {
  return Boolean(process.env.API_URL?.trim() || process.env.NEXT_PUBLIC_API_URL?.trim())
}

/**
 * Board data for the public Minervini Standard scanner (top 25).
 * When API_URL is set, never silently substitute fixtures — show live/empty/pending.
 */
export async function getScannerBoardData(
  preset: ScannerPreset = "standard",
): Promise<ScannerBoardData> {
  if (hasBackendConfigured()) {
    try {
      const latest = await fetchStandardLatest({ cache: "no-store" })
      if (latest.status === "succeeded" && (latest.resultCount ?? 0) > 0) {
        const results = await fetchStandardResults({ cache: "no-store" })
        return {
          preset: "standard",
          results,
          asOfDate: latest.asOfDate ?? results[0]?.asOfDate ?? "1970-01-01",
          isLiveData: true,
          status: latest.status,
        }
      }
      return {
        preset: "standard",
        results: [],
        asOfDate: latest.asOfDate ?? "1970-01-01",
        isLiveData: true,
        status: latest.status,
      }
    } catch {
      return {
        preset: "standard",
        results: [],
        asOfDate: "1970-01-01",
        isLiveData: true,
        status: "error",
      }
    }
  }

  const results = getPreviewResults(preset)
  return {
    preset: "standard",
    results,
    asOfDate: results[0]?.asOfDate ?? "1970-01-01",
    isLiveData: false,
    status: "preview",
  }
}

export async function getScannerResult(symbol: string): Promise<ScannerResultPreview | undefined> {
  const board = await getScannerBoardData("standard")
  const normalized = symbol.trim().toUpperCase()
  const live = board.results.find((row) => row.symbol.toUpperCase() === normalized)
  if (live) return live
  if (!board.isLiveData) return getPreviewResult(symbol)
  return undefined
}

export async function getScannerStockSlugs(): Promise<string[]> {
  if (hasBackendConfigured()) {
    try {
      const latest = await fetchStandardLatest({ cache: "no-store" })
      if (latest.status !== "succeeded") return []
      const results = await fetchStandardResults({ cache: "no-store" })
      return results.map((row) => row.symbol.toLowerCase()).sort()
    } catch {
      return []
    }
  }
  return getPreviewStockSlugs()
}

export function normalizeStockSymbol(raw: string): string {
  return raw.trim().toLowerCase()
}

export function stockPath(symbol: string): string {
  return `/stocks/${normalizeStockSymbol(symbol)}`
}
