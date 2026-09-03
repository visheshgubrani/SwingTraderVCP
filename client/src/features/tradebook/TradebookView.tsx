import { useMemo } from "react"

import { StatusChip, type StatusTone } from "@/components/terminal/bits"
import { useJournalEntries, useJournalSummary } from "@/features/journal/api"
import { toneCls } from "@/lib/format"
import { cn } from "@/lib/utils"

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
  return outcome.toUpperCase().replaceAll("_", " ")
}

function outcomeTone(outcome: string): StatusTone {
  const o = outcome.toLowerCase()
  if (o.includes("stop")) return "rej"
  if (o.includes("trailing") || o.includes("target")) return "fill"
  if (o.includes("manual")) return "wait"
  return "work"
}

/** Tradebook — closed trades reconciled from the journal. */
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
      hold_duration_days: entry.hold_duration_hours ? Math.round(entry.hold_duration_hours / 24) : 0,
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
  const avgR = summaryMetrics.avg_r != null ? Number(summaryMetrics.avg_r) : 0
  const pnlTone = toneCls(totalPnl)

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center bg-background font-mono text-xs text-muted-foreground">
        Loading closed trades from journal…
      </div>
    )
  }

  return (
    <section className="view h-full">
      <div className="vhead">
        <div>
          <h2>
            Tradebook <span className="sub">closed trades · journal source</span>
          </h2>
          <p className="vmeta">
            <b>{totalTrades}</b> closed · {wins} wins ({winRate.toFixed(1)}%) · avg R{" "}
            <b>
              {avgR > 0 ? "+" : ""}
              {avgR.toFixed(2)}
            </b>{" "}
            · latest month
          </p>
        </div>
        <div className="vhead-right">
          <div className="netp">
            <span className="lbl">NET REALIZED P&L</span>
            <span className={cn("val", pnlTone)}>
              {totalPnl >= 0 ? "+" : "-"}₹{Math.abs(totalPnl).toFixed(2)}
            </span>
          </div>
          <span className="note-demo">FROM JOURNAL</span>
        </div>
      </div>
      <div className="tscroll">
        <table className="tbl">
          <thead>
            <tr>
              <th className="l" style={{ minWidth: 92 }}>CLOSED DATE</th>
              <th className="l" style={{ minWidth: 112 }}>SYMBOL</th>
              <th className="l" style={{ minWidth: 60 }}>SIDE</th>
              <th style={{ minWidth: 56 }}>QTY</th>
              <th style={{ minWidth: 92 }}>ENTRY</th>
              <th style={{ minWidth: 92 }}>EXIT</th>
              <th style={{ minWidth: 100 }}>NET P&L</th>
              <th style={{ minWidth: 84 }}>R-MULTIPLE</th>
              <th style={{ minWidth: 72 }}>HOLD</th>
              <th className="l" style={{ minWidth: 110 }}>EXIT OUTCOME</th>
            </tr>
          </thead>
          <tbody>
            {trades.length === 0 && (
              <tr>
                <td colSpan={10} className="l" style={{ padding: 26, textAlign: "center" }}>
                  No closed journal trades yet — future app-managed fills appear here.
                </td>
              </tr>
            )}
            {trades.map((t) => {
              const isWin = t.realized_pnl >= 0
              const tone = toneCls(t.realized_pnl)
              return (
                <tr key={t.id}>
                  <td className="l">
                    {t.closed_at ? new Date(t.closed_at).toLocaleDateString("en-IN") : "—"}
                  </td>
                  <td className="l" style={{ fontWeight: 700 }}>{t.symbol}</td>
                  <td className={cn("l", isWin ? "up" : "down")} style={{ fontWeight: 700 }}>
                    {t.side.toUpperCase()}
                  </td>
                  <td>{t.quantity}</td>
                  <td>{Number.isFinite(t.entry_price) ? `₹${t.entry_price.toFixed(2)}` : "—"}</td>
                  <td>{Number.isFinite(t.exit_price) ? `₹${t.exit_price.toFixed(2)}` : "—"}</td>
                  <td className={tone}>
                    {t.realized_pnl >= 0 ? "+" : "-"}₹{Math.abs(t.realized_pnl).toFixed(2)}
                  </td>
                  <td className={tone}>
                    {t.r_multiple > 0 ? "+" : ""}
                    {t.r_multiple.toFixed(2)}R
                  </td>
                  <td>{t.hold_duration_days}d</td>
                  <td className="l">
                    <StatusChip tone={outcomeTone(t.exit_reason)}>{t.exit_reason}</StatusChip>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}
