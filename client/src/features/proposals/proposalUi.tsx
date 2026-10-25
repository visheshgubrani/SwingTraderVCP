import { useEffect, useState, type ReactNode } from "react"
import { Maximize2Icon, XIcon } from "lucide-react"

import { StatusChip, type StatusTone } from "@/components/terminal/bits"
import { cn } from "@/lib/utils"
import type {
  ProposalEntryState,
  ProposalGenerationAttempt,
  ProposalStatus,
  TradeProposalItem,
} from "./api"

/* ── labels and tones ── */

export const STATUS_META: Record<ProposalStatus, { label: string; tone: StatusTone }> = {
  pending_approval: { label: "Pending", tone: "wait" },
  approved: { label: "Approved", tone: "fill" },
  rejected: { label: "Rejected", tone: "rej" },
  expired_unapproved: { label: "Expired", tone: "off" },
}

export const ENTRY_STATE_META: Record<ProposalEntryState, { label: string; tone: StatusTone }> = {
  armed: { label: "Armed", tone: "work" },
  trigger_observed: { label: "Triggered", tone: "work" },
  waiting_for_reset: { label: "Waiting reset", tone: "wait" },
  executing: { label: "Executing", tone: "work" },
  filled: { label: "Filled", tone: "fill" },
  expired: { label: "Entry expired", tone: "off" },
}

const TEMPLATE_LABEL: Record<TradeProposalItem["entry_template"], string> = {
  single: "Single",
  two_leg: "Two-leg",
  two_leg_staged: "Two-leg staged",
  three_leg_front: "Three-leg front",
  three_leg_balanced: "Three-leg balanced",
}

export function ProposalStatusChip({ status }: { status: ProposalStatus }) {
  const meta = STATUS_META[status] ?? { label: status, tone: "off" as StatusTone }
  return <StatusChip tone={meta.tone}>{meta.label}</StatusChip>
}

export function EntryStateChip({ state }: { state: TradeProposalItem["entry_state"] }) {
  if (!state) return <span className="text-muted-text">—</span>
  const meta = ENTRY_STATE_META[state]
  return <StatusChip tone={meta.tone}>{meta.label}</StatusChip>
}

export function TemplateBadge({ template }: { template: TradeProposalItem["entry_template"] }) {
  return (
    <span className="inline-flex items-center rounded border border-border-soft bg-field px-1.5 font-mono text-2xs leading-4 text-fg2">
      {TEMPLATE_LABEL[template] ?? template}
    </span>
  )
}

export function PositionStateChip({ position }: { position: TradeProposalItem["position"] }) {
  if (!position) return <span className="text-muted-text">—</span>
  const tone: StatusTone =
    position.state === "closed" ? "off" : position.state === "cancelled" ? "off" : position.state === "exit_pending" ? "wait" : "fill"
  return <StatusChip tone={tone}>{position.state.replaceAll("_", " ")}</StatusChip>
}

/** Outcome of one generation attempt, as the owner would describe it. */
export function attemptOutcome(attempt: ProposalGenerationAttempt): { label: string; tone: StatusTone } {
  if (attempt.error_type === "proposal_already_exists") return { label: "Already proposed", tone: "off" }
  if (attempt.error_type === "proposal_structural_forming") return { label: "Forming", tone: "wait" }
  switch (attempt.status) {
    case "valid":
      return { label: "Proposed", tone: "fill" }
    case "running":
      return { label: "Running", tone: "work" }
    case "uncertain":
      return { label: "Uncertain", tone: "wait" }
    case "partial":
      return { label: "Partial", tone: "wait" }
    case "timed_out":
      return { label: "Timed out", tone: "rej" }
    case "failed":
      return { label: "Failed", tone: "rej" }
    default:
      return { label: "Rejected", tone: "rej" }
  }
}

export function AttemptOutcomeChip({ attempt }: { attempt: ProposalGenerationAttempt }) {
  const outcome = attemptOutcome(attempt)
  return <StatusChip tone={outcome.tone}>{outcome.label}</StatusChip>
}

