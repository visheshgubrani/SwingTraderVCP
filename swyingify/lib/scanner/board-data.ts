import { getPreviewResult, getPreviewResults, getPreviewStockSlugs } from "@/lib/scanner/fixtures"
import { fetchScannerLatest, fetchScannerResults } from "@/lib/scanner/api"
import type { ScannerPreset, ScannerResultPreview } from "@/lib/scanner/types"

export type ScannerBoardData = {
  preset: Exclude<ScannerPreset, "custom">
  results: ScannerResultPreview[]
  asOfDate: string
  /** False while fixture/preview data is shown. Enables CollectionPage JSON-LD only when true. */
  isLiveData: boolean
  status?: string
}

export function hasBackendConfigured(): boolean {
  return Boolean(process.env.API_URL?.trim() || process.env.NEXT_PUBLIC_API_URL?.trim())
}

/**
 * Board data for the public Minervini Standard scanner (top 25).
 * When API_URL is set, never silently substitute fixtures — show live/empty/pending.
 */
export async function getScannerBoardData(
  preset: Exclude<ScannerPreset, "custom"> = "standard",
  options?: { accessToken?: string; asOfDate?: string },
): Promise<ScannerBoardData> {
  if (hasBackendConfigured()) {
    try {
      if (options?.asOfDate) {
        const results = await fetchScannerResults(preset, {
          cache: "no-store",
          asOfDate: options.asOfDate,
          accessToken: options.accessToken,
        })
        return {
          preset,
          results,
          asOfDate: options.asOfDate,
          isLiveData: true,
          status: "succeeded",
        }
      }
      const latest = await fetchScannerLatest(preset, { cache: "no-store" })
      if (latest.status === "succeeded" && (latest.resultCount ?? 0) > 0) {
        const results = await fetchScannerResults(preset, {
          cache: "no-store",
          accessToken: options?.accessToken,
        })
        return {
          preset,
          results,
          asOfDate: latest.asOfDate ?? results[0]?.asOfDate ?? "1970-01-01",
          isLiveData: true,
          status: latest.status,
        }
      }
      return {
        preset,
        results: [],
        asOfDate: latest.asOfDate ?? "1970-01-01",
        isLiveData: true,
        status: latest.status,
      }
    } catch {
      return {
        preset,
        results: [],
        asOfDate: "1970-01-01",
        isLiveData: true,
        status: "error",
      }
    }
  }

  const results = preset === "standard" ? getPreviewResults(preset) : []
  return {
    preset,
    results,
    asOfDate: results[0]?.asOfDate ?? "1970-01-01",
    isLiveData: false,
    status: "preview",
  }
}

export async function getScannerResult(
  symbol: string,
  preset: Exclude<ScannerPreset, "custom"> = "standard",
  options?: { accessToken?: string },
): Promise<ScannerResultPreview | undefined> {
  const board = await getScannerBoardData(preset, options)
  const normalized = symbol.trim().toUpperCase()
  const live = board.results.find((row) => row.symbol.toUpperCase() === normalized)
  if (live) return live
  if (!board.isLiveData && preset === "standard") return getPreviewResult(symbol)
  return undefined
}

export async function getScannerStockSlugs(): Promise<string[]> {
  if (hasBackendConfigured()) {
    try {
      const latest = await fetchScannerLatest("standard", { cache: "no-store" })
      if (latest.status !== "succeeded") return []
      const results = await fetchScannerResults("standard", { cache: "no-store" })
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

export function stockPath(
  symbol: string,
  preset: Exclude<ScannerPreset, "custom"> = "standard",
): string {
  const path = `/stocks/${normalizeStockSymbol(symbol)}`
  return preset === "strict" ? `${path}?preset=strict` : path
}
