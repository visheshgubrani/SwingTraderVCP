import { useCallback, useEffect, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"

import type { AuthStatus } from "@/features/auth/api"
import {
  canScanAfterSync,
  historicalKeys,
  isSyncActive,
  useSyncStatus,
  useTriggerSync,
} from "@/features/historical/api"
import {
  latestActiveScanRun,
  screeningKeys,
  useScanRuns,
  useTriggerScan,
} from "@/features/screener/api"
import { ApiError } from "@/lib/api"

type ScanWorkflowPhase =
  | "idle"
  | "syncing"
  | "attaching_scan"
  | "queued"
  | "scanning"
  | "completed"
  | "failed"

interface ScanWorkflowState {
  phase: ScanWorkflowPhase
  syncRunId: string | null
  scanRunId: string | null
  syncWarning: string | null
  message: string | null
}

const INITIAL_STATE: ScanWorkflowState = {
  phase: "idle",
  syncRunId: null,
  scanRunId: null,
  syncWarning: null,
  message: null,
}

function partialSyncWarning(
  successfulSymbols: number,
  totalSymbols: number,
  errors: Array<{ symbol: string; error: string }>,
) {
  const failedSymbols = errors
    .slice(0, 3)
    .map(({ symbol }) => symbol)
    .join(", ")
  const suffix = failedSymbols ? ` Missing: ${failedSymbols}.` : ""
  return `EOD sync completed for ${successfulSymbols}/${totalSymbols} symbols.${suffix}`
}

export function useScanWorkflow(authStatus?: AuthStatus) {
  const [state, setState] = useState<ScanWorkflowState>(INITIAL_STATE)
  const queryClient = useQueryClient()
  const syncStatus = useSyncStatus()
  const scanRuns = useScanRuns()
  const triggerSync = useTriggerSync()
  const triggerScan = useTriggerScan()

  const ensureScan = useCallback(
    async (
      syncRunId: string | null,
      syncWarning: string | null,
      currentDataReused = false,
    ) => {
      setState({
        phase: "attaching_scan",
        syncRunId,
        scanRunId: null,
        syncWarning,
        message: currentDataReused
          ? "EOD data is already current. Opening today’s personal scan…"
          : syncWarning
            ? `${syncWarning} Opening today’s personal scan…`
            : "EOD data is current. Opening today’s personal scan…",
      })

      try {
        const result = await triggerScan.mutateAsync()
        const completed = result.status === "succeeded"
        setState({
          phase:
            result.status === "running"
              ? "scanning"
              : completed
                ? "completed"
                : "queued",
          syncRunId,
          scanRunId: result.scan_run_id,
          syncWarning,
          message: completed
            ? `Today’s personal scan is complete.${syncWarning ? ` ${syncWarning}` : ""}`
            : result.status === "running"
              ? "Technical scoring is running across the Nifty 500…"
              : "Personal scan is queued and waiting for the scanner worker…",
        })
        if (completed) {
          void queryClient.invalidateQueries({
            queryKey: screeningKeys.runResults(result.scan_run_id),
          })
        }
      } catch (error) {
        setState({
          phase: "failed",
          syncRunId,
          scanRunId: null,
          syncWarning,
          message:
            error instanceof Error
              ? error.message
              : "Technical scoring could not be queued.",
        })
      }
    },
    [queryClient, triggerScan],
  )

  const start = useCallback(async () => {
    const refreshedStatus = await syncStatus.refetch()
    const current = refreshedStatus.data ?? syncStatus.data

    if (current?.data_current && current.scanner_ready) {
      const syncWarning =
        current.state === "partial"
          ? partialSyncWarning(
              current.successful_symbols,
              current.total_symbols,
              current.errors,
            )
          : null
      await ensureScan(current.run_id || null, syncWarning, true)
      return
    }

    if (isSyncActive(current) && current?.run_id) {
      setState({
        phase: "syncing",
        syncRunId: current.run_id,
        scanRunId: null,
        syncWarning: null,
        message: "Attached to the EOD sync already in progress…",
      })
      return
    }

    if (!authStatus?.authenticated || !authStatus.healthy) {
      setState({
        ...INITIAL_STATE,
        phase: "failed",
        message: "Stored EOD data is stale. Log in to Fyers before syncing and running the scanner.",
      })
      return
    }

    const needsHistoryRepair = Boolean(current && !current.scanner_ready)
    setState({
      ...INITIAL_STATE,
      phase: "syncing",
      message: needsHistoryRepair
        ? `Only ${current?.scoreable_instruments ?? 0}/${current?.required_scoreable_instruments ?? 0} required stocks have enough history. Queueing a two-year repair…`
        : "Queueing the latest EOD candle sync…",
    })

    try {
      const result = await triggerSync.mutateAsync({
        backfillYears: needsHistoryRepair ? 2 : 1,
        repairHistory: needsHistoryRepair,
      })
      setState({
        phase: "syncing",
        syncRunId: result.run_id,
        scanRunId: null,
        syncWarning: null,
        message: needsHistoryRepair
          ? "Repairing the Nifty 500 history required by the scanner…"
          : "Waiting for the incremental EOD sync to finish…",
      })
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        const current = (await syncStatus.refetch()).data
        if (isSyncActive(current) && current?.run_id) {
          setState({
            phase: "syncing",
            syncRunId: current.run_id,
            scanRunId: null,
            syncWarning: null,
            message: "Attached to the EOD sync already in progress…",
          })
          return
        }
      }
      setState({
        ...INITIAL_STATE,
        phase: "failed",
        message:
          error instanceof Error
            ? error.message
            : "Failed to queue the EOD sync.",
      })
    }
  }, [authStatus, ensureScan, syncStatus, triggerSync])

  // Recover the visible workflow after a reload without starting any new work.
  useEffect(() => {
    if (state.phase !== "idle") return
    const currentSync = syncStatus.data
    if (isSyncActive(currentSync) && currentSync?.run_id) {
      setState({
        phase: "syncing",
        syncRunId: currentSync.run_id,
        scanRunId: null,
        syncWarning: null,
        message: "Attached to the EOD candle sync already in progress…",
      })
      return
    }

    // Runs are newest-first. Only the newest production run can represent
    // work that should be recovered; older queued rows are superseded and may
    // be orphaned legacy jobs.
    const activeRun = latestActiveScanRun(scanRuns.data)
    if (!activeRun) return
    setState({
      phase: activeRun.status === "running" ? "scanning" : "queued",
      syncRunId: null,
      scanRunId: activeRun.id,
      syncWarning: null,
      message:
        activeRun.status === "running"
          ? "Attached to technical scoring already in progress…"
          : "Personal scan is queued and waiting for the scanner worker…",
    })
  }, [scanRuns.data, state.phase, syncStatus.data])

  useEffect(() => {
    const current = syncStatus.data
    if (state.phase !== "syncing" || !state.syncRunId || !current) return
    if (current.run_id !== state.syncRunId) return

    if (canScanAfterSync(current)) {
      const syncWarning =
        current.state === "partial"
          ? partialSyncWarning(
              current.successful_symbols,
              current.total_symbols,
              current.errors,
            )
          : null
      void Promise.all([
        queryClient.invalidateQueries({
          queryKey: historicalKeys.candles(),
        }),
        queryClient.invalidateQueries({
          queryKey: historicalKeys.status(),
        }),
      ])
      if (!current.personal_scan_run_id) {
        setState((previous) => ({
          ...previous,
          phase: "failed",
          syncWarning,
          message:
            current.logs.at(-1) ??
            "EOD data synced, but the backend could not create the personal scan.",
        }))
        return
      }
      const run = scanRuns.data?.find(
        (item) => item.id === current.personal_scan_run_id,
      )
      setState({
        phase: run?.status === "running" ? "scanning" : "queued",
        syncRunId: current.run_id,
        scanRunId: current.personal_scan_run_id,
        syncWarning,
        message:
          run?.status === "running"
            ? "Technical scoring is running across the Nifty 500…"
            : "EOD sync is complete. Personal scan is waiting for the scanner worker…",
      })
      return
    }

    if (
      (current.state === "succeeded" && !current.scanner_ready) ||
      (current.state === "partial" && !canScanAfterSync(current)) ||
      current.state === "failed" ||
      current.state === "cancelled" ||
      current.state === "authentication_required"
    ) {
      setState((previous) => ({
        ...previous,
        phase: "failed",
        syncWarning: null,
        message:
          !current.scanner_ready
            ? `The scanner needs ${current.required_scoreable_instruments} stocks with at least ${current.minimum_history_days} sessions, but only ${current.scoreable_instruments} are ready.`
            : current.state === "partial"
            ? `The EOD sync completed for only ${current.successful_symbols}/${current.total_symbols} symbols, so the scanner was not started.`
            : current.logs.at(-1) ??
              `The EOD sync ended with status ${current.state}.`,
      }))
    }
  }, [
    queryClient,
    scanRuns.data,
    state.phase,
    state.syncRunId,
    syncStatus.data,
  ])

  useEffect(() => {
    if (
      (state.phase !== "queued" && state.phase !== "scanning") ||
      !state.scanRunId
    ) return
    const run = scanRuns.data?.find((item) => item.id === state.scanRunId)
    if (!run) return

    if (run.status === "queued" && state.phase !== "queued") {
      setState((previous) => ({
        ...previous,
        phase: "queued",
        message: "Personal scan is queued and waiting for the scanner worker…",
      }))
    } else if (run.status === "running" && state.phase !== "scanning") {
      setState((previous) => ({
        ...previous,
        phase: "scanning",
        message: "Technical scoring is running across the Nifty 500…",
      }))
    } else if (run.status === "succeeded") {
      setState((previous) => ({
        ...previous,
        phase: "completed",
        message: previous.syncWarning
          ? `Scan complete: ${run.passing_count} ranked setups. ${previous.syncWarning}`
          : `Scan complete: ${run.passing_count} ranked setups.`,
      }))
      void Promise.all([
        queryClient.invalidateQueries({
          queryKey: screeningKeys.runs(),
        }),
        queryClient.invalidateQueries({
          queryKey: screeningKeys.runResults(run.id),
        }),
      ])
    } else if (run.status === "failed" || run.status === "cancelled") {
      setState((previous) => ({
        ...previous,
        phase: "failed",
        message:
          run.error_message ??
          `The technical scan ended with status ${run.status}.`,
      }))
    }
  }, [queryClient, scanRuns.data, state.phase, state.scanRunId])

  // Keep runs list fresh while a scan is in flight (avoids a stuck spinner if
  // the initial invalidate races ahead of the insert becoming visible).
  useEffect(() => {
    if (
      state.phase !== "scanning" &&
      state.phase !== "queued" &&
      state.phase !== "attaching_scan"
    ) return
    const timer = window.setInterval(() => {
      void queryClient.invalidateQueries({ queryKey: screeningKeys.runs() })
    }, 1500)
    return () => window.clearInterval(timer)
  }, [queryClient, state.phase])

  const reset = useCallback(() => setState(INITIAL_STATE), [])

  return {
    ...state,
    isBusy:
      state.phase === "syncing" ||
      state.phase === "attaching_scan" ||
      state.phase === "queued" ||
      state.phase === "scanning",
    start,
    reset,
  }
}
