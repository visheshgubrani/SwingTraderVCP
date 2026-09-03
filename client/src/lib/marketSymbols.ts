/** Symbols the terminal quotes by default (benchmarks tick regardless). */
export const NIFTY50_INDEX = "NSE:NIFTY50-INDEX"
export const NIFTY500_INDEX = "NSE:NIFTY500-INDEX"
export const BENCHMARK_SYMBOLS = [NIFTY50_INDEX]

export function shortSymbol(fyersSymbol: string): string {
  return fyersSymbol.replace(/^[A-Z0-9]+:/, "").replace(/-EQ$/, "")
}
