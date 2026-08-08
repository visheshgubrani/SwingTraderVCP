import { queryOptions } from "@tanstack/react-query"

import { getPreviewResults } from "./fixtures"
import type { ScannerPreset } from "./types"

export const scannerKeys = {
  all: ["scanner"] as const,
  results: (preset: ScannerPreset) => [...scannerKeys.all, "results", preset] as const,
}

export function scannerResultsQuery(preset: ScannerPreset) {
  return queryOptions({
    queryKey: scannerKeys.results(preset),
    queryFn: async () => getPreviewResults(preset),
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: 30 * 60 * 1000,
  })
}

