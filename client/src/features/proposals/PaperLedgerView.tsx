import { useState } from "react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  usePaperPortfolio,
  useP10Rollout,
  useResetPaperPortfolio,
} from "@/features/proposals/api"

function inr(value: number | null | undefined) {
  if (value == null || Number.isNaN(Number(value))) return "—"
  return Number(value).toLocaleString("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
  })
}

export function PaperLedgerView() {
  const rollout = useP10Rollout()
  const paper = usePaperPortfolio(rollout.data?.stage === "paper")
  const reset = useResetPaperPortfolio()
  const [changedBy, setChangedBy] = useState("")
  const [reason, setReason] = useState("")

  if (rollout.data?.stage !== "paper") {
    return (
      <div className="flex h-full items-center justify-center bg-background p-6">
        <Alert className="max-w-xl">
          <AlertTitle>Paper ledger is stage-gated</AlertTitle>
          <AlertDescription>
            The paper cash ledger is available when paper trading is active.
            Live Fyers balances are never fabricated here.
          </AlertDescription>
        </Alert>
      </div>
    )
  }

  if (paper.error) {
    return (
      <div className="flex h-full items-center justify-center bg-background p-6">
        <Alert className="max-w-xl">
          <AlertTitle>Paper account not seeded</AlertTitle>
          <AlertDescription>{paper.error.message}</AlertDescription>
        </Alert>
      </div>
    )
  }

  const data = paper.data
  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto bg-background p-4 font-mono text-xs">
      <h1 className="text-sm font-semibold">Paper portfolio (₹1,00,000 seed)</h1>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Metric label="Starting cash" value={inr(Number(data?.starting_cash))} />
        <Metric label="Cash available" value={inr(Number(data?.cash_available))} />
        <Metric label="Invested notional" value={inr(Number(data?.invested_notional))} />
        <Metric label="Equity" value={inr(Number(data?.equity))} />
        <Metric label="Open risk" value={inr(Number(data?.open_risk))} />
        <Metric label="Realized P&L" value={inr(Number(data?.realized_pnl))} />
        <Metric
          label="Win rate"
          value={
            data?.win_rate == null
              ? "—"
              : `${(Number(data.win_rate) * 100).toFixed(0)}% (${data.closed_trade_count} closed)`
          }
        />
        <Metric
          label="Average R"
          value={data?.average_r_multiple == null ? "—" : Number(data.average_r_multiple).toFixed(2)}
        />
      </div>
      <section className="rounded-lg border bg-card p-4">
        <h2 className="mb-2 font-semibold">Reset paper cash to ₹1,00,000</h2>
        <p className="mb-3 text-muted-foreground">
          Blocked while paper positions or intents are still open. Phrase is CONFIRM_PAPER_RESET.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <Input
            aria-label="Reset operator"
            className="h-8 w-40 text-[11px]"
            onChange={(event) => setChangedBy(event.target.value)}
            placeholder="Changed by"
            value={changedBy}
          />
          <Input
            aria-label="Reset reason"
            className="h-8 min-w-48 flex-1 text-[11px]"
            onChange={(event) => setReason(event.target.value)}
            placeholder="Reason"
            value={reason}
          />
          <Button
            disabled={reset.isPending || changedBy.trim().length === 0 || reason.trim().length === 0}
            onClick={() =>
              reset.mutate({
                changedBy: changedBy.trim(),
                reason: reason.trim(),
              })
            }
            size="sm"
            type="button"
            variant="outline"
          >
            Reset ledger
          </Button>
        </div>
        {reset.error instanceof Error && (
          <p className="mt-2 text-destructive">{reset.error.message}</p>
        )}
      </section>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <section className="rounded-lg border bg-card p-4">
      <div className="text-muted-foreground">{label}</div>
      <strong className="text-lg">{value}</strong>
    </section>
  )
}
