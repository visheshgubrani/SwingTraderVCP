import { useCallback, useEffect, useMemo, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"

import {
  claimChartArtifact,
  journalKeys,
  uploadChartArtifact,
  type ChartArtifactClaim,
} from "@/features/journal/api"
import { EntryCaptureChart } from "@/features/journal/EntryCaptureChart"

const CLAIMER_ID =
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? `capture-${crypto.randomUUID()}`
    : `capture-${Date.now()}`

const CLAIM_POLL_INTERVAL_MS = 15_000
const CLAIM_RETRY_MAX_INTERVAL_MS = 5 * 60_000

function retryDelay(consecutiveFailures: number) {
  return Math.min(
    CLAIM_POLL_INTERVAL_MS * 2 ** Math.max(0, consecutiveFailures - 1),
    CLAIM_RETRY_MAX_INTERVAL_MS,
  )
}

export function JournalCaptureManager() {
  const queryClient = useQueryClient()
  const [artifact, setArtifact] = useState<ChartArtifactClaim | null>(null)

  useEffect(() => {
    if (artifact) return

    let cancelled = false
    let consecutiveFailures = 0
    let timer: number | undefined

    const schedule = (delay: number) => {
      timer = window.setTimeout(() => {
        void poll()
      }, delay)
    }

    const poll = async () => {
      try {
        const claim = await claimChartArtifact(CLAIMER_ID)
        if (cancelled) return

        consecutiveFailures = 0
        if (claim) {
          setArtifact(claim)
          return
        }
        schedule(CLAIM_POLL_INTERVAL_MS)
      } catch {
        if (cancelled) return

        consecutiveFailures += 1
        schedule(retryDelay(consecutiveFailures))
      }
    }

    // Scheduling the initial poll also avoids duplicate requests from React's
    // development-mode effect setup/cleanup check.
    schedule(0)

    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [artifact])

  const handleCaptured = useCallback(
    async (blob: Blob) => {
      if (!artifact) return
      try {
        await uploadChartArtifact(artifact.id, CLAIMER_ID, blob)
        queryClient.invalidateQueries({ queryKey: journalKeys.all })
      } finally {
        setArtifact(null)
      }
    },
    [artifact, queryClient],
  )

  const handleError = useCallback((_message: string) => {
    setArtifact(null)
  }, [])

  const activeArtifact = useMemo(() => artifact, [artifact])

  if (!activeArtifact) return null

  return (
    <EntryCaptureChart
      artifact={activeArtifact}
      onCaptured={handleCaptured}
      onError={handleError}
    />
  )
}
