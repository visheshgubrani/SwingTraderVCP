import { useMemo, useState, type ReactNode } from "react"

import { Panel, SectionLabel } from "@/components/terminal/panel"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"
import { fmtDate, fmtNum } from "@/lib/format"
import { cn } from "@/lib/utils"
import {
  useEnforceMarketContext,
  useMarketContext,
  useResetStopStreak,
  useStopStreak,
  type MarketLight,
  type SectorTier,
} from "./api"

function lightClass(light: MarketLight | SectorTier) {
  if (light === "green" || light === "leading") return "border-ok/30 bg-ok-soft text-ok"
  if (light === "yellow" || light === "neutral") return "border-wa/30 bg-wa-soft text-wa"
  if (light === "red" || light === "lagging") return "border-ko/30 bg-ko-soft text-ko"
  return "border-border bg-hl text-muted-text"
}

function Pill({ tone, children, title }: { tone: MarketLight | SectorTier; children: ReactNode; title?: string }) {
  return (
    <span className={cn("inline-flex items-center rounded border px-1.5 font-mono text-2xs leading-5", lightClass(tone))} title={title}>
      {children}
    </span>
  )
}

/** Compact market-light chip for page headers; click opens the details. */
export function MarketLightChip({ onClick }: { onClick: () => void }) {
  const { data } = useMarketContext()
  const light = data?.market_light ?? "unavailable"
  return (
    <button
      className={cn("chip cursor-pointer", lightClass(light))}
      onClick={onClick}
      title="P9 market context — click for sectors and breadth"
      type="button"
    >
      <i className="dot" />
      MARKET {light.toUpperCase()}
      {data && <span className="font-mono text-2xs opacity-80">{data.mode}</span>}
    </button>
  )
}

/** Read-only P9 snapshot: light, axes, sectors. */
export function MarketContextDetails() {
  const { data: context, error, isLoading } = useMarketContext()
  const rankedSectors = useMemo(
    () => [...(context?.sectors ?? [])].sort((a, b) => (a.ordinal_rank ?? 99) - (b.ordinal_rank ?? 99)),
    [context?.sectors],
  )
  if (isLoading) return <p className="text-xs text-muted-text">Loading the latest EOD snapshot…</p>
  if (error instanceof Error) return <p className="text-xs text-ko">{error.message}</p>
  if (!context) return <p className="text-xs text-muted-text">No market-context snapshot yet.</p>

  const breadth = context.evidence.breadth_distribution as Record<string, unknown> | undefined
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone={context.market_light}>{context.market_light.toUpperCase()}</Pill>
        <span className="text-xs text-muted-text">
          {context.mode} · policy {context.policy_version} · EOD {fmtDate(context.reference_eod_date)} · risk ×
          {fmtNum(context.exposure_multiplier)}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Pill tone={context.trend_state}>trend {context.trend_state}</Pill>
        <Pill tone={context.breadth_state}>breadth {context.breadth_state}</Pill>
        <Pill tone={context.distribution_state}>distribution {context.distribution_state}</Pill>
      </div>
      <p className="text-xs text-muted-text">
        Breadth {breadth?.breadth_pct === undefined ? "—" : `${fmtNum(Number(breadth.breadth_pct), 1)}%`} · distribution days{" "}
        {context.evidence.distribution_count === undefined ? "—" : String(context.evidence.distribution_count)}
      </p>
      <div>
        <SectionLabel>Sectors · rank · RS · confirmed gate</SectionLabel>
        <div className="mt-1.5 flex flex-wrap gap-1">
          {rankedSectors.length === 0 ? (
            <span className="text-xs text-muted-text">No complete sector snapshot.</span>
          ) : (
            rankedSectors.map((sector) => (
              <Pill key={sector.sector_code} title={`${sector.sector_name} · raw ${sector.raw_tier}`} tone={sector.gate_tier}>
                {sector.ordinal_rank ?? "—"}. {sector.sector_code} · {sector.rs_rating ?? "—"}
              </Pill>
            ))
          )}
        </div>
      </div>
      {context.mode === "shadow" && (
        <p className="text-xs text-muted-text">
          Shadow mode: the light is recorded but does not gate entries. Enforcement is an owner action in Operations.
        </p>
      )}
    </div>
  )
}

