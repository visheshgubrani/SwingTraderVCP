import { useState, type ReactNode } from "react"
import { Link, useNavigate, useParams } from "react-router"
import {
  AlertTriangleIcon,
  ArrowLeftIcon,
  CheckCircle2Icon,
  ClockIcon,
  LineChartIcon,
  ShieldAlertIcon,
  XCircleIcon,
} from "lucide-react"

import { EmptyState, Panel, SectionLabel } from "@/components/terminal/panel"
import { useToast } from "@/components/terminal/toast"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { useTradingAppContext } from "@/features/dashboard/app-context"
import { API_BASE_URL } from "@/lib/api"
import { fmtDate, fmtDateTime, fmtMoney, fmtNum, fmtR, fmtRelative, toNum } from "@/lib/format"
import { shortSymbol } from "@/lib/marketSymbols"
import { cn } from "@/lib/utils"
import {
  useEntrySupervisorStatus,
  useP10Rollout,
  useRecordProposalDecision,
  useTradeProposal,
  type TradeProposalItem,
} from "./api"
import {
  EntryStateChip,
  KV,
  LevelTile,
  PositionStateChip,
  ProposalCharts,
  ProposalStatusChip,
  TemplateBadge,
} from "./proposalUi"

const LEG_STATUS_LABEL: Record<string, string> = {
  planned: "Planned",
  armed: "Armed",
  trigger_observed: "Trigger observed",
  waiting_for_reset: "Waiting for reset",
  intent_created: "Intent created",
  submitted: "Submitted",
  partially_filled: "Partially filled",
  filled: "Filled",
  expired: "Entry expired",
  cancelled: "Cancelled",
  submission_unknown: "Submission unknown",
}

function price(value: unknown): string {
  const n = toNum(value as number | string | null | undefined)
  return n === null ? "—" : fmtNum(n)
}

function text(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—"
  return String(value).replaceAll("_", " ")
}

/** Why a decision cannot be recorded right now (server enforces the same rules). */
function decisionBlockers(proposal: TradeProposalItem, approvalsAllowed: boolean, stage: string | undefined) {
  const deadlinePassed = new Date(proposal.approval_deadline).getTime() <= Date.now()
  if (deadlinePassed) return { both: "The 09:00 IST approval window has closed.", approve: null }
  if (!proposal.live_eligible)
    return { both: "Review-only proposal: it did not finish before the live cutoff, so it cannot be approved or rejected.", approve: null }
  if (!approvalsAllowed)
    return { both: null, approve: `Approvals are blocked in the ${stage ?? "current"} rollout stage. You can still reject.` }
  return { both: null, approve: null }
}

function DecisionBar({ proposal, onDecided }: { proposal: TradeProposalItem; onDecided?: () => void }) {
  const { toast } = useToast()
  const rollout = useP10Rollout()
  const decide = useRecordProposalDecision()
  const [notes, setNotes] = useState("")
  const [pending, setPending] = useState<"approved" | "rejected" | null>(null)
  const blockers = decisionBlockers(proposal, rollout.data?.approvals_allowed === true, rollout.data?.stage)

  const submit = (decision: "approved" | "rejected") => {
    setPending(decision)
    decide.mutate(
      {
        id: proposal.id,
        payload: { decision, expected_proposal_hash: proposal.proposal_hash, notes: notes.trim() || undefined },
      },
      {
        onSuccess: () => {
          toast(decision === "approved" ? "ok" : "info", {
            title: `${shortSymbol(proposal.symbol)} ${decision === "approved" ? "approved — leg 1 armed for the session" : "rejected"}`,
          })
          setNotes("")
          onDecided?.()
        },
        onSettled: () => setPending(null),
      },
    )
  }

  return (
    <Panel
      className="border-accent/40"
      description="Pivot, stop, targets, template and risk budget are fixed by Python. Approving accepts this exact version."
      title="Your decision"
    >
      <div className="flex flex-col gap-3">
        <Textarea
          aria-label="Decision notes"
          className="min-h-14 text-xs"
          disabled={Boolean(blockers.both) || decide.isPending}
          onChange={(event) => setNotes(event.target.value)}
          placeholder="Notes (optional) — saved with the decision"
          value={notes}
        />
        {(blockers.both || blockers.approve) && (
          <p className="flex items-start gap-1.5 text-xs text-wa">
            <AlertTriangleIcon className="mt-0.5 size-3.5 flex-none" />
            {blockers.both ?? blockers.approve}
          </p>
        )}
        {decide.error instanceof Error && (
          <p className="rounded-md border border-ko/40 bg-ko-soft px-3 py-2 text-xs text-ko" role="alert">
            {decide.error.message}
          </p>
        )}
        <div className="flex flex-wrap items-center justify-end gap-2">
          <span className="mr-auto text-xs text-muted-text">
            Deadline {fmtDateTime(proposal.approval_deadline)} IST · {fmtRelative(proposal.approval_deadline)}
          </span>
          <Button
            disabled={Boolean(blockers.both) || decide.isPending}
            onClick={() => submit("rejected")}
            size="sm"
            type="button"
            variant="destructive"
          >
            {pending === "rejected" ? <Spinner data-icon="inline-start" /> : <XCircleIcon data-icon="inline-start" />}
            Reject
          </Button>
          <Button
            className="bg-ok text-background hover:bg-ok/90"
            disabled={Boolean(blockers.both) || Boolean(blockers.approve) || decide.isPending}
            onClick={() => submit("approved")}
            size="sm"
            type="button"
          >
            {pending === "approved" ? <Spinner data-icon="inline-start" /> : <CheckCircle2Icon data-icon="inline-start" />}
            Approve &amp; arm leg 1
          </Button>
        </div>
      </div>
    </Panel>
  )
}

