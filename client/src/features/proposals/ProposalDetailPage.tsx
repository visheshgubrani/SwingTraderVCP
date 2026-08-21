import { useState, useEffect } from "react"
import { useNavigate, useParams } from "react-router"
import {
  ArrowLeftIcon,
  CheckCircle2Icon,
  CrosshairIcon,
  CalculatorIcon,
  Edit3Icon,
  LayersIcon,
  Maximize2Icon,
  RotateCcwIcon,
  ShieldAlertIcon,
  SlidersIcon,
  SparklesIcon,
  TargetIcon,
  TrendingUpIcon,
  XCircleIcon,
} from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"
import {
  useTradeProposal,
  useRecordProposalDecision,
  useEntrySupervisorStatus,
  useP10Rollout,
  type DecisionPayload,
} from "./api"

function asFiniteNumber(value: unknown): number | null {
  if (value == null || value === "") return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function formatR(value: unknown): string | null {
  const parsed = asFiniteNumber(value)
  if (parsed == null) return null
  return `${parsed.toFixed(2)}R`
}

export function ProposalDetailPage() {
  const { proposalId } = useParams<{ proposalId: string }>()
  const navigate = useNavigate()
  const [notes] = useState("")
  const [activeChartTab, setActiveChartTab] = useState<"both" | "detail" | "context">("both")
  const [imageModal, setImageModal] = useState<string | null>(null)

  // Interactive Level Customizer State
  const [customPivot, setCustomPivot] = useState<string>("")
  const [customStop, setCustomStop] = useState<string>("")
  const [customT1, setCustomT1] = useState<string>("")
  const [customT2, setCustomT2] = useState<string>("")
  const [customT3, setCustomT3] = useState<string>("")
  const [customTemplate, setCustomTemplate] = useState<string>("")
  const [customLeg2Price, setCustomLeg2Price] = useState<string>("")
  const [isCustomizing, setIsCustomizing] = useState<boolean>(false)

  const { data: proposal, isLoading, error } = useTradeProposal(proposalId ?? null)
  const recordDecision = useRecordProposalDecision()
  const rollout = useP10Rollout()
  const entrySupervisor = useEntrySupervisorStatus()
  const approvalsAllowed = rollout.data?.approvals_allowed === true

  useEffect(() => {
    if (proposal && !customPivot) {
      setCustomPivot(String(proposal.pivot_price))
      setCustomStop(String(proposal.initial_stop))
      setCustomT1(String(proposal.t1))
      setCustomT2(String(proposal.t2))
      setCustomT3(String(proposal.t3))
      setCustomTemplate(proposal.entry_template)
    }
  }, [proposal])

  if (isLoading) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-background text-foreground font-mono">
        <div className="flex flex-col items-center gap-3">
          <Spinner className="h-6 w-6 text-primary" />
          <p className="text-xs text-muted-foreground">Loading proposal details and calculation basis…</p>
        </div>
      </div>
    )
  }

  if (error || !proposal) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-4 bg-background p-6 font-mono text-foreground">
        <Alert variant="destructive" className="max-w-md">
          <ShieldAlertIcon className="h-4 w-4" />
          <AlertTitle>Proposal Not Found</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : "The requested trade proposal could not be found."}
          </AlertDescription>
        </Alert>
        <Button variant="outline" size="sm" onClick={() => navigate("/proposals")}>
          <ArrowLeftIcon className="mr-1.5 h-3.5 w-3.5" /> Back to Proposals
        </Button>
      </div>
    )
  }

  const geometry = proposal.geometry ?? {}
  const calcBasis = geometry.calculation_basis
  const grounding = geometry.pivot_grounding
  const t1R = formatR(geometry.t1_r ?? calcBasis?.targets.t1.r_at_ceiling)
  const t2R = formatR(geometry.t2_r ?? calcBasis?.targets.t2.r_at_ceiling)
  const t3R = formatR(geometry.t3_r ?? calcBasis?.targets.t3.r_at_ceiling)
  const baseCeiling = asFiniteNumber(geometry.base_chase_ceiling ?? calcBasis?.entry_chase.base_chase_ceiling)
  const lockedCeiling = Number(proposal.chase_ceiling)
  const ceilingTightened =
    baseCeiling != null && Number.isFinite(lockedCeiling) && baseCeiling > lockedCeiling

  const isPending = proposal.status === "pending_approval"
  const isApproved = proposal.status === "approved"
  const entryMonitoringReady =
    entrySupervisor.data?.status === "active" &&
    entrySupervisor.data.market_data.ready
  const monitoringIssue =
    entrySupervisor.data?.status !== "active"
      ? "the entry supervisor is inactive"
      : `the market-data worker is ${entrySupervisor.data.market_data.status}`
  const activePivot = customPivot ? Number(customPivot) : Number(proposal.pivot_price)
  const activeStop = customStop ? Number(customStop) : Number(proposal.initial_stop)
  const activeT1 = customT1 ? Number(customT1) : Number(proposal.t1)
  const activeT2 = customT2 ? Number(customT2) : Number(proposal.t2)
  const activeT3 = customT3 ? Number(customT3) : Number(proposal.t3)

  const liveStopDistPct = activePivot > 0 && activeStop > 0 ? ((activePivot - activeStop) / activePivot) * 100 : 0
  const liveRDistance = activePivot - activeStop
  const liveT1R = liveRDistance > 0 && activeT1 > activePivot ? ((activeT1 - activePivot) / liveRDistance).toFixed(2) : null
  const liveT2R = liveRDistance > 0 && activeT2 > activePivot ? ((activeT2 - activePivot) / liveRDistance).toFixed(2) : null
  const liveT3R = liveRDistance > 0 && activeT3 > activePivot ? ((activeT3 - activePivot) / liveRDistance).toFixed(2) : null
  const riskBudget = Number(proposal.approved_risk_budget_amount ?? 0)
  const liveShares = liveRDistance > 0 && riskBudget > 0 ? Math.floor(riskBudget / liveRDistance) : 0

  const handleDecision = async (decision: "approved" | "rejected") => {
    try {
      const payload: DecisionPayload = {
        decision,
        expected_proposal_hash: proposal.proposal_hash,
        notes: notes.trim() || undefined,
      }
      if (decision === "approved" && isCustomizing) {
        if (customPivot && Number(customPivot) !== Number(proposal.pivot_price)) {
          payload.adjusted_pivot_price = Number(customPivot)
        }
        if (customStop && Number(customStop) !== Number(proposal.initial_stop)) {
          payload.adjusted_initial_stop = Number(customStop)
        }
        if (customT1 && Number(customT1) !== Number(proposal.t1)) {
          payload.adjusted_t1 = Number(customT1)
        }
        if (customT2 && Number(customT2) !== Number(proposal.t2)) {
          payload.adjusted_t2 = Number(customT2)
        }
        if (customT3 && Number(customT3) !== Number(proposal.t3)) {
          payload.adjusted_t3 = Number(customT3)
        }
        if (customTemplate && customTemplate !== proposal.entry_template) {
          payload.adjusted_entry_template = customTemplate as any
        }
        if (customLeg2Price) {
          payload.adjusted_leg2_price = Number(customLeg2Price)
        }
      }
      await recordDecision.mutateAsync({
        id: proposal.id,
        payload,
      })
    } catch (err: any) {
      alert(`Decision error: ${err.message}`)
    }
  }

  const templateColors: Record<string, string> = {
    single: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    two_leg: "bg-blue-500/10 text-blue-400 border-blue-500/20",
    two_leg_staged: "bg-cyan-500/10 text-cyan-400 border-cyan-500/20",
    three_leg_front: "bg-purple-500/10 text-purple-400 border-purple-500/20",
    three_leg_balanced: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  }

  const contextChartSrc = `/api/v1/automation/proposals/${proposal.id}/charts/context`
  const detailChartSrc = `/api/v1/automation/proposals/${proposal.id}/charts/detail`

  return (
    <div className="flex h-full w-full flex-col overflow-y-auto bg-background font-mono text-xs text-foreground">
      {/* Top Header */}
      <div className="sticky top-0 z-20 flex shrink-0 items-center justify-between border-b border-border/80 bg-card/95 px-4 py-3 backdrop-blur shadow-sm">
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="h-8 gap-1 px-2.5 text-xs text-muted-foreground hover:text-foreground"
            onClick={() => navigate("/proposals")}
          >
            <ArrowLeftIcon className="h-3.5 w-3.5" /> Proposals
          </Button>

          <span className="text-border">|</span>

          <div className="flex items-center gap-2">
            <h1 className="text-lg font-bold tracking-tight text-foreground">
              {proposal.symbol}
            </h1>
            <Badge variant="outline" className={templateColors[proposal.entry_template] || ""}>
              {proposal.entry_template.toUpperCase()}
            </Badge>
            <Badge variant="outline" className="text-[10px]">
              CONF: {(Number(proposal.confidence) * 100).toFixed(0)}%
            </Badge>
            {proposal.live_eligible ? (
              <Badge variant="default" className="bg-emerald-600/20 text-emerald-400 border-emerald-500/30">
                LIVE ELIGIBLE
              </Badge>
            ) : (
              <Badge variant="secondary" className="text-amber-300">
                REVIEW ONLY
              </Badge>
            )}
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="hidden text-right text-[11px] text-muted-foreground sm:block">
            <div>Session: <strong className="text-foreground">{proposal.entry_session_date}</strong></div>
            <div className="text-[10px]">
              Deadline: {new Date(proposal.approval_deadline).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit" })} IST
            </div>
          </div>

          <Badge
            variant={isApproved ? "default" : isPending ? "secondary" : "destructive"}
            className="h-7 px-3 text-[11px] font-bold uppercase tracking-wider"
          >
            {proposal.status.replaceAll("_", " ")}
          </Badge>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="mx-auto w-full max-w-7xl space-y-6 p-4 sm:p-6">
        {/* Key Numerical Levels Dashboard */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <div className="rounded-lg border border-border/70 bg-card p-3 shadow-sm">
            <div className="flex items-center justify-between text-[10px] uppercase text-muted-foreground">
              <span>Pivot Breakout</span>
              <CrosshairIcon className="h-3 w-3 text-primary" />
            </div>
            <div className="mt-1 text-base font-bold text-foreground">
              ₹{Number(proposal.pivot_price).toFixed(2)}
            </div>
            <div className="mt-0.5 text-[10px] text-muted-foreground">
              Trigger entry point
            </div>
          </div>

          <div className="rounded-lg border border-border/70 bg-card p-3 shadow-sm">
            <div className="flex items-center justify-between text-[10px] uppercase text-muted-foreground">
              <span>Chase Ceiling</span>
              <TrendingUpIcon className="h-3 w-3 text-amber-400" />
            </div>
            <div className="mt-1 text-base font-bold text-foreground">
              ₹{lockedCeiling.toFixed(2)}
            </div>
            <div className="mt-0.5 text-[10px] text-muted-foreground">
              +{(((lockedCeiling - Number(proposal.pivot_price)) / Number(proposal.pivot_price)) * 100).toFixed(2)}% max chase
            </div>
          </div>

          <div className="rounded-lg border border-border/70 bg-card p-3 shadow-sm">
            <div className="flex items-center justify-between text-[10px] uppercase text-muted-foreground">
              <span>Structural SL</span>
              <ShieldAlertIcon className="h-3 w-3 text-rose-400" />
            </div>
            <div className="mt-1 text-base font-bold text-rose-400">
              ₹{Number(proposal.initial_stop).toFixed(2)}
            </div>
            <div className="mt-0.5 text-[10px] text-rose-400/80">
              -{Number(proposal.stop_distance_pct).toFixed(2)}% risk distance
            </div>
          </div>

          <div className="rounded-lg border border-border/70 bg-card p-3 shadow-sm">
            <div className="flex items-center justify-between text-[10px] uppercase text-muted-foreground">
              <span>Target 1</span>
              <TargetIcon className="h-3 w-3 text-emerald-400" />
            </div>
            <div className="mt-1 text-base font-bold text-emerald-400">
              ₹{Number(proposal.t1).toFixed(2)}
            </div>
            <div className="mt-0.5 text-[10px] text-emerald-400/80 font-semibold">
              {t1R ? `${t1R} at ceiling` : "Primary 1R+"}
            </div>
          </div>

          <div className="rounded-lg border border-border/70 bg-card p-3 shadow-sm">
            <div className="flex items-center justify-between text-[10px] uppercase text-muted-foreground">
              <span>Target 2</span>
              <TargetIcon className="h-3 w-3 text-emerald-400/80" />
            </div>
            <div className="mt-1 text-base font-bold text-foreground">
              ₹{Number(proposal.t2).toFixed(2)}
            </div>
            <div className="mt-0.5 text-[10px] text-muted-foreground">
              {t2R ? `${t2R} expansion` : "2R objective"}
            </div>
          </div>

          <div className="rounded-lg border border-border/70 bg-card p-3 shadow-sm">
            <div className="flex items-center justify-between text-[10px] uppercase text-muted-foreground">
              <span>Target 3 (Runner)</span>
              <TargetIcon className="h-3 w-3 text-emerald-400/60" />
            </div>
            <div className="mt-1 text-base font-bold text-foreground">
              ₹{Number(proposal.t3).toFixed(2)}
            </div>
            <div className="mt-0.5 text-[10px] text-muted-foreground">
              {t3R ? `${t3R} major swing` : "3R objective"}
            </div>
          </div>
        </div>

        {/* Interactive Customizer & Human Approval Checkpoint */}
        {isPending && (
          <div className="rounded-xl border border-primary/40 bg-card p-4 shadow-sm space-y-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between border-b border-border/50 pb-3">
              <div>
                <div className="flex items-center gap-2 text-sm font-bold text-foreground">
                  <SlidersIcon className="h-4 w-4 text-primary" />
                  Interactive Trade Plan & Price Levels
                </div>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  Review AI suggested levels, or fine-tune Entry (Cheat Pivot vs Base Breakout), Stop Loss, and Targets before arming automated execution.
                </p>
                {!approvalsAllowed && (
                  <p className="mt-1 text-[10px] text-amber-400">
                    Shadow stage active: Review or reject only. Promote rollout stage to Paper before approving live trades.
                  </p>
                )}
              </div>

              <div className="flex items-center gap-2">
                <Button
                  variant={isCustomizing ? "default" : "outline"}
                  size="xs"
                  className="gap-1.5"
                  onClick={() => setIsCustomizing(!isCustomizing)}
                >
                  <Edit3Icon className="h-3 w-3" />
                  {isCustomizing ? "Customizing Active" : "Customize Levels"}
                </Button>
                {isCustomizing && (
                  <Button
                    variant="ghost"
                    size="xs"
                    className="gap-1 text-muted-foreground hover:text-foreground"
                    onClick={() => {
                      setCustomPivot(String(proposal.pivot_price))
                      setCustomStop(String(proposal.initial_stop))
                      setCustomT1(String(proposal.t1))
                      setCustomT2(String(proposal.t2))
                      setCustomT3(String(proposal.t3))
                      setCustomTemplate(proposal.entry_template)
                      setCustomLeg2Price("")
                      setIsCustomizing(false)
                    }}
                  >
                    <RotateCcwIcon className="h-3 w-3" /> Reset to AI
                  </Button>
                )}
              </div>
            </div>

            {/* Customizer Form Grid */}
            {isCustomizing && (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 bg-muted/10 p-3 rounded-lg border border-border/40">
                <div className="space-y-1">
                  <label className="text-[10px] uppercase font-semibold text-muted-foreground">
                    Leg 1 Entry Pivot (₹)
                  </label>
                  <Input
                    type="number"
                    step="0.05"
                    value={customPivot}
                    onChange={(e) => setCustomPivot(e.target.value)}
                    className="h-8 font-mono text-xs"
                    placeholder="Pivot price"
                  />
                </div>

                <div className="space-y-1">
                  <div className="flex justify-between items-center">
                    <label className="text-[10px] uppercase font-semibold text-muted-foreground">
                      Initial Stop Loss (₹)
                    </label>
                    <div className="flex gap-1">
                      <button
                        type="button"
                        onClick={() => {
                          const p = Number(customPivot) || Number(proposal.pivot_price)
                          setCustomStop((p * 0.995).toFixed(2))
                        }}
                        className="text-[9px] px-1 py-0.5 rounded bg-muted hover:bg-muted/80 text-muted-foreground"
                      >
                        -0.5%
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          const p = Number(customPivot) || Number(proposal.pivot_price)
                          setCustomStop((p * 0.99).toFixed(2))
                        }}
                        className="text-[9px] px-1 py-0.5 rounded bg-muted hover:bg-muted/80 text-muted-foreground"
                      >
                        -1.0%
                      </button>
                    </div>
                  </div>
                  <Input
                    type="number"
                    step="0.05"
                    value={customStop}
                    onChange={(e) => setCustomStop(e.target.value)}
                    className="h-8 font-mono text-xs"
                    placeholder="Stop loss price"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-[10px] uppercase font-semibold text-muted-foreground">
                    Execution Template
                  </label>
                  <select
                    value={customTemplate}
                    onChange={(e) => setCustomTemplate(e.target.value)}
                    className="w-full h-8 rounded-md border border-input bg-background px-2 text-xs font-mono text-foreground"
                  >
                    <option value="single">Single Leg (100%)</option>
                    <option value="two_leg_staged">2-Leg Staged (Cheat 50% + Breakout 50%)</option>
                    <option value="two_leg">2-Leg Standard (60% / 40%)</option>
                    <option value="three_leg_front">3-Leg Front (50% / 30% / 20%)</option>
                    <option value="three_leg_balanced">3-Leg Balanced (40% / 30% / 30%)</option>
                  </select>
                </div>

                {customTemplate === "two_leg_staged" && (
                  <div className="space-y-1">
                    <label className="text-[10px] uppercase font-semibold text-muted-foreground">
                      Leg 2 Breakout Add (₹)
                    </label>
                    <Input
                      type="number"
                      step="0.05"
                      value={customLeg2Price}
                      onChange={(e) => setCustomLeg2Price(e.target.value)}
                      className="h-8 font-mono text-xs"
                      placeholder="Base Breakout price"
                    />
                  </div>
                )}

                <div className="space-y-1">
                  <label className="text-[10px] uppercase font-semibold text-muted-foreground">
                    Target 1 (₹)
                  </label>
                  <Input
                    type="number"
                    step="0.05"
                    value={customT1}
                    onChange={(e) => setCustomT1(e.target.value)}
                    className="h-8 font-mono text-xs"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-[10px] uppercase font-semibold text-muted-foreground">
                    Target 2 (₹)
                  </label>
                  <Input
                    type="number"
                    step="0.05"
                    value={customT2}
                    onChange={(e) => setCustomT2(e.target.value)}
                    className="h-8 font-mono text-xs"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-[10px] uppercase font-semibold text-muted-foreground">
                    Target 3 (₹)
                  </label>
                  <Input
                    type="number"
                    step="0.05"
                    value={customT3}
                    onChange={(e) => setCustomT3(e.target.value)}
                    className="h-8 font-mono text-xs"
                  />
                </div>
              </div>
            )}

            {/* Live Metrics Feedback */}
            <div className="flex flex-wrap items-center justify-between gap-3 bg-muted/20 p-3 rounded-lg border border-border/30 text-[11px]">
              <div className="flex items-center gap-4">
                <div>
                  <span className="text-muted-foreground">Stop Distance: </span>
                  <strong className={Number(liveStopDistPct) <= 8 && Number(liveStopDistPct) > 0 ? "text-emerald-400" : "text-destructive"}>
                    {Number(liveStopDistPct).toFixed(2)}%
                  </strong>
                  {Number(liveStopDistPct) > 8 && (
                    <span className="ml-1 text-[10px] text-destructive">(Exceeds 8% limit!)</span>
                  )}
                </div>
                <div>
                  <span className="text-muted-foreground">Est. Position Sizing: </span>
                  <strong className="text-foreground">{liveShares} shares</strong>
                  <span className="text-muted-foreground"> (~₹{(liveShares * Number(activePivot)).toLocaleString("en-IN")})</span>
                </div>
                <div>
                  <span className="text-muted-foreground">T1 R:R: </span>
                  <strong className={Number(liveT1R ?? 0) >= 1 ? "text-emerald-400" : "text-amber-400"}>
                    {liveT1R ? `${liveT1R}R` : "—"}
                  </strong>
                </div>
                <div>
                  <span className="text-muted-foreground">T2: </span>
                  <strong className="text-foreground">{liveT2R ? `${liveT2R}R` : "—"}</strong>
                </div>
                <div>
                  <span className="text-muted-foreground">T3: </span>
                  <strong className="text-foreground">{liveT3R ? `${liveT3R}R` : "—"}</strong>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <Button
                  variant="destructive"
                  size="sm"
                  className="font-bold"
                  disabled={recordDecision.isPending}
                  onClick={() => handleDecision("rejected")}
                >
                  <XCircleIcon className="mr-1.5 h-3.5 w-3.5" /> Reject Plan
                </Button>
                <Button
                  variant="default"
                  size="sm"
                  className="bg-emerald-600 hover:bg-emerald-500 text-white font-bold"
                  disabled={recordDecision.isPending || !approvalsAllowed || Number(liveStopDistPct) > 8 || Number(liveStopDistPct) <= 0}
                  onClick={() => handleDecision("approved")}
                >
                  <CheckCircle2Icon className="mr-1.5 h-3.5 w-3.5" />
                  {isCustomizing ? "Approve & Arm Custom Plan" : "Approve & Arm Leg 1"}
                </Button>
              </div>
            </div>
          </div>
        )}

        {isApproved && entryMonitoringReady && (
          <Alert>
            <CheckCircle2Icon />
            <AlertTitle>Approved and actively monitored</AlertTitle>
            <AlertDescription>
              The entry supervisor is receiving Fyers market data and monitoring fresh 5-minute confirmation bars for {proposal.symbol}.
            </AlertDescription>
          </Alert>
        )}

        {isApproved && !entryMonitoringReady && (
          <Alert variant="destructive">
            <ShieldAlertIcon />
            <AlertTitle>Entry monitoring unavailable</AlertTitle>
            <AlertDescription>
              This proposal remains armed, but entries cannot be detected because {monitoringIssue}. Paper trading still requires live Fyers ticks. No order can be placed until market data recovers and a fresh two-bar confirmation completes.
            </AlertDescription>
          </Alert>
        )}

        {/* High-Resolution Headless Rendered Charts */}
        <div className="rounded-xl border border-border/70 bg-card overflow-hidden shadow-sm">
          <div className="flex flex-wrap items-center justify-between border-b border-border/60 bg-muted/20 px-4 py-2.5">
            <div className="flex items-center gap-2">
              <SparklesIcon className="h-4 w-4 text-primary" />
              <span className="font-semibold text-foreground uppercase tracking-wider text-[11px]">
                Deterministic Proposal Charts
              </span>
              <Badge variant="outline" className="text-[10px]">
                {proposal.renderer_version}
              </Badge>
            </div>

            <div className="flex items-center gap-1.5">
              <Button
                variant={activeChartTab === "both" ? "secondary" : "ghost"}
                size="xs"
                onClick={() => setActiveChartTab("both")}
              >
                Both Charts
              </Button>
              <Button
                variant={activeChartTab === "detail" ? "secondary" : "ghost"}
                size="xs"
                onClick={() => setActiveChartTab("detail")}
              >
                Detail (126s)
              </Button>
              <Button
                variant={activeChartTab === "context" ? "secondary" : "ghost"}
                size="xs"
                onClick={() => setActiveChartTab("context")}
              >
                Context (252s)
              </Button>
            </div>
          </div>

          <div className={`p-4 grid gap-4 ${activeChartTab === "both" ? "lg:grid-cols-2" : "grid-cols-1"}`}>
            {(activeChartTab === "both" || activeChartTab === "context") && (
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between text-[10px] text-muted-foreground uppercase">
                  <span>Context View · 252 Sessions (Log Scale + 50/150/200 SMA)</span>
                  <button
                    type="button"
                    onClick={() => setImageModal(contextChartSrc)}
                    className="hover:text-foreground text-muted-foreground flex items-center gap-1"
                  >
                    <Maximize2Icon className="h-3 w-3" /> Expand
                  </button>
                </div>
                <div className="relative group overflow-hidden rounded-lg border border-border/60 bg-black">
                  <img
                    src={contextChartSrc}
                    alt={`${proposal.symbol} 252-session context`}
                    className="w-full object-contain cursor-pointer transition-transform duration-200 group-hover:scale-[1.01]"
                    onClick={() => setImageModal(contextChartSrc)}
                  />
                </div>
              </div>
            )}

            {(activeChartTab === "both" || activeChartTab === "detail") && (
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between text-[10px] text-muted-foreground uppercase">
                  <span>Inference Detail · 126 Sessions (Annotated VCP Geometry + 21 EMA)</span>
                  <button
                    type="button"
                    onClick={() => setImageModal(detailChartSrc)}
                    className="hover:text-foreground text-muted-foreground flex items-center gap-1"
                  >
                    <Maximize2Icon className="h-3 w-3" /> Expand
                  </button>
                </div>
                <div className="relative group overflow-hidden rounded-lg border border-border/60 bg-black">
                  <img
                    src={detailChartSrc}
                    alt={`${proposal.symbol} 126-session detail`}
                    className="w-full object-contain cursor-pointer transition-transform duration-200 group-hover:scale-[1.01]"
                    onClick={() => setImageModal(detailChartSrc)}
                  />
                </div>
              </div>
            )}
          </div>
        </div>

        {/* DECISION & CALCULATION BASIS (The Core Feature) */}
        <div className="space-y-4">
          <div className="flex items-center gap-2 border-b border-border/60 pb-2 text-sm font-bold uppercase tracking-wider text-foreground">
            <CalculatorIcon className="h-4 w-4 text-primary" />
            Decision & Calculation Basis (Deterministic Python Rules)
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            {/* 1. Pivot Price & Grounding Basis Card */}
            <div className="flex flex-col rounded-xl border border-border/70 bg-card p-4 shadow-sm">
              <div className="flex items-center justify-between border-b border-border/50 pb-2">
                <span className="flex items-center gap-2 font-bold text-foreground">
                  <CrosshairIcon className="h-4 w-4 text-primary" /> 1. Pivot & Resistance Grounding
                </span>
                <Badge variant={grounding?.is_grounded ? "default" : "secondary"}>
                  {grounding?.is_grounded ? "Grounded" : "Ungrounded"}
                </Badge>
              </div>

              <div className="mt-3 space-y-2 text-[11px]">
                <div className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Decided Pivot:</span>
                  <strong className="text-foreground">₹{Number(proposal.pivot_price).toFixed(2)}</strong>
                </div>

                <div className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Grounded Resistance Zone:</span>
                  <span className="text-foreground font-mono">
                    ₹{calcBasis?.pivot.selected_zone_low ?? grounding?.selected_zone?.low ?? Number(proposal.pivot_price).toFixed(2)} – ₹{calcBasis?.pivot.selected_zone_high ?? grounding?.selected_zone?.high ?? Number(proposal.pivot_price).toFixed(2)}
                  </span>
                </div>

                <div className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-muted-foreground">ATR14 Tolerance (0.5×ATR14):</span>
                  <span className="text-foreground">₹{calcBasis?.pivot.tolerance_atr ?? Number(geometry.anchor_merge_tolerance ?? 0).toFixed(2)}</span>
                </div>

                <div className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Distance to Resistance Boundary:</span>
                  <span className="text-emerald-400">
                    ₹{calcBasis?.pivot.boundary_distance ?? grounding?.boundary_distance ?? "0.00"}
                  </span>
                </div>

                <div className="mt-3 rounded bg-muted/20 p-2.5 text-[10px] leading-relaxed text-muted-foreground border border-border/40">
                  <strong className="text-foreground">Grounding Basis: </strong>
                  {calcBasis?.pivot.basis ?? `Pivot ₹${Number(proposal.pivot_price).toFixed(2)} is verified within 0.5×ATR14 tolerance of overhead resistance in the 126-session detail window.`}
                </div>
              </div>
            </div>

            {/* 2. Structural Stop Loss (SL) Basis Card */}
            <div className="flex flex-col rounded-xl border border-border/70 bg-card p-4 shadow-sm">
              <div className="flex items-center justify-between border-b border-border/50 pb-2">
                <span className="flex items-center gap-2 font-bold text-foreground">
                  <ShieldAlertIcon className="h-4 w-4 text-rose-400" /> 2. Structural Stop Loss (SL)
                </span>
                <Badge variant="outline" className="text-rose-400 border-rose-500/30">
                  {Number(proposal.stop_distance_pct).toFixed(2)}% Risk (≤ 8.0% Max)
                </Badge>
              </div>

              <div className="mt-3 space-y-2 text-[11px]">
                <div className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Decided Stop Loss:</span>
                  <strong className="text-rose-400">₹{Number(proposal.initial_stop).toFixed(2)}</strong>
                </div>

                <div className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Final Contraction Low Anchor:</span>
                  <span className="text-foreground">
                    ₹{calcBasis?.stop_loss.final_contraction_low ?? geometry.final_contraction_low ?? "-"} {calcBasis?.stop_loss.final_contraction_low_date ? `(${calcBasis.stop_loss.final_contraction_low_date})` : ""}
                  </span>
                </div>

                <div className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Frozen ATR14 & Buffer (0.25×ATR14):</span>
                  <span className="text-foreground">
                    ATR14: ₹{Number(geometry.atr14 ?? 0).toFixed(2)} · Buffer: ₹{calcBasis?.stop_loss.stop_buffer_amount ?? (Number(geometry.atr14 ?? 0) * 0.25).toFixed(2)}
                  </span>
                </div>

                <div className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Monetary Distance from Pivot:</span>
                  <span className="text-rose-400">₹{(Number(proposal.pivot_price) - Number(proposal.initial_stop)).toFixed(2)} (1R distance)</span>
                </div>

                <div className="mt-3 rounded bg-muted/20 p-2.5 text-[10px] leading-relaxed text-muted-foreground border border-border/40">
                  <strong className="text-foreground">Stop Loss Basis: </strong>
                  {calcBasis?.stop_loss.basis ?? `Initial stop loss is snapped below final contraction low with a 0.25×ATR14 structural buffer.`}
                </div>
              </div>
            </div>

            {/* 3. Entry Point & Chase Ceiling Basis Card */}
            <div className="flex flex-col rounded-xl border border-border/70 bg-card p-4 shadow-sm">
              <div className="flex items-center justify-between border-b border-border/50 pb-2">
                <span className="flex items-center gap-2 font-bold text-foreground">
                  <TrendingUpIcon className="h-4 w-4 text-amber-400" /> 3. Entry Point & Chase Range
                </span>
                {ceilingTightened ? (
                  <Badge variant="outline" className="text-amber-400 border-amber-500/30">
                    Tightened for 1R Target
                  </Badge>
                ) : (
                  <Badge variant="outline">Standard Ceiling</Badge>
                )}
              </div>

              <div className="mt-3 space-y-2 text-[11px]">
                <div className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Entry Trigger:</span>
                  <strong className="text-foreground">₹{Number(proposal.pivot_price).toFixed(2)} (Pivot Breakout)</strong>
                </div>

                <div className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Final Approved Chase Ceiling:</span>
                  <strong className="text-foreground">₹{lockedCeiling.toFixed(2)}</strong>
                </div>

                <div className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Base Chase Ceiling (min 2%, 0.5×R):</span>
                  <span className="text-foreground">₹{baseCeiling?.toFixed(2) ?? lockedCeiling.toFixed(2)}</span>
                </div>

                <div className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Max Allowable Chase Margin:</span>
                  <span className="text-amber-300">
                    +₹{(lockedCeiling - Number(proposal.pivot_price)).toFixed(2)} (+{(((lockedCeiling - Number(proposal.pivot_price)) / Number(proposal.pivot_price)) * 100).toFixed(2)}%)
                  </span>
                </div>

                <div className="mt-3 rounded bg-muted/20 p-2.5 text-[10px] leading-relaxed text-muted-foreground border border-border/40">
                  <strong className="text-foreground">Chase Ceiling Basis: </strong>
                  {calcBasis?.entry_chase.basis ?? `Chase ceiling is capped to ensure T1 achieves at least 1.00R even if filled at the worst allowable ceiling.`}
                </div>
              </div>
            </div>

            {/* 4. Targets & Risk-Reward Multiples Card */}
            <div className="flex flex-col rounded-xl border border-border/70 bg-card p-4 shadow-sm">
              <div className="flex items-center justify-between border-b border-border/50 pb-2">
                <span className="flex items-center gap-2 font-bold text-foreground">
                  <TargetIcon className="h-4 w-4 text-emerald-400" /> 4. Profit Targets & R:R Ratios
                </span>
                <Badge variant="outline" className="text-emerald-400 border-emerald-500/30">
                  Strictly T1 &lt; T2 &lt; T3
                </Badge>
              </div>

              <div className="mt-3 space-y-2 text-[11px]">
                <div className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Target 1 (Primary Measured Move):</span>
                  <strong className="text-emerald-400">
                    ₹{Number(proposal.t1).toFixed(2)} {t1R ? `(${t1R} at ceiling)` : ""}
                  </strong>
                </div>

                <div className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Target 2 (Secondary Expansion):</span>
                  <span className="text-foreground">
                    ₹{Number(proposal.t2).toFixed(2)} {t2R ? `(${t2R})` : ""}
                  </span>
                </div>

                <div className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Target 3 (Major Swing / Runner):</span>
                  <span className="text-foreground">
                    ₹{Number(proposal.t3).toFixed(2)} {t3R ? `(${t3R})` : ""}
                  </span>
                </div>

                <div className="flex justify-between py-1 border-b border-border/30">
                  <span className="text-muted-foreground">Worst-Case Entry Risk Distance:</span>
                  <span className="text-foreground font-mono">
                    ₹{(lockedCeiling - Number(proposal.initial_stop)).toFixed(2)} (at ceiling) vs ₹{(Number(proposal.pivot_price) - Number(proposal.initial_stop)).toFixed(2)} (at pivot)
                  </span>
                </div>

                <div className="mt-3 rounded bg-muted/20 p-2.5 text-[10px] leading-relaxed text-muted-foreground border border-border/40">
                  <strong className="text-foreground">Target Basis: </strong>
                  {calcBasis?.targets.basis ?? `Gemini structural upside objectives validated with T1 ≥ 1R at maximum chase ceiling.`}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Multi-Leg Schedule & Sizing */}
        <div className="rounded-xl border border-border/70 bg-card p-4 shadow-sm">
          <div className="flex items-center justify-between border-b border-border/50 pb-2">
            <span className="flex items-center gap-2 font-bold text-foreground text-sm uppercase tracking-wider">
              <LayersIcon className="h-4 w-4 text-primary" /> Multi-Leg Execution Plan & Sizing
            </span>
            <div className="flex items-center gap-2 text-[11px]">
              <span className="text-muted-foreground">Max Approved Risk:</span>
              <strong className="text-foreground">₹{Number(proposal.approved_risk_budget_amount ?? 0).toFixed(2)}</strong>
              <span className="text-muted-foreground">({Number(proposal.risk_budget_pct).toFixed(1)}% Capital)</span>
            </div>
          </div>

          <div className="mt-3 grid gap-3 md:grid-cols-3">
            {(proposal.legs ?? []).map((leg, idx) => {
              const legNum = idx + 1
              return (
                <div
                  key={legNum}
                  className="flex flex-col justify-between rounded-lg border border-border/50 bg-muted/15 p-3"
                >
                  <div>
                    <div className="flex items-center justify-between">
                      <strong className="text-foreground">Leg {legNum}</strong>
                      <Badge variant="outline" className="text-[10px]">
                        {(Number(leg.risk_allocation_pct) * 100).toFixed(0)}% Risk Share
                      </Badge>
                    </div>
                    <div className="mt-2 space-y-1 text-[11px] text-muted-foreground">
                      <div>Trigger: <strong className="text-foreground">{leg.trigger_type.toUpperCase()}</strong> @ ₹{Number(proposal.pivot_price).toFixed(2)}</div>
                      <div>Volume Gate: <strong className="text-foreground">RVOL ≥ {proposal.relative_volume_threshold}×</strong></div>
                      <div>Hold / Base Gates: {leg.hold_required} bars hold · {leg.base_required} bars base</div>
                    </div>
                  </div>
                  <div className="mt-3 flex items-center justify-between border-t border-border/30 pt-2 text-[10px]">
                    <span className="text-muted-foreground">Status:</span>
                    <Badge variant={leg.status === "filled" ? "default" : leg.status === "armed" ? "secondary" : "outline"}>
                      {leg.status.toUpperCase()}
                    </Badge>
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        {/* Gemini Vision AI Evidence & Geometry Anchors */}
        <div className="rounded-xl border border-border/70 bg-card p-4 shadow-sm">
          <div className="flex items-center justify-between border-b border-border/50 pb-2">
            <span className="flex items-center gap-2 font-bold text-foreground text-sm uppercase tracking-wider">
              <TargetIcon className="h-4 w-4 text-primary" /> Gemini VCP Assessment & Contraction Evidence
            </span>
            <div className="flex items-center gap-2 text-[10px]">
              <span className="text-muted-foreground">Model:</span>
              <strong className="text-foreground">{proposal.model}</strong>
            </div>
          </div>

          <div className="mt-3 grid gap-4 lg:grid-cols-3">
            <div className="lg:col-span-2 space-y-3">
              <div className="flex flex-wrap gap-2 text-[11px]">
                <div className="rounded-md border border-border/40 bg-muted/20 px-3 py-1.5">
                  <span className="text-muted-foreground">Base Tightness: </span>
                  <strong className="text-foreground capitalize">{proposal.gemini_evidence?.base_tightness ?? "Solid"}</strong>
                </div>
                <div className="rounded-md border border-border/40 bg-muted/20 px-3 py-1.5">
                  <span className="text-muted-foreground">Volume Dry-Up: </span>
                  <strong className="text-foreground capitalize">{proposal.gemini_evidence?.dry_up_quality ?? "Drying Up"}</strong>
                </div>
                <div className="rounded-md border border-border/40 bg-muted/20 px-3 py-1.5">
                  <span className="text-muted-foreground">Overhead Resistance Room: </span>
                  <strong className="text-foreground capitalize">{proposal.gemini_evidence?.resistance_room ?? "Clear"}</strong>
                </div>
              </div>

              <div className="rounded-lg border border-border/40 bg-background/50 p-3 text-[11px] leading-relaxed text-muted-foreground">
                <div className="mb-1 text-[10px] uppercase font-semibold text-foreground">AI Evidence Summary</div>
                {proposal.gemini_evidence?.evidence_summary || "No structured summary provided."}
              </div>
            </div>

            {/* Contraction Anchors Table */}
            <div className="rounded-lg border border-border/40 bg-background/50 p-3">
              <div className="mb-2 text-[10px] uppercase font-semibold text-foreground">Validated Pattern Anchors</div>
              <div className="max-h-40 overflow-y-auto">
                <table className="w-full text-left text-[10px]">
                  <thead>
                    <tr className="border-b border-border/40 text-muted-foreground">
                      <th className="py-1">Date</th>
                      <th className="py-1">Type</th>
                      <th className="py-1 text-right">Price</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/20">
                    {(proposal.gemini_evidence?.contraction_anchors ?? []).map((anchor, idx) => (
                      <tr key={idx}>
                        <td className="py-1 font-mono text-muted-foreground">{anchor.date}</td>
                        <td className="py-1 text-foreground capitalize">
                          {anchor.anchor_type?.replace("_", " ") ?? "Anchor"}
                        </td>
                        <td className="py-1 text-right font-mono font-semibold text-foreground">
                          ₹{Number(anchor.price ?? 0).toFixed(2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>

        {/* Audit & Hash Metadata Footer */}
        <div className="rounded-lg border border-border/40 bg-muted/10 p-3 text-[10px] text-muted-foreground">
          <div className="flex flex-wrap items-center justify-between gap-2 font-mono">
            <div>Proposal Hash: <span className="text-foreground">{proposal.proposal_hash}</span></div>
            <div>Source Hash: <span className="text-foreground">{proposal.source_hash.slice(0, 16)}…</span></div>
            <div>Generated: {new Date(proposal.generated_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST</div>
          </div>
        </div>
      </div>

      {/* Lightbox Image Modal */}
      {imageModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm"
          onClick={() => setImageModal(null)}
        >
          <div className="relative max-h-[95vh] max-w-[95vw] overflow-hidden rounded-xl border border-border/80 bg-card p-2 shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 flex items-center justify-between px-2">
              <span className="text-xs font-semibold text-foreground">Proposal Chart Preview</span>
              <Button size="xs" variant="ghost" onClick={() => setImageModal(null)}>Close ✕</Button>
            </div>
            <img src={imageModal} alt="Expanded proposal chart" className="max-h-[85vh] w-auto rounded-lg object-contain bg-black" />
          </div>
        </div>
      )}
    </div>
  )
}
