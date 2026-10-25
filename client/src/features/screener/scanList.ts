import { useMemo } from "react"

import {
  defaultScanRunId,
  productionScanRuns,
  useScanResults,
  useScanRuns,
  type ScanResult,
  type ScanRun,
} from "@/features/screener/api"
import { fmtDate } from "@/lib/format"

/*
 * One definition of "the scan list" shared by the Scanner table and the chart
 * sidebar, so prev/next on the chart walks exactly the order the scanner shows.
 * Filters and sort live in the URL under the same keys on both routes:
 * run, q, grade, sort, dir.
 */

export type ScanSortKey = "rank" | "score" | "rs" | "off52w" | "adtv"
export type SortDir = "asc" | "desc"

export const SCAN_SORT_LABEL: Record<ScanSortKey, string> = {
  rank: "Rank",
  score: "Score",
  rs: "RS",
  off52w: "Off 52w high",
  adtv: "ADTV",
}

/** Natural direction when a column is first chosen. */
export const SCAN_SORT_DEFAULT_DIR: Record<ScanSortKey, SortDir> = {
  rank: "asc",
  score: "desc",
  rs: "desc",
  off52w: "asc",
  adtv: "desc",
}

export const SCAN_GRADES = ["ALL", "A", "B", "C", "D"] as const

export interface ScanListFilters {
  q: string
  grade: string
  sort: string
  dir: string
}

export const SCAN_LIST_DEFAULTS: ScanListFilters & { run: string } = {
  run: "",
  q: "",
  grade: "ALL",
  sort: "rank",
  dir: "",
}

export function isScanSortKey(value: string): value is ScanSortKey {
  return value in SCAN_SORT_LABEL
}

export function resolveSort(sort: string, dir: string): { key: ScanSortKey; dir: SortDir } {
  const key = isScanSortKey(sort) ? sort : "rank"
  return { key, dir: dir === "asc" || dir === "desc" ? dir : SCAN_SORT_DEFAULT_DIR[key] }
}

export function filterScanResults(rows: ScanResult[], { q, grade }: Pick<ScanListFilters, "q" | "grade">): ScanResult[] {
  const query = q.trim().toLowerCase()
  return rows.filter((row) => {
    if (grade !== "ALL" && row.score_grade !== grade) return false
    if (!query) return true
    return (
      row.symbol.toLowerCase().includes(query) ||
      (row.name?.toLowerCase().includes(query) ?? false) ||
      row.fyers_symbol.toLowerCase().includes(query)
    )
  })
}

function sortValue(row: ScanResult, key: ScanSortKey): number | null {
  switch (key) {
    case "rank":
      return row.rank
    case "score":
      return row.technical_score
    case "rs":
      return row.rs_rating
    case "off52w":
      return Number.isFinite(row.pct_from_52w_high) ? Math.abs(row.pct_from_52w_high) : null
    case "adtv":
      return row.adtv_crore
  }
}

/** Stable sort; missing values always sink to the bottom; rank breaks ties. */
export function sortScanResults(rows: ScanResult[], sort: string, dir: string): ScanResult[] {
  const resolved = resolveSort(sort, dir)
  const factor = resolved.dir === "asc" ? 1 : -1
  return [...rows].sort((a, b) => {
    const av = sortValue(a, resolved.key)
    const bv = sortValue(b, resolved.key)
    if (av === null && bv === null) return a.rank - b.rank
    if (av === null) return 1
    if (bv === null) return -1
    if (av !== bv) return (av - bv) * factor
    return a.rank - b.rank
  })
}

export function applyScanListFilters(rows: ScanResult[], filters: ScanListFilters): ScanResult[] {
  return sortScanResults(filterScanResults(rows, filters), filters.sort, filters.dir)
}

/** "24 Oct 2026 · V4 · 179 setups" */
export function formatScanRun(run: ScanRun): string {
  const date = run.as_of_date ? fmtDate(run.as_of_date) : fmtDate(run.created_at)
  const version = run.technical_config.pipeline_version?.replace("vcp_score_", "").toUpperCase() ?? "LEGACY"
  const status = run.status === "succeeded" ? `${run.passing_count} setups` : run.status
  return `${date} · ${version} · ${status}`
}

/**
 * Production scan runs plus the results of the requested run (or the latest
 * succeeded production run when `runParam` is empty).
 */
export function useScanRunResults(runParam: string | null) {
  const scanRuns = useScanRuns()
  const runs = useMemo(() => productionScanRuns(scanRuns.data), [scanRuns.data])
  const runId = runParam || defaultScanRunId(runs)
  const run = runs.find((candidate) => candidate.id === runId)
  const resultsQuery = useScanResults(runId, run?.status)
  const results = useMemo(() => resultsQuery.data ?? [], [resultsQuery.data])
  return { scanRuns, runs, runId, run, resultsQuery, results }
}
