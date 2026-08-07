import { useMemo } from "react"

import { useJournalEntries, useJournalSummary } from "@/features/journal/api"

export interface ClosedTradeItem {
  id: string
  symbol: string
  side: "long" | "short"
  quantity: number
  entry_price: number
  exit_price: number
  realized_pnl: number
  r_multiple: number
  hold_duration_days: number
  opened_at: string
  closed_at: string
  exit_reason: string
}

function exitLabel(outcome: string | null): string {
  if (!outcome) return "UNKNOWN"
  return outcome.toUpperCase().replace("_", " ")
}

export function TradebookView() {
  const { data: listData, isLoading } = useJournalEntries({
    status: "closed",
    limit: 200,
  })
  const { data: summary } = useJournalSummary({ bucket: "month" })

  const trades: ClosedTradeItem[] = useMemo(() => {
    return (listData?.items ?? []).map((entry) => ({
      id: entry.id,
      symbol: entry.symbol,
      side: "long",
      quantity: entry.final_entry_quantity ?? entry.first_entry_quantity ?? 0,
      entry_price: Number(entry.weighted_entry_price ?? entry.first_entry_price ?? 0),
      exit_price: Number(entry.weighted_exit_price ?? 0),
      realized_pnl: Number(entry.net_pnl ?? entry.gross_pnl ?? 0),
      r_multiple: Number(entry.net_r_multiple ?? entry.gross_r_multiple ?? 0),
      hold_duration_days: entry.hold_duration_hours
        ? Math.round(entry.hold_duration_hours / 24)
        : 0,
      opened_at: entry.first_entry_fill_at ?? entry.closed_at ?? "",
      closed_at: entry.closed_at ?? "",
      exit_reason: exitLabel(entry.exit_outcome),
    }))
  }, [listData?.items])

  const summaryMetrics = (summary?.summary ?? {}) as Record<string, unknown>
  const totalTrades = trades.length
  const wins = trades.filter((t) => t.realized_pnl > 0).length
  const winRate = totalTrades > 0 ? (wins / totalTrades) * 100 : 0
  const totalPnl = trades.reduce((acc, t) => acc + t.realized_pnl, 0)
  const avgR =
    summaryMetrics.avg_r != null ? Number(summaryMetrics.avg_r) : 0

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center bg-[#080a0e] font-mono text-xs text-[#8b949e]">
        Loading closed trades from journal…
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto bg-[#080a0e] p-3 font-mono text-xs select-none">
      <div className="grid shrink-0 grid-cols-2 gap-3 md:grid-cols-4">
        <div className="rounded border border-[#252932] bg-[#0d1117] p-3">
          <span className="block text-[10px] text-[#8b949e]">TOTAL CLOSED TRADES</span>
          <span className="text-lg font-bold text-[#e6edf3]">{totalTrades}</span>
        </div>
        <div className="rounded border border-[#252932] bg-[#0d1117] p-3">
          <span className="block text-[10px] text-[#8b949e]">WIN RATE %</span>
          <span className="text-lg font-bold text-[#3b82f6]">{winRate.toFixed(1)}%</span>
        </div>
        <div className="rounded border border-[#252932] bg-[#0d1117] p-3">
          <span className="block text-[10px] text-[#8b949e]">NET REALIZED P&L</span>
          <span
            className={`text-lg font-bold ${
              totalPnl >= 0 ? "text-[#22c55e]" : "text-[#ef4444]"
            }`}
          >
            {totalPnl >= 0 ? "+" : ""}₹{totalPnl.toFixed(2)}
          </span>
        </div>
        <div className="rounded border border-[#252932] bg-[#0d1117] p-3">
          <span className="block text-[10px] text-[#8b949e]">AVG R-MULTIPLE</span>
          <span className="text-lg font-bold text-[#22c55e]">
            {avgR > 0 ? "+" : ""}
            {avgR.toFixed(2)} R
          </span>
        </div>
      </div>

      <div className="flex flex-1 flex-col overflow-hidden rounded border border-[#252932] bg-[#0d1117]">
        <div className="flex h-9 items-center justify-between border-b border-[#252932] px-3 font-bold text-[#e6edf3]">
          <span>CLOSED TRADE HISTORY</span>
        </div>
        {trades.length === 0 ? (
          <div className="p-4 text-[#8b949e]">
            No closed journal trades yet. Future app-managed fills will appear here.
          </div>
        ) : (
          <div className="flex-1 overflow-auto">
            <table className="w-full border-collapse text-left">
              <thead className="sticky top-0 border-b border-[#252932] bg-[#080a0e] text-[10px] font-semibold uppercase text-[#8b949e]">
                <tr>
                  <th className="px-3 py-2">CLOSED DATE</th>
                  <th className="px-3 py-2">SYMBOL</th>
                  <th className="px-3 py-2">SIDE</th>
                  <th className="px-3 py-2 text-right">QTY</th>
                  <th className="px-3 py-2 text-right">ENTRY (₹)</th>
                  <th className="px-3 py-2 text-right">EXIT (₹)</th>
                  <th className="px-3 py-2 text-right">NET P&L</th>
                  <th className="px-3 py-2 text-right">R-MULTIPLE</th>
                  <th className="px-3 py-2 text-center">HOLD (DAYS)</th>
                  <th className="px-3 py-2 text-center">EXIT REASON</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#161b22]">
                {trades.map((t) => {
                  const isWin = t.realized_pnl >= 0
                  return (
                    <tr key={t.id} className="transition-colors hover:bg-[#161b22]">
                      <td className="px-3 py-2 text-[11px] text-[#8b949e]">
                        {t.closed_at
                          ? new Date(t.closed_at).toLocaleDateString("en-IN")
                          : "—"}
                      </td>
                      <td className="px-3 py-2 font-bold text-[#3b82f6]">{t.symbol}</td>
                      <td className="px-3 py-2 font-semibold uppercase text-[#e6edf3]">
                        {t.side}
                      </td>
                      <td className="px-3 py-2 text-right font-bold text-[#e6edf3]">
                        {t.quantity}
                      </td>
                      <td className="px-3 py-2 text-right text-[#e6edf3]">
                        ₹{t.entry_price.toFixed(2)}
                      </td>
                      <td className="px-3 py-2 text-right text-[#e6edf3]">
                        ₹{t.exit_price.toFixed(2)}
                      </td>
                      <td
                        className={`px-3 py-2 text-right font-bold ${
                          isWin ? "text-[#22c55e]" : "text-[#ef4444]"
                        }`}
                      >
                        {isWin ? "+" : ""}₹{t.realized_pnl.toFixed(2)}
                      </td>
                      <td
                        className={`px-3 py-2 text-right font-bold ${
                          isWin ? "text-[#22c55e]" : "text-[#ef4444]"
                        }`}
                      >
                        {t.r_multiple > 0 ? "+" : ""}
                        {t.r_multiple.toFixed(2)}R
                      </td>
                      <td className="px-3 py-2 text-center text-[#e6edf3]">
                        {t.hold_duration_days}d
                      </td>
                      <td className="px-3 py-2 text-center">
                        <span className="rounded border border-[#252932] bg-[#161b22] px-1.5 py-0.5 text-[10px] text-[#8b949e]">
                          {t.exit_reason}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
