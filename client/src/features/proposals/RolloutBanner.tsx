import { useP10Rollout, usePaperPortfolio } from "@/features/proposals/api"

function inr(value: number | null | undefined) {
  if (value == null || Number.isNaN(Number(value))) return "—"
  return Number(value).toLocaleString("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  })
}

export function RolloutBanner() {
  const rollout = useP10Rollout()
  const stage = rollout.data?.stage
  const paper = usePaperPortfolio(stage === "paper")

  if (!stage) return null

  if (stage === "shadow") {
    return (
      <div className="shrink-0 border-b border-amber-500/30 bg-amber-500/10 px-4 py-1.5 font-mono text-[11px] text-amber-200">
        P10 Shadow — proposals are review-only. Approve is blocked until paper promotion.
      </div>
    )
  }

  if (stage === "paper") {
    const cash = paper.data?.cash_available
    const equity = paper.data?.equity
    return (
      <div className="shrink-0 border-b border-sky-500/30 bg-sky-500/10 px-4 py-1.5 font-mono text-[11px] text-sky-200">
        P10 Paper — fake capital {cash != null ? inr(Number(cash)) : "…"} cash
        {equity != null ? ` · equity ${inr(Number(equity))}` : ""}. Fyers is market data only.
      </div>
    )
  }

  return (
    <div className="shrink-0 border-b border-red-500/30 bg-red-500/10 px-4 py-1.5 font-mono text-[11px] text-red-200">
      P10 {stage.replaceAll("_", " ")} — live Fyers orders are armed.
    </div>
  )
}
