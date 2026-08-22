import { useState } from "react"
import {
  CheckCircle2Icon,
  ClockIcon,
  LayersIcon,
  LockIcon,
  TargetIcon,
  XCircleIcon,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { useRecordProposalDecision, useTradeProposal, useP10Rollout, type TradeProposalItem } from "./api"

interface ProposalDetailModalProps {
  proposal: TradeProposalItem | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

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

export function ProposalDetailModal({ proposal, open, onOpenChange }: ProposalDetailModalProps) {
  const [notes] = useState("")
  const recordDecision = useRecordProposalDecision()
  const { data: detail } = useTradeProposal(open ? proposal?.id ?? null : null)
  const rollout = useP10Rollout()
  const approvalsAllowed = rollout.data?.approvals_allowed === true

  if (!proposal) return null
  const activeProposal = detail ?? proposal
  const geometry = activeProposal.geometry ?? {}
  const t1R = formatR(geometry.t1_r)
  const t2R = formatR(geometry.t2_r)
  const t3R = formatR(geometry.t3_r)
  const baseCeiling = asFiniteNumber(geometry.base_chase_ceiling)
  const lockedCeiling = Number(activeProposal.chase_ceiling)
  const ceilingTightened =
    baseCeiling != null && Number.isFinite(lockedCeiling) && baseCeiling > lockedCeiling

  const isPending = activeProposal.status === "pending_approval"
  const isApproved = activeProposal.status === "approved"
  const isRejected = activeProposal.status === "rejected"
  const isExpired = activeProposal.status === "expired_unapproved"

  const handleDecision = async (decision: "approved" | "rejected") => {
    try {
      await recordDecision.mutateAsync({
        id: activeProposal.id,
        payload: {
          decision,
          expected_proposal_hash: activeProposal.proposal_hash,
          notes: notes.trim() || undefined,
        },
      })
      onOpenChange(false)
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

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full sm:max-w-2xl overflow-y-auto border-l border-border bg-card font-mono text-xs">
        <SheetHeader className="border-b border-border/60 pb-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <SheetTitle className="font-mono text-lg font-bold text-foreground">
                {activeProposal.symbol}
              </SheetTitle>
              <Badge variant="outline" className={templateColors[activeProposal.entry_template] || ""}>
                {activeProposal.entry_template.toUpperCase()}
              </Badge>
            </div>
            <Badge
              variant={isApproved ? "default" : isPending ? "secondary" : "destructive"}
              className="uppercase tracking-wider"
            >
              {activeProposal.status.replaceAll("_", " ")}
            </Badge>
          </div>
          <SheetDescription className="text-muted-foreground text-[11px]">
            Target Session: {activeProposal.entry_session_date} | Approval Deadline:{" "}
            {new Date(activeProposal.approval_deadline).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit" })} IST
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-5 py-4">
          {/* Key Price Levels */}
          <div className="grid grid-cols-3 gap-2">
            <div className="rounded-lg border border-border/60 bg-muted/20 p-2.5">
              <div className="text-[10px] text-muted-foreground uppercase">Pivot Entry</div>
              <div className="text-sm font-bold text-foreground">₹{Number(activeProposal.pivot_price).toFixed(2)}</div>
              <div className="text-[9px] text-muted-foreground">Ceiling: ₹{lockedCeiling.toFixed(2)}</div>
              {ceilingTightened && baseCeiling != null && (
                <div className="text-[9px] text-amber-400">
                  Tightened from ₹{baseCeiling.toFixed(2)} to keep T1 ≥ 1R
                </div>
              )}
            </div>
            <div className="rounded-lg border border-border/60 bg-muted/20 p-2.5">
              <div className="text-[10px] text-muted-foreground uppercase">Structural Stop</div>
              <div className="text-sm font-bold text-rose-400">₹{Number(activeProposal.initial_stop).toFixed(2)}</div>
              <div className="text-[9px] text-rose-400/80">-{Number(activeProposal.stop_distance_pct).toFixed(2)}% Risk</div>
            </div>
            <div className="rounded-lg border border-border/60 bg-muted/20 p-2.5">
              <div className="text-[10px] text-muted-foreground uppercase">Target 1</div>
              <div className="text-sm font-bold text-emerald-400">₹{Number(activeProposal.t1).toFixed(2)}</div>
              <div className="text-[9px] text-muted-foreground">{t1R ? `${t1R} at ceiling` : "Structural objective"}</div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div className="rounded-lg border border-border/60 bg-muted/20 p-2.5">
              <div className="text-[10px] text-muted-foreground uppercase">Target 2</div>
              <div className="text-sm font-bold text-foreground">₹{Number(activeProposal.t2).toFixed(2)}</div>
              <div className="text-[9px] text-muted-foreground">{t2R ?? "Structural objective"}</div>
              {geometry.t2_below_2r && (
                <div className="text-[9px] text-amber-400">Below 2R — audit flag, not a reject</div>
              )}
            </div>
            <div className="rounded-lg border border-border/60 bg-muted/20 p-2.5">
              <div className="text-[10px] text-muted-foreground uppercase">Target 3</div>
              <div className="text-sm font-bold text-foreground">₹{Number(activeProposal.t3).toFixed(2)}</div>
              <div className="text-[9px] text-muted-foreground">{t3R ?? "Structural objective"}</div>
              {geometry.t3_below_3r && (
                <div className="text-[9px] text-amber-400">Below 3R — audit flag, not a reject</div>
              )}
            </div>
          </div>

          <div className="grid gap-3">
            <div>
              <div className="mb-1 text-[10px] uppercase text-muted-foreground">Frozen 252-session context</div>
              <img className="w-full rounded-lg border border-border/60 bg-black" src={`/api/v1/automation/proposals/${activeProposal.id}/charts/context`} alt={`${activeProposal.symbol} frozen context chart`} />
            </div>
            <div>
              <div className="mb-1 text-[10px] uppercase text-muted-foreground">Deterministically annotated 126-session detail</div>
              <img className="w-full rounded-lg border border-border/60 bg-black" src={`/api/v1/automation/proposals/${activeProposal.id}/charts/detail`} alt={`${activeProposal.symbol} annotated detail chart`} />
            </div>
          </div>

          {/* AI Evidence & Quality */}
          <div className="rounded-lg border border-border/60 bg-muted/10 p-3 flex flex-col gap-2">
            <div className="flex items-center justify-between border-b border-border/40 pb-1.5">
              <span className="font-semibold text-foreground flex items-center gap-1.5">
                <TargetIcon className="h-3.5 w-3.5 text-primary" /> Gemini VCP Assessment
              </span>
              <div className="flex gap-1.5 text-[10px]">
                <span className="text-muted-foreground">Prior trend: <strong className="text-foreground">{activeProposal.gemini_evidence?.prior_uptrend ?? activeProposal.gemini_evidence?.base_tightness ?? "—"}</strong></span>
                <span>•</span>
                <span className="text-muted-foreground">Volume dry-up: <strong className="text-foreground">{activeProposal.gemini_evidence?.volume_dry_up ?? activeProposal.gemini_evidence?.dry_up_quality ?? "—"}</strong></span>
              </div>
            </div>
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              {activeProposal.gemini_evidence?.evidence_summary || "No summary provided."}
            </p>
          </div>

          {/* Multi-Leg Schedule */}
          <div className="rounded-lg border border-border/60 bg-muted/10 p-3 flex flex-col gap-2">
            <div className="font-semibold text-foreground flex items-center gap-1.5">
              <LayersIcon className="h-3.5 w-3.5 text-primary" /> Multi-Leg Execution Plan
            </div>
            <div className="space-y-1.5">
              {(activeProposal.legs ?? []).map((leg, idx) => {
                const legNum = idx + 1
                return (
                  <div
                    key={legNum}
                    className="flex items-center justify-between rounded bg-background/50 px-2.5 py-1.5 border border-border/40 text-[11px]"
                  >
                    <span className="font-medium text-foreground">
                      Leg {legNum} ({Number(leg.risk_allocation_pct) * 100}% Risk Budget)
                    </span>
                    <div className="flex items-center gap-3 text-muted-foreground">
                      <span>RVOL ≥ {activeProposal.relative_volume_threshold}x</span>
                      <span className="text-foreground/80">
                        {legNum === 1 ? "D1 Pivot Trigger" : "Hold/Base Add Gate"} · {leg.status}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {/* Immutable Decision Checkpoint */}
          {isPending && (
            <div className="rounded-lg border border-primary/20 bg-primary/5 p-3 flex flex-col gap-3">
              <div className="flex items-center gap-2 text-foreground font-semibold">
                <LockIcon className="h-4 w-4 text-primary" /> Immutable Approval Checkpoint
              </div>
              <p className="text-[11px] text-muted-foreground">
                Approval arms Leg 1 for the <strong>{activeProposal.entry_session_date}</strong> trading session only.
                It accepts a maximum risk budget of ₹{Number(activeProposal.approved_risk_budget_amount ?? 0).toFixed(2)}, never a quantity. Quantity is calculated from fresh broker state under the allocation lock.
              </p>
              <div className="flex gap-2 pt-1">
                <Button
                  variant="destructive"
                  size="sm"
                  className="flex-1 font-mono"
                  disabled={recordDecision.isPending}
                  onClick={() => handleDecision("rejected")}
                >
                  <XCircleIcon className="mr-1.5 h-3.5 w-3.5" /> Reject
                </Button>
                <Button
                  variant="default"
                  size="sm"
                  className="flex-1 font-mono bg-emerald-600 hover:bg-emerald-500 text-white"
                  disabled={recordDecision.isPending || !approvalsAllowed}
                  onClick={() => handleDecision("approved")}
                >
                  <CheckCircle2Icon className="mr-1.5 h-3.5 w-3.5" /> Approve & Arm Leg 1
                </Button>
              </div>
              {!approvalsAllowed && (
                <p className="text-[10px] text-amber-400">
                  Shadow stage: review or reject only. Promote to Paper before arming entries.
                </p>
              )}
            </div>
          )}

          {isApproved && (
            <div className="flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-emerald-400">
              <CheckCircle2Icon className="h-4 w-4 shrink-0" />
              <span>Approved by operator. Entry supervisor is monitoring 5m bars for confirmation.</span>
            </div>
          )}

          {isExpired && (
            <div className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-amber-400">
              <ClockIcon className="h-4 w-4 shrink-0" />
              <span>Proposal expired unapproved at 09:00 IST pre-market cutoff.</span>
            </div>
          )}

          {isRejected && (
            <div className="flex items-center gap-2 rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-rose-400">
              <XCircleIcon className="h-4 w-4 shrink-0" />
              <span>Proposal was rejected by operator.</span>
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
