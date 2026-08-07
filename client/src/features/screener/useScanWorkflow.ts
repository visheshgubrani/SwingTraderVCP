import { useCallback, useEffect, useRef, useState } from "react"
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
  screeningKeys,
  useScanRuns,
  useTriggerScan,
} from "@/features/screener/api"
import { ApiError } from "@/lib/api"

type ScanWorkflowPhase =
  | "idle"
  | "syncing"
  | "queueing_scan"
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
  const queuedSyncRuns = useRef(new Set<string>())
  const queryClient = useQueryClient()
  const syncStatus = useSyncStatus()
  const scanRuns = useScanRuns()
  const triggerSync = useTriggerSync()
  const triggerScan = useTriggerScan()

  const queueScan = useCallback(
    async (
      syncRunId: string | null,
      syncWarning: string | null,
      currentDataReused = false,
    ) => {
      setState({
        phase: "queueing_scan",
        syncRunId,
        scanRunId: null,
        syncWarning,
        message: currentDataReused
          ? "EOD data is already current. Queueing technical scoring without another sync…"
          : syncWarning
            ? `${syncWarning} Queueing technical scoring with current symbols…`
            : "EOD data is current. Queueing technical scoring…",
      })

      try {
        const result = await triggerScan.mutateAsync()
        setState({
          phase: "scanning",
          syncRunId,
          scanRunId: result.scan_run_id,
          syncWarning,
          message: syncWarning
            ? `Technical scoring is running. ${syncWarning}`
            : "Technical scoring is running across the Nifty 500…",
        })
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
    [triggerScan],
  )

  const start = useCallback(async () => {
    const refreshedStatus = await syncStatus.refetch()
    const current = refreshedStatus.data ?? syncStatus.data

    if (current?.data_current) {
      const syncWarning =
        current.state === "partial"
          ? partialSyncWarning(
              current.successful_symbols,
              current.total_symbols,
              current.errors,
            )
          : null
      await queueScan(current.run_id || null, syncWarning, true)
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

    setState({
      ...INITIAL_STATE,
      phase: "syncing",
      message: "Queueing the latest EOD candle sync…",
    })

    try {
      const result = await triggerSync.mutateAsync(1)
      setState({
        phase: "syncing",
        syncRunId: result.run_id,
        scanRunId: null,
        syncWarning: null,
        message: "Waiting for the incremental EOD sync to finish…",
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
  }, [authStatus, queueScan, syncStatus, triggerSync])

  useEffect(() => {
    const current = syncStatus.data
    if (state.phase !== "syncing" || !state.syncRunId || !current) return
    if (current.run_id !== state.syncRunId) return

    if (canScanAfterSync(current)) {
      const completedRunId = current.run_id
      if (!completedRunId || queuedSyncRuns.current.has(completedRunId)) return
      const syncWarning =
        current.state === "partial"
          ? partialSyncWarning(
              current.successful_symbols,
              current.total_symbols,
              current.errors,
            )
          : null
      queuedSyncRuns.current.add(completedRunId)
      void Promise.all([
        queryClient.invalidateQueries({
          queryKey: historicalKeys.candles(),
        }),
        queryClient.invalidateQueries({
          queryKey: historicalKeys.status(),
        }),
      ])
      void queueScan(completedRunId, syncWarning)
      return
    }

    if (
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
          current.state === "partial"
            ? `The EOD sync completed for only ${current.successful_symbols}/${current.total_symbols} symbols, so the scanner was not started.`
            : current.logs.at(-1) ??
              `The EOD sync ended with status ${current.state}.`,
      }))
    }
  }, [
    queryClient,
    queueScan,
    state.phase,
    state.syncRunId,
    syncStatus.data,
  ])

  useEffect(() => {
    if (state.phase !== "scanning" || !state.scanRunId) return
    const run = scanRuns.data?.find((item) => item.id === state.scanRunId)
    if (!run) return

    if (run.status === "succeeded") {
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

  const reset = useCallback(() => setState(INITIAL_STATE), [])

  return {
    ...state,
    isBusy:
      state.phase === "syncing" ||
      state.phase === "queueing_scan" ||
      state.phase === "scanning",
    start,
    reset,
  }
}
