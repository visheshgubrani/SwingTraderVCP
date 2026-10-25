import { describe, expect, it } from "vitest"

import type { ScanResult } from "@/features/screener/api"
import { applyScanListFilters, filterScanResults, resolveSort, sortScanResults } from "@/features/screener/scanList"

function row(partial: Partial<ScanResult> & Pick<ScanResult, "rank" | "symbol">): ScanResult {
  return {
    id: `id-${partial.symbol}`,
    name: `${partial.symbol} Ltd`,
    fyers_symbol: `NSE:${partial.symbol}-EQ`,
    technical_score: 50,
    score_grade: "B",
    rs_rating: 80,
    pct_from_52w_high: -0.1,
    adtv_crore: 10,
    ...partial,
  } as ScanResult
}

const rows = [
  row({ rank: 1, symbol: "TCS", technical_score: 70, score_grade: "A", rs_rating: 70 }),
  row({ rank: 2, symbol: "INFY", technical_score: 90, score_grade: "A", rs_rating: 95 }),
  row({ rank: 3, symbol: "RELIANCE", technical_score: null, score_grade: null, rs_rating: 95, name: "Reliance Industries" }),
  row({ rank: 4, symbol: "HDFCBANK", technical_score: 60, score_grade: "C", pct_from_52w_high: -0.02 }),
]

describe("scan list filters", () => {
  it("filters by grade and by symbol, name or fyers symbol", () => {
    expect(filterScanResults(rows, { q: "", grade: "A" }).map((r) => r.symbol)).toEqual(["TCS", "INFY"])
    expect(filterScanResults(rows, { q: "industries", grade: "ALL" }).map((r) => r.symbol)).toEqual(["RELIANCE"])
    expect(filterScanResults(rows, { q: "nse:hdfc", grade: "ALL" }).map((r) => r.symbol)).toEqual(["HDFCBANK"])
  })

  it("uses each column's natural direction by default", () => {
    expect(resolveSort("score", "")).toEqual({ key: "score", dir: "desc" })
    expect(resolveSort("off52w", "")).toEqual({ key: "off52w", dir: "asc" })
    expect(resolveSort("bogus", "")).toEqual({ key: "rank", dir: "asc" })
  })

  it("sorts with missing values last and rank as the tie-breaker", () => {
    expect(sortScanResults(rows, "score", "").map((r) => r.symbol)).toEqual(["INFY", "TCS", "HDFCBANK", "RELIANCE"])
    expect(sortScanResults(rows, "score", "asc").map((r) => r.symbol)).toEqual(["HDFCBANK", "TCS", "INFY", "RELIANCE"])
    // INFY and RELIANCE tie on RS 95 — rank decides.
    expect(sortScanResults(rows, "rs", "").map((r) => r.symbol)).toEqual(["INFY", "RELIANCE", "HDFCBANK", "TCS"])
    expect(sortScanResults(rows, "off52w", "")[0]!.symbol).toBe("HDFCBANK")
  })

  it("filters then sorts, matching what the scanner table shows", () => {
    const result = applyScanListFilters(rows, { q: "", grade: "A", sort: "score", dir: "" })
    expect(result.map((r) => r.symbol)).toEqual(["INFY", "TCS"])
  })
})