/** "proposal_structural_undercut_lower_low" → "Structural undercut lower low". */
export function humanizeCode(code: string | null | undefined): string {
  if (!code) return "—"
  const text = code.replace(/^proposal_/, "").replaceAll("_", " ")
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/* ── layout bits ── */

/** Label/value row for detail panels. */
export function KV({ label, children, className }: { label: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-baseline justify-between gap-4 border-b border-border-soft py-1.5 last:border-b-0", className)}>
      <span className="text-xs text-muted-text">{label}</span>
      <span className="text-right font-mono text-xs text-fg2">{children}</span>
    </div>
  )
}

/** Labelled value tile in the key-levels strip. */
export function LevelTile({
  label,
  value,
  sub,
  tone = "neutral",
  flag,
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
  tone?: "neutral" | "ok" | "bad"
  flag?: ReactNode
}) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5 rounded-md border border-border bg-field px-3 py-2">
      <span className="text-2xs font-semibold tracking-[0.08em] text-muted-text uppercase">{label}</span>
      <span
        className={cn(
          "font-mono text-sm font-semibold tabular-nums",
          tone === "ok" ? "text-ok" : tone === "bad" ? "text-ko" : "text-foreground",
        )}
      >
        {value}
      </span>
      {sub && <span className="truncate text-2xs text-muted-text">{sub}</span>}
      {flag && <span className="text-2xs text-wa">{flag}</span>}
    </div>
  )
}

/* ── charts ── */

function Lightbox({ src, alt, onClose }: { src: string; alt: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation()
        onClose()
      }
    }
    window.addEventListener("keydown", onKey, true)
    return () => window.removeEventListener("keydown", onKey, true)
  }, [onClose])

  return (
    <div
      aria-label={alt}
      aria-modal="true"
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/85 p-6 backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
    >
      <button aria-label="Close chart" className="ibtn absolute top-4 right-4 !size-8 bg-surface" onClick={onClose} type="button">
        <XIcon className="size-4" />
      </button>
      <img alt={alt} className="max-h-full max-w-full rounded-md object-contain" onClick={(event) => event.stopPropagation()} src={src} />
    </div>
  )
}

/**
 * The two server-rendered proposal charts: the 126-session chart Gemini saw
 * and the 252-session human context chart. Click to enlarge.
 */
export function ProposalCharts({ baseUrl, symbol, rendererVersion }: { baseUrl: string; symbol: string; rendererVersion?: string }) {
  const [view, setView] = useState<"detail" | "context">("detail")
  const [expanded, setExpanded] = useState(false)
  const src = `${baseUrl}/${view}`
  const alt =
    view === "detail" ? `${symbol} · 126-session chart sent to Gemini` : `${symbol} · 252-session context chart (human only)`

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="seg" role="group" aria-label="Chart view">
          <button aria-pressed={view === "detail"} className={cn("!h-7 !text-2xs", view === "detail" && "on")} onClick={() => setView("detail")} type="button">
            126 sessions · Gemini
          </button>
          <button aria-pressed={view === "context"} className={cn("!h-7 !text-2xs", view === "context" && "on")} onClick={() => setView("context")} type="button">
            252 sessions · context
          </button>
        </div>
        {rendererVersion && <span className="font-mono text-2xs text-muted-text">{rendererVersion}</span>}
        <button className="link-act ml-auto inline-flex items-center gap-1" onClick={() => setExpanded(true)} type="button">
          <Maximize2Icon className="size-3" /> Enlarge
        </button>
      </div>
      <button
        className="overflow-hidden rounded-md border border-border bg-black"
        onClick={() => setExpanded(true)}
        title="Enlarge chart"
        type="button"
      >
        <img alt={alt} className="w-full object-contain" loading="lazy" src={src} />
      </button>
      {expanded && <Lightbox alt={alt} onClose={() => setExpanded(false)} src={src} />}
    </div>
  )
}
