import { describe, expect, it } from "vitest"

import { canScanAfterSync, isSyncActive, type SyncStatus } from "../api"

describe("sync request restrictions", () => {
  it("keeps a cooldown active while preventing scan eligibility", () => {
    const status = {
      state: "running", wait_reason: "rate_limit", data_current: false,
      scanner_ready: true,
    } as SyncStatus
    expect(isSyncActive(status)).toBe(true)
    expect(canScanAfterSync(status)).toBe(false)
  })

  it("rejects terminal restrictions even with otherwise usable coverage", () => {
    const status = {
      state: "partial", data_current: true, scanner_ready: true,
      total_symbols: 500, successful_symbols: 500, error_count: 0,
      failure_reason: "Fyers history rolling 24-hour request quota is exhausted.",
    } as SyncStatus
    expect(isSyncActive(status)).toBe(false)
    expect(canScanAfterSync(status)).toBe(false)
  })
})
