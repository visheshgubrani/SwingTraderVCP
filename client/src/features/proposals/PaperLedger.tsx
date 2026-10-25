import { useState } from "react"
import { LandmarkIcon } from "lucide-react"

import { EmptyState, Panel, Stat } from "@/components/terminal/panel"
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useTradingAppContext } from "@/features/dashboard/app-context"
import { fmtDateTime, fmtMoney, fmtNum, fmtR, toNum, toneCls } from "@/lib/format"
import { shortSymbol } from "@/lib/marketSymbols"
import { cn } from "@/lib/utils"
import { usePaperPortfolio, useP10Rollout, useResetPaperPortfolio } from "./api"

function ResetLedger({ startingCash }: { startingCash: number | null }) {
  const reset = useResetPaperPortfolio()
  const [open, setOpen] = useState(false)
  const [changedBy, setChangedBy] = useState("")
  const [reason, setReason] = useState("")
  const ready = changedBy.trim().length > 0 && reason.trim().length > 0

  return (
    <Panel
      actions={
        <Button onClick={() => setOpen(true)} size="sm" type="button" variant="outline">
          Reset ledger…
        </Button>
      }
      description="Restores paper cash to the configured seed. Blocked while paper positions or intents are open."
      title="Reset"
    >
      {reset.isSuccess && <p className="text-xs text-ok">Paper ledger reset.</p>}
      {reset.error instanceof Error && <p className="text-xs text-ko">{reset.error.message}</p>}
      {!reset.isSuccess && !reset.error && <p className="text-xs text-muted-text">Current seed: {fmtMoney(startingCash)}</p>}
      <AlertDialog onOpenChange={setOpen} open={open}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reset the paper ledger?</AlertDialogTitle>
            <AlertDialogDescription>
              Paper cash returns to the seed amount and paper P&L history starts over. Live balances are never touched.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="flex flex-col gap-2">
            <Input aria-label="Reset operator" onChange={(event) => setChangedBy(event.target.value)} placeholder="Your name" value={changedBy} />
            <Input aria-label="Reset reason" onChange={(event) => setReason(event.target.value)} placeholder="Reason" value={reason} />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button
              disabled={!ready || reset.isPending}
              onClick={() =>
                reset.mutate(
                  { changedBy: changedBy.trim(), reason: reason.trim() },
                  { onSettled: () => setOpen(false) },
                )
              }
              type="button"
              variant="destructive"
            >
              Reset ledger
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Panel>
  )
}

/** Paper-broker cash ledger (paper rollout stage only). */
export function PaperLedger() {
  const { openChart } = useTradingAppContext()
  const rollout = useP10Rollout()
  const isPaper = rollout.data?.stage === "paper"
  const paper = usePaperPortfolio(isPaper)

  if (rollout.isLoading) return <p className="p-4 text-xs text-muted-text">Loading rollout stage…</p>
  if (!isPaper) {
    return (
      <EmptyState icon={LandmarkIcon} title="Paper ledger is not active">
        The paper cash ledger exists only in the paper rollout stage (current stage: {rollout.data?.stage ?? "unknown"}). Live Fyers balances are never
        shown here.
      </EmptyState>
    )
  }
  if (paper.error) {
    return (
      <EmptyState icon={LandmarkIcon} title="Paper account not seeded" tone="bad">
        {paper.error.message}
      </EmptyState>
    )
  }

  const data = paper.data
  const loading = paper.isLoading
  const realized = toNum(data?.realized_pnl)
  const unrealized = toNum(data?.unrealized_pnl)
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <Stat label="Starting cash" loading={loading} value={fmtMoney(data?.starting_cash, { decimals: 0 })} />
        <Stat label="Cash available" loading={loading} value={fmtMoney(data?.cash_available, { decimals: 0 })} />
        <Stat label="Invested" loading={loading} value={fmtMoney(data?.invested_notional, { decimals: 0 })} />
        <Stat label="Equity" loading={loading} value={fmtMoney(data?.equity, { decimals: 0 })} />
        <Stat label="Open risk" loading={loading} value={fmtMoney(data?.open_risk, { decimals: 0 })} />
        <Stat
          label="Realized P&L"
          loading={loading}
          tone={realized === null || realized === 0 ? "neutral" : realized > 0 ? "ok" : "bad"}
          value={fmtMoney(realized, { sign: true, decimals: 0 })}
        />
        <Stat
          label="Unrealized P&L"
          loading={loading}
          tone={unrealized === null || unrealized === 0 ? "neutral" : unrealized > 0 ? "ok" : "bad"}
          value={fmtMoney(unrealized, { sign: true, decimals: 0 })}
        />
        <Stat
          label="Max drawdown"
          loading={loading}
          tone={(toNum(data?.max_drawdown_from_start) ?? 0) < 0 ? "bad" : "neutral"}
          value={fmtMoney(data?.max_drawdown_from_start, { decimals: 0 })}
        />
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Stat label="Closed trades" loading={loading} value={data?.closed_trade_count ?? "—"} />
        <Stat
          label="Win rate"
          loading={loading}
          value={data?.win_rate == null ? "—" : `${fmtNum(Number(data.win_rate) * 100, 0)}%`}
        />
        <Stat label="Average R" loading={loading} value={fmtR(data?.average_r_multiple, { sign: true })} />
        <Stat
          label="Ledger"
          loading={loading}
          sub={`Updated ${fmtDateTime(data?.updated_at)}`}
          value={<span className="text-xs">Seeded {fmtDateTime(data?.seeded_at)}</span>}
        />
      </div>

      <Panel flush title={`Open paper positions (${data?.open_positions.length ?? 0})`}>
        {data && data.open_positions.length > 0 ? (
          <table className="tbl">
            <thead>
              <tr>
                <th className="l">SYMBOL</th>
                <th className="l">STATE</th>
                <th>OPEN QTY</th>
                <th>AVG ENTRY</th>
                <th>REALIZED</th>
              </tr>
            </thead>
            <tbody>
              {data.open_positions.map((position) => (
                <tr key={position.id}>
                  <td className="l">
                    <button className="symlink" onClick={() => openChart(position.symbol)} type="button">
                      {shortSymbol(position.symbol)}
                    </button>
                  </td>
                  <td className="l">{position.state.replaceAll("_", " ")}</td>
                  <td>{position.open_quantity}</td>
                  <td>{fmtNum(toNum(position.average_entry_price))}</td>
                  <td className={cn(toneCls(toNum(position.realized_pnl)))}>{fmtMoney(position.realized_pnl, { sign: true })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="px-4 py-3 text-xs text-muted-text">No open paper positions.</p>
        )}
      </Panel>

      <ResetLedger startingCash={toNum(data?.starting_cash)} />
    </div>
  )
}