function OutcomeBanner({ proposal }: { proposal: TradeProposalItem }) {
  const supervisor = useEntrySupervisorStatus()
  if (proposal.status === "rejected") {
    return (
      <Banner icon={XCircleIcon} tone="bad">
        Rejected {proposal.decided_at ? `on ${fmtDateTime(proposal.decided_at)} IST` : ""}
        {proposal.decision_notes ? ` — “${proposal.decision_notes}”` : ""}
      </Banner>
    )
  }
  if (proposal.status === "expired_unapproved") {
    return (
      <Banner icon={ClockIcon} tone="warn">
        Expired unapproved at the {fmtDateTime(proposal.approval_deadline)} IST deadline. A fresh scan and proposal are needed.
      </Banner>
    )
  }
  if (proposal.status !== "approved") return null

  const who = proposal.decision_source === "paper_auto" ? "Auto-approved (paper testing)" : "Approved by you"
  const when = proposal.decided_at ? ` on ${fmtDateTime(proposal.decided_at)} IST` : ""
  const inPlay = proposal.entry_state && proposal.entry_state !== "expired" && proposal.entry_state !== "filled"
  const monitoringReady = supervisor.data?.status === "active" && supervisor.data.market_data.ready
  return (
    <>
      <Banner icon={CheckCircle2Icon} tone="ok">
        {who}
        {when}
        {proposal.decision_notes && proposal.decision_source === "operator" ? ` — “${proposal.decision_notes}”` : ""}
      </Banner>
      {inPlay && supervisor.data && !monitoringReady && (
        <Banner icon={ShieldAlertIcon} tone="bad">
          Entry monitoring is down:{" "}
          {supervisor.data.status !== "active"
            ? "the entry supervisor is inactive"
            : `market data is ${supervisor.data.market_data.status}`}
          . No entry can trigger until it recovers and a fresh confirmation bar completes.
        </Banner>
      )}
    </>
  )
}

function Banner({
  icon: Icon,
  tone,
  children,
}: {
  icon: typeof CheckCircle2Icon
  tone: "ok" | "warn" | "bad"
  children: ReactNode
}) {
  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-md border px-3 py-2 text-xs",
        tone === "ok" && "border-ok/30 bg-ok-soft text-ok",
        tone === "warn" && "border-wa/30 bg-wa-soft text-wa",
        tone === "bad" && "border-ko/30 bg-ko-soft text-ko",
      )}
    >
      <Icon className="mt-0.5 size-3.5 flex-none" />
      <span>{children}</span>
    </div>
  )
}

