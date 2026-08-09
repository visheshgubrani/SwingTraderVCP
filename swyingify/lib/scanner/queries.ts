import { queryOptions } from "@tanstack/react-query"

import { fetchStandardLatest, fetchStandardResults } from "./api"
import { getPreviewResults } from "./fixtures"
import type { ScannerLatestMeta, ScannerPreset, ScannerResultPreview } from "./types"

export const scannerKeys = {
  all: ["scanner"] as const,
  results: (preset: ScannerPreset = "standard") =>
    [...scannerKeys.all, "results", preset] as const,
  latest: () => [...scannerKeys.all, "latest"] as const,
}

function apiConfigured(): boolean {
  if (typeof window !== "undefined") {
    // Browser always goes through same-origin BFF; treat as configured.
    return true
  }
  return Boolean(process.env.API_URL?.trim() || process.env.NEXT_PUBLIC_API_URL?.trim())
}

async function loadStandardResults(): Promise<ScannerResultPreview[]> {
  try {
    const latest = await fetchStandardLatest({ cache: "no-store" })
    if (latest.status !== "succeeded") return []
    return await fetchStandardResults({ cache: "no-store" })
  } catch {
    if (!apiConfigured()) return getPreviewResults("standard")
    return []
  }
}

async function loadStandardLatest(): Promise<ScannerLatestMeta> {
  try {
    return await fetchStandardLatest({ cache: "no-store" })
  } catch {
    if (!apiConfigured()) {
      const preview = getPreviewResults("standard")
      return {
        family: "minervini",
        code: "standard",
        asOfDate: preview[0]?.asOfDate ?? null,
        status: "preview",
        completedAt: null,
        resultCount: preview.length,
        scanRunId: null,
        message: "Preview fixtures (API unavailable)",
      }
    }
    return {
      family: "minervini",
      code: "standard",
      asOfDate: null,
      status: "error",
      completedAt: null,
      resultCount: 0,
      scanRunId: null,
      message: "Could not reach the scanner API",
    }
  }
}

export function scannerResultsQuery(preset: ScannerPreset = "standard") {
  return queryOptions({
    queryKey: scannerKeys.results(preset),
    queryFn: loadStandardResults,
    staleTime: 30_000,
    gcTime: 30 * 60 * 1000,
    refetchOnWindowFocus: true,
  })
}

export function scannerLatestQuery() {
  return queryOptions({
    queryKey: scannerKeys.latest(),
    queryFn: loadStandardLatest,
    staleTime: 15_000,
    gcTime: 30 * 60 * 1000,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  })
}
