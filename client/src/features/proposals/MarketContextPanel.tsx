import { useMemo, useState } from "react"
import { ActivityIcon, ShieldAlertIcon } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select"
import {
  useEnforceMarketContext,
  useMarketContext,
  useResetStopStreak,
  useStopStreak,
  type MarketLight,
  type SectorTier,
} from "./api"

function lightClass(light: MarketLight | SectorTier) {
  if (light === "green" || light === "leading") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
  if (light === "yellow" || light === "neutral") return "border-amber-500/30 bg-amber-500/10 text-amber-300"
  if (light === "red" || light === "lagging") return "border-rose-500/30 bg-rose-500/10 text-rose-400"
  return "border-border bg-muted/30 text-muted-foreground"
}

function StopState({ mode }: { mode: "paper" | "live" }) {
  const { data, error } = useStopStreak(mode)
  const reset = useResetStopStreak()
  const [reason, setReason] = useState("")

  return (
    <div className="rounded-md border border-border/60 bg-background/40 p-2">
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold uppercase">{mode}</span>
        <Badge variant={data?.tripped ? "destructive" : "outline"}>
          {data ? `${data.consecutive_count}/${data.limit}` : "—"}
        </Badge>
      </div>
      <div className="mt-1 text-[10px] text-muted-foreground">
        {error instanceof Error
          ? error.message
          : data?.tripped
            ? "Tripped — initials and adds remain paused until owner reset."
            : "Stop-loss closures counted from breaker activation."}
      </div>
      {data?.tripped && (
        <div className="mt-2 flex gap-2">
          <Input
            aria-label={`${mode} breaker reset acknowledgement`}
            className="h-7 text-[10px]"
            onChange={(event) => setReason(event.target.value)}
            placeholder="Owner acknowledgement"
            value={reason}
          />
          <Button
            disabled={!reason.trim() || reset.isPending}
            onClick={() => reset.mutate({ mode, reason: reason.trim() })}
            size="sm"
            type="button"
            variant="destructive"
          >
            Reset
          </Button>
        </div>
      )}
      {reset.error instanceof Error && (
        <div className="mt-1 text-[10px] text-destructive">{reset.error.message}</div>
      )}
    </div>
  )
}