function KeyLevels({ proposal }: { proposal: TradeProposalItem }) {
  const geometry = proposal.geometry ?? {}
  const slots = geometry.target_slots ?? {}
  const targetSub = (slot: string | undefined, r: string | undefined) => [slot?.replaceAll("_", " "), r ? fmtR(r) : null].filter(Boolean).join(" · ")
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
      <LevelTile label="Pivot" sub={`Planned entry ${price(geometry.planned_entry ?? proposal.pivot_price)}`} value={price(proposal.pivot_price)} />
      <LevelTile label="Chase ceiling" sub="Max fill price" value={price(proposal.chase_ceiling)} />
      <LevelTile
        flag={geometry.wide_risk_flag ? "Wide stop (>5%) — flagged, not rejected" : undefined}
        label="Stop"
        sub={`${fmtNum(toNum(proposal.stop_distance_pct))}% below pivot`}
        tone="bad"
        value={price(proposal.initial_stop)}
      />
      <LevelTile label="T1" sub={targetSub(slots.t1, geometry.t1_r)} tone="ok" value={price(proposal.t1)} />
      <LevelTile
        flag={geometry.t2_below_2r ? "Below 2R — audit flag" : undefined}
        label="T2"
        sub={targetSub(slots.t2, geometry.t2_r)}
        value={price(proposal.t2)}
      />
      <LevelTile
        flag={geometry.t3_below_3r ? "Below 3R — audit flag" : undefined}
        label="T3"
        sub={targetSub(slots.t3, geometry.t3_r)}
        value={price(proposal.t3)}
      />
    </div>
  )
}