/** Three-stop circuit breaker for one execution mode, with owner reset (Operations). */
export function StopStreakCard({ mode }: { mode: "paper" | "live" }) {
  const { data, error } = useStopStreak(mode)
  const reset = useResetStopStreak()
  const [reason, setReason] = useState("")
  return (
    <div className="rounded-md border border-border-soft bg-field p-3">
      <div className="flex items-center justify-between gap-2">
        <SectionLabel>{mode} stop streak</SectionLabel>
        <span className={cn("font-mono text-sm font-semibold", data?.tripped ? "text-ko" : "text-fg2")}>
          {data ? `${data.consecutive_count}/${data.limit}` : "—"}
        </span>
      </div>
      <p className="mt-1 text-xs text-muted-text">
        {error instanceof Error
          ? error.message
          : data?.tripped
            ? "Tripped — new initials and adds are paused until you reset."
            : "Counts consecutive pure stop-loss closures."}
      </p>
      {data?.tripped && (
        <div className="mt-2 flex gap-2">
          <Input
            aria-label={`${mode} breaker reset acknowledgement`}
            className="h-7 text-xs"
            onChange={(event) => setReason(event.target.value)}
            placeholder="Acknowledgement"
            value={reason}
          />
          <Button
            disabled={!reason.trim() || reset.isPending}
            onClick={() => reset.mutate({ mode, reason: reason.trim() }, { onSuccess: () => setReason("") })}
            size="sm"
            type="button"
            variant="destructive"
          >
            Reset
          </Button>
        </div>
      )}
      {reset.error instanceof Error && <p className="mt-1 text-xs text-ko">{reset.error.message}</p>}
    </div>
  )
}

/** Owner-approved shadow → enforced promotion of the P9 policy (Operations). */
export function EnforcePolicyForm() {
  const { data: context } = useMarketContext()
  const enforce = useEnforceMarketContext()
  const [reportHash, setReportHash] = useState("")
  const [approvedBy, setApprovedBy] = useState("")
  const [membershipMode, setMembershipMode] = useState<"point_in_time" | "current_membership_survivorship_biased">("point_in_time")

  if (!context) return <p className="text-xs text-muted-text">No market-context policy loaded.</p>
  if (context.mode !== "shadow") {
    return (
      <p className="text-xs text-muted-text">
        Policy {context.policy_version} is <b className="text-ok">enforced</b>
        {context.replay_report_hash ? ` (replay ${context.replay_report_hash.slice(0, 12)}…)` : ""}.
      </p>
    )
  }
  const canEnforce = /^[0-9a-f]{64}$/.test(reportHash) && approvedBy.trim().length > 0
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-muted-text">
        Policy {context.policy_version} runs in shadow. Enforcing needs the owner-approved replay-report hash.
      </p>
      <div className="grid gap-2 md:grid-cols-[1.4fr_0.8fr_1fr_auto]">
        <Input
          aria-label="Replay report SHA-256"
          className="h-8 text-xs"
          onChange={(event) => setReportHash(event.target.value.trim().toLowerCase())}
          placeholder="64-character replay report hash"
          value={reportHash}
        />
        <Input aria-label="Policy approver" className="h-8 text-xs" onChange={(event) => setApprovedBy(event.target.value)} placeholder="Approved by" value={approvedBy} />
        <NativeSelect
          aria-label="Replay membership mode"
          className="h-8 text-xs"
          onChange={(event) => setMembershipMode(event.target.value as typeof membershipMode)}
          value={membershipMode}
        >
          <NativeSelectOption value="point_in_time">Point-in-time membership</NativeSelectOption>
          <NativeSelectOption value="current_membership_survivorship_biased">Current members (biased)</NativeSelectOption>
        </NativeSelect>
        <Button
          disabled={!canEnforce || enforce.isPending}
          onClick={() =>
            enforce.mutate({ version: context.policy_version, replayReportHash: reportHash, membershipMode, approvedBy: approvedBy.trim() })
          }
          size="sm"
          type="button"
        >
          Enforce
        </Button>
      </div>
      {enforce.error instanceof Error && <p className="text-xs text-ko">{enforce.error.message}</p>}
    </div>
  )
}

/** Market context + both stop-streak breakers, as one Operations panel. */
export function MarketContextAdminPanel() {
  return (
    <Panel title="Market context & breakers">
      <div className="flex flex-col gap-4">
        <MarketContextDetails />
        <EnforcePolicyForm />
        <div className="grid gap-2 md:grid-cols-2">
          <StopStreakCard mode="paper" />
          <StopStreakCard mode="live" />
        </div>
      </div>
    </Panel>
  )
}