export function MarketContextPanel() {
  const { data: context, error, isLoading } = useMarketContext()
  const enforce = useEnforceMarketContext()
  const [reportHash, setReportHash] = useState("")
  const [approvedBy, setApprovedBy] = useState("")
  const [membershipMode, setMembershipMode] = useState<
    "point_in_time" | "current_membership_survivorship_biased"
  >("point_in_time")

  const rankedSectors = useMemo(
    () => [...(context?.sectors ?? [])].sort((a, b) => (a.ordinal_rank ?? 99) - (b.ordinal_rank ?? 99)),
    [context?.sectors],
  )
  const canEnforce = context?.mode === "shadow"
    && /^[0-9a-f]{64}$/.test(reportHash)
    && approvedBy.trim().length > 0
  const breadthEvidence = context?.evidence.breadth_distribution as Record<string, unknown> | undefined
  const distributionCount = context?.evidence.distribution_count

  return (
    <div className="border-b border-border/60 bg-card/30 px-4 py-3">
      <div className="grid gap-3 xl:grid-cols-[1.4fr_2fr_1fr]">
        <Alert className="bg-background/40">
          <ActivityIcon aria-hidden="true" />
          <AlertTitle className="flex items-center gap-2">
            P9 market context
            <Badge className={lightClass(context?.market_light ?? "unavailable")} variant="outline">
              {(context?.market_light ?? "unavailable").toUpperCase()}
            </Badge>
            <Badge variant={context?.mode === "enforced" ? "default" : "secondary"}>
              {context?.mode ?? "loading"}
            </Badge>
          </AlertTitle>
          <AlertDescription className="text-[10px]">
            {isLoading
              ? "Loading the latest deterministic EOD snapshot…"
              : error instanceof Error
                ? error.message
                : `${context?.reference_eod_date ?? "No EOD snapshot"} · risk multiplier ${Number(context?.exposure_multiplier ?? 0).toFixed(2)}×`}
          </AlertDescription>
          {context && (
            <div className="col-start-2 mt-2 text-[10px]">
              <div className="grid grid-cols-3 gap-1">
                {["trend", "breadth", "distribution"].map((axis) => {
                  const value = context[`${axis}_state` as "trend_state" | "breadth_state" | "distribution_state"]
                  return <Badge className={lightClass(value)} key={axis} variant="outline">{axis}: {value}</Badge>
                })}
              </div>
              <div className="mt-1 text-muted-foreground">
                Breadth {breadthEvidence?.breadth_pct === undefined ? "—" : `${Number(breadthEvidence.breadth_pct).toFixed(1)}%`} · distribution days {distributionCount === undefined ? "—" : String(distributionCount)} · source {context.source_hash?.slice(0, 10) ?? "unavailable"}
              </div>
            </div>
          )}
        </Alert>

        <div className="rounded-lg border border-border/60 bg-background/40 p-2">
          <div className="mb-2 flex items-center justify-between">
            <strong>Sector discipline</strong>
            <span className="text-[10px] text-muted-foreground">rank · display RS · confirmed gate</span>
          </div>
          <div className="flex max-h-24 flex-wrap gap-1 overflow-auto">
            {rankedSectors.length === 0 ? (
              <span className="text-muted-foreground">No complete sector snapshot.</span>
            ) : rankedSectors.map((sector) => (
              <Badge className={lightClass(sector.gate_tier)} key={sector.sector_code} title={`${sector.sector_name} · raw ${sector.raw_tier}`} variant="outline">
                {sector.ordinal_rank ?? "—"}. {sector.sector_code} · {sector.rs_rating ?? "—"} · {sector.gate_tier}
              </Badge>
            ))}
          </div>
          {context?.mode === "shadow" && (
            <div className="mt-2 grid gap-2 md:grid-cols-[1fr_0.7fr_1fr_auto]">
              <Input aria-label="Replay report SHA-256" className="h-7 text-[10px]" onChange={(event) => setReportHash(event.target.value.trim().toLowerCase())} placeholder="64-character replay report hash" value={reportHash} />
              <Input aria-label="P9 policy approver" className="h-7 text-[10px]" onChange={(event) => setApprovedBy(event.target.value)} placeholder="Approved by" value={approvedBy} />
              <NativeSelect aria-label="Replay membership mode" className="h-7 text-[10px]" onChange={(event) => setMembershipMode(event.target.value as typeof membershipMode)} value={membershipMode}>
                <NativeSelectOption value="point_in_time">Point-in-time membership</NativeSelectOption>
                <NativeSelectOption value="current_membership_survivorship_biased">Current members (biased)</NativeSelectOption>
              </NativeSelect>
              <Button disabled={!canEnforce || enforce.isPending} onClick={() => context && enforce.mutate({ version: context.policy_version, replayReportHash: reportHash, membershipMode, approvedBy: approvedBy.trim() })} size="sm" type="button">
                Enforce v1
              </Button>
            </div>
          )}
          {enforce.error instanceof Error && <div className="mt-1 text-[10px] text-destructive">{enforce.error.message}</div>}
        </div>

        <Alert className="bg-background/40">
          <ShieldAlertIcon aria-hidden="true" />
          <AlertTitle>Three-stop breaker</AlertTitle>
          <AlertDescription className="text-[10px]">Reset is atomic and does not clear an independent manual pause.</AlertDescription>
          <div className="col-start-2 mt-2 grid gap-2">
            <StopState mode="paper" />
            <StopState mode="live" />
          </div>
        </Alert>
      </div>
    </div>
  )
}