function LegsPanel({ proposal }: { proposal: TradeProposalItem }) {
  const legs = proposal.legs ?? []
  const events = proposal.trigger_events ?? []
  return (
    <Panel
      actions={
        <span className="font-mono text-xs text-muted-text">
          Max risk <b className="text-fg2">{fmtMoney(proposal.approved_risk_budget_amount)}</b> ·{" "}
          {fmtNum(toNum(proposal.risk_budget_pct), 1)}% of capital
        </span>
      }
      flush
      title="Entry legs"
    >
      {legs.length === 0 ? (
        <p className="px-4 py-3 text-xs text-muted-text">Legs are created when the proposal is persisted.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="tbl">
            <thead>
              <tr>
                <th className="l">LEG</th>
                <th>RISK SHARE</th>
                <th>TRIGGER</th>
                <th>RVOL ≥</th>
                <th className="l">GATES</th>
                <th className="l">STATUS</th>
                <th>FILLED</th>
                <th className="l">LATEST BAR</th>
              </tr>
            </thead>
            <tbody>
              {legs.map((leg) => {
                const legEvents = events.filter((event) => event.leg_id === leg.id)
                const latest = legEvents[legEvents.length - 1]
                const signal = [...legEvents].reverse().find((event) => event.bar_type === "signal_bar")
                const status = leg.derived_status ?? leg.status
                return (
                  <tr key={leg.id}>
                    <td className="l font-semibold text-foreground">L{leg.leg_index}</td>
                    <td>{fmtNum(toNum(leg.risk_allocation_pct) !== null ? Number(leg.risk_allocation_pct) * 100 : null, 0)}%</td>
                    <td>{leg.trigger_price === null ? <span className="text-muted-text">after base</span> : price(leg.trigger_price)}</td>
                    <td>{fmtNum(toNum(leg.relative_volume_threshold))}×</td>
                    <td className="l text-2xs">
                      {leg.hold_required || leg.base_required
                        ? `Hold ${leg.hold_count}/${leg.hold_required} · Base ${leg.base_count}/${leg.base_required}`
                        : "Breakout"}
                    </td>
                    <td className="l">
                      <span className={cn(status === "filled" ? "text-ok" : status === "expired" || status === "cancelled" ? "text-muted-text" : "text-fg2")}>
                        {LEG_STATUS_LABEL[status] ?? status}
                      </span>
                    </td>
                    <td>{leg.filled_shares > 0 ? `${leg.filled_shares} @ ${price(leg.filled_avg_price)}` : "—"}</td>
                    <td className="l text-2xs">
                      {latest ? (
                        <span title={latest.entry_rejection_reason ?? undefined}>
                          {fmtDateTime(latest.bar_timestamp)} · {text(latest.trigger_outcome ?? latest.entry_eligibility_outcome)}
                          {signal?.bar_relative_volume != null && ` · RVOL ${fmtNum(toNum(signal.bar_relative_volume))}×`}
                          {latest.entry_rejection_reason && <span className="block text-wa">{latest.entry_rejection_reason}</span>}
                        </span>
                      ) : (
                        <span className="text-muted-text">No bars yet</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

function EvidencePanel({ proposal }: { proposal: TradeProposalItem }) {
  const evidence = proposal.gemini_evidence ?? { evidence_summary: "" }
  const geometry = proposal.geometry ?? {}
  const chips: [string, unknown][] = [
    ["Classification", evidence.classification],
    ["Stage 2", evidence.base_quality?.stage2_context ?? evidence.prior_uptrend],
    ["Tightening", evidence.progressive_tightening],
    ["Volume dry-up", evidence.volume_dry_up ?? evidence.dry_up_quality],
    ["Price action", evidence.base_quality?.price_action],
    ["Confidence (display only)", fmtNum(toNum(evidence.confidence ?? proposal.confidence), 0)],
    ["Contractions", `Python ${evidence.python_count ?? "—"} · Gemini ${evidence.llm_count ?? "—"}`],
  ]
  const survivors = geometry.survivors ?? []
  return (
    <Panel actions={<span className="font-mono text-2xs text-muted-text">{proposal.model}</span>} title="Gemini audit & contractions">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-1.5">
          {chips.map(([label, value]) => (
            <span className="rounded border border-border-soft bg-field px-2 py-0.5 text-2xs" key={label}>
              <span className="text-muted-text">{label}: </span>
              <b className="font-semibold text-fg2 capitalize">{text(value)}</b>
            </span>
          ))}
          {(geometry.fifty_two_week_tags ?? []).map((tag) => (
            <span className="rounded border border-accent/30 bg-accent-soft px-2 py-0.5 text-2xs text-accent" key={tag}>
              {text(tag)}
            </span>
          ))}
        </div>
        <p className="text-xs leading-relaxed text-fg2">{evidence.evidence_summary || "No evidence summary recorded."}</p>
        {(evidence.red_flags?.length ?? 0) > 0 && (
          <div className="rounded-md border border-wa/30 bg-wa-soft px-3 py-2 text-xs text-wa">
            <SectionLabel className="text-wa">Red flags</SectionLabel>
            <ul className="mt-1 list-disc space-y-0.5 pl-4">
              {evidence.red_flags!.map((flag) => (
                <li key={flag}>{flag}</li>
              ))}
            </ul>
          </div>
        )}
        {survivors.length > 0 && (
          <div>
            <SectionLabel>Surviving contractions (Python-snapped)</SectionLabel>
            <table className="tbl mt-1">
              <thead>
                <tr>
                  <th className="l">#</th>
                  <th className="l">SOURCE</th>
                  <th>DEPTH</th>
                  <th>HIGH</th>
                  <th>LOW</th>
                </tr>
              </thead>
              <tbody>
                {survivors.map((row, index) => (
                  <tr key={String(row.index ?? index)}>
                    <td className="l">T{String(row.index ?? index + 1)}</td>
                    <td className="l">{text(row.source)}</td>
                    <td>{fmtNum(toNum(row.depth_pct as number | null), 1)}%</td>
                    <td>{price(row.high_price)}</td>
                    <td>{price(row.low_price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {(evidence.candidate_assessments?.length ?? 0) > 0 && (
          <div>
            <SectionLabel>Gemini actions on Python candidates</SectionLabel>
            <ul className="mt-1 space-y-0.5 text-xs text-fg2">
              {evidence.candidate_assessments!.map((row) => (
                <li key={row.index}>
                  <span className="font-mono text-muted-text">#{row.index}</span> {row.action}
                  {row.merge_with_index != null && ` → #${row.merge_with_index}`}
                  {row.note && <span className="text-muted-text"> — {row.note}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Panel>
  )
}

function BasisPanel({ proposal }: { proposal: TradeProposalItem }) {
  const geometry = proposal.geometry ?? {}
  const basis = (geometry.calculation_basis ?? {}) as Record<string, Record<string, unknown> | undefined>
  return (
    <details className="group rounded-md border border-border bg-surface">
      <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-2.5 text-xs font-semibold tracking-[0.08em] text-fg2 uppercase">
        How the levels were calculated
        <span className="font-normal tracking-normal text-muted-text normal-case group-open:hidden">Show</span>
        <span className="hidden font-normal tracking-normal text-muted-text normal-case group-open:inline">Hide</span>
      </summary>
      <div className="grid gap-x-6 border-t border-border-soft px-4 py-3 md:grid-cols-2">
        <div>
          <SectionLabel>Pivot & entry</SectionLabel>
          <KV label="Pivot (final survivor high)">{price(proposal.pivot_price)}</KV>
          <KV label="Final survivor high date">{text(basis.pivot?.final_contraction_high_date)}</KV>
          <KV label="Planned entry (pivot + 0.10×ATR14)">{price(geometry.planned_entry)}</KV>
          <KV label="ATR14 (frozen)">{price(geometry.atr14)}</KV>
          <KV label="Base chase ceiling">{price(geometry.base_chase_ceiling)}</KV>
          <KV label="Approved chase ceiling">{price(proposal.chase_ceiling)}</KV>
          <p className="mt-2 text-2xs leading-relaxed text-muted-text">{text(basis.pivot?.basis ?? basis.entry_chase?.formula)}</p>
        </div>
        <div>
          <SectionLabel>Stop & targets</SectionLabel>
          <KV label="Stop (final low − 0.25×ATR14)">{price(proposal.initial_stop)}</KV>
          <KV label="Final contraction low">{price(basis.stop_loss?.final_contraction_low ?? geometry.final_contraction_low)}</KV>
          <KV label="1R at planned entry">{price(geometry.r_distance)}</KV>
          <KV label="1R at chase ceiling">{price(geometry.worst_entry_r_distance)}</KV>
          <KV label="Template">{text(proposal.entry_template)} · {text(geometry.template_policy_version)}</KV>
          <KV label="Breakout RVOL threshold">{fmtNum(toNum(proposal.relative_volume_threshold))}×</KV>
          <p className="mt-2 text-2xs leading-relaxed text-muted-text">
            {text(geometry.template_reason ?? basis.stop_loss?.formula)}
          </p>
        </div>
      </div>
    </details>
  )
}

function AuditFooter({ proposal, onOpenRun }: { proposal: TradeProposalItem; onOpenRun?: (runId: string) => void }) {
  const rows: [string, string][] = [
    ["Proposal hash", proposal.proposal_hash],
    ["Source hash", proposal.source_hash],
    ["Geometry", proposal.geometry_version],
    ["Prompt / schema", `${proposal.prompt_version} / ${proposal.schema_version}`],
    ["Renderer", proposal.renderer_version],
    ["Risk policy", `v${proposal.risk_policy_version}`],
    ["Trigger policy", proposal.entry_trigger_policy_version],
    ["Generated", `${fmtDateTime(proposal.generated_at)} IST`],
  ]
  return (
    <div className="rounded-md border border-border-soft bg-field px-4 py-3">
      <div className="grid gap-x-6 gap-y-1 font-mono text-2xs text-muted-text md:grid-cols-2">
        {rows.map(([label, value]) => (
          <div className="flex min-w-0 gap-2" key={label}>
            <span className="flex-none">{label}</span>
            <span className="truncate text-fg2" title={value}>
              {value}
            </span>
          </div>
        ))}
      </div>
      {proposal.automation_run_id && onOpenRun && (
        <button className="link-act mt-2 !px-0" onClick={() => onOpenRun(proposal.automation_run_id!)} type="button">
          Open the generation run →
        </button>
      )}
    </div>
  )
}

/** Full proposal detail: decision, levels, legs, charts, evidence, basis, audit. */
export function ProposalDetail({
  proposalId,
  onDecided,
  onOpenRun,
}: {
  proposalId: string
  onDecided?: () => void
  onOpenRun?: (runId: string) => void
}) {
  const { openChart } = useTradingAppContext()
  const { data: proposal, isLoading, error } = useTradeProposal(proposalId)

  if (isLoading && !proposal) {
    return (
      <div className="flex h-40 items-center justify-center gap-2 text-sm text-muted-text">
        <Spinner /> Loading proposal…
      </div>
    )
  }
  if (error || !proposal) {
    return (
      <EmptyState icon={ShieldAlertIcon} title="Proposal not found" tone="bad">
        {error instanceof Error ? error.message : "The requested proposal could not be loaded."}
      </EmptyState>
    )
  }

  const evidence = proposal.gemini_evidence ?? { evidence_summary: "" }
  const hasDetail = proposal.legs !== undefined
  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-start gap-x-4 gap-y-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-bold tracking-tight">{shortSymbol(proposal.symbol)}</h2>
            <ProposalStatusChip status={proposal.status} />
            {proposal.status === "approved" && <EntryStateChip state={proposal.entry_state} />}
            <TemplateBadge template={proposal.entry_template} />
            {!proposal.live_eligible && (
              <span className="rounded border border-wa/40 px-1.5 text-2xs leading-4 text-wa">Review only</span>
            )}
          </div>
          <p className="mt-1 font-mono text-2xs text-muted-text">
            EOD {fmtDate(proposal.as_of_date)} · entry session {fmtDate(proposal.entry_session_date)} · confidence{" "}
            {fmtNum(toNum(proposal.confidence), 0)} (display only)
          </p>
        </div>
        <Button className="ml-auto" onClick={() => openChart(proposal.symbol)} size="sm" type="button" variant="outline">
          <LineChartIcon data-icon="inline-start" /> Chart
        </Button>
      </header>

      {evidence.mismatch_banner && (
        <Banner icon={AlertTriangleIcon} tone="warn">
          Contraction-count mismatch: Python found {evidence.python_count ?? "?"}, Gemini resolved {evidence.llm_count ?? "?"}. The template was forced to
          single — review both lists before approving.
        </Banner>
      )}
      <OutcomeBanner proposal={proposal} />
      {proposal.status === "pending_approval" && <DecisionBar onDecided={onDecided} proposal={proposal} />}

      <KeyLevels proposal={proposal} />

      {proposal.position && (
        <Panel flush title="Position">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1 px-4 py-2.5 text-xs">
            <PositionStateChip position={proposal.position} />
            <span className="text-muted-text">
              {proposal.position.execution_mode.toUpperCase()} · {proposal.position.open_quantity ?? 0}/{proposal.position.quantity ?? 0} open @{" "}
              {price(proposal.position.average_entry_price)}
            </span>
            <span className="text-muted-text">
              Realized{" "}
              <b className={cn("font-mono", (toNum(proposal.position.realized_pnl) ?? 0) >= 0 ? "text-ok" : "text-ko")}>
                {fmtMoney(proposal.position.realized_pnl, { sign: true })}
              </b>
            </span>
            {proposal.position.closed_at && <span className="text-muted-text">Closed {fmtDateTime(proposal.position.closed_at)}</span>}
            <Link className="link-act ml-auto" to="/positions">
              Open positions →
            </Link>
          </div>
        </Panel>
      )}

      {hasDetail ? <LegsPanel proposal={proposal} /> : <div className="h-16 animate-pulse rounded-md bg-hl" />}

      <Panel title="Charts">
        <ProposalCharts
          baseUrl={`${API_BASE_URL}/automation/proposals/${proposal.id}/charts`}
          rendererVersion={proposal.renderer_version}
          symbol={shortSymbol(proposal.symbol)}
        />
      </Panel>

      <EvidencePanel proposal={proposal} />
      <BasisPanel proposal={proposal} />
      <AuditFooter onOpenRun={onOpenRun} proposal={proposal} />
    </div>
  )
}

/** Deep-link route /proposals/:proposalId — same detail, full width. */
export function ProposalDetailPage() {
  const { proposalId } = useParams<{ proposalId: string }>()
  const navigate = useNavigate()
  return (
    <section className="view h-full">
      <div className="flex flex-none items-center gap-2 border-b border-border px-5 py-2">
        <Button onClick={() => navigate("/proposals")} size="sm" type="button" variant="ghost">
          <ArrowLeftIcon data-icon="inline-start" /> Proposals
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-5xl p-5">
          {proposalId && (
            <ProposalDetail
              onOpenRun={(runId) => navigate(`/proposals?tab=runs&run=${runId}`)}
              proposalId={proposalId}
            />
          )}
        </div>
      </div>
    </section>
  )
}
