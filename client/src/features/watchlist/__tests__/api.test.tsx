import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"

import {
  useInstrumentSearch,
  useWatchlistItems,
  useWatchlists,
} from "../api"

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

const WATCHLISTS = [
  { id: "wl-1", name: "Core", description: null, is_active: true, item_count: 2, created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z" },
]

const ITEMS = [
  { id: "wi-1", instrument_id: "inst-1", symbol: "RELIANCE", fyers_symbol: "NSE:RELIANCE-EQ", name: "Reliance Industries", added_at: "2026-09-01T00:00:00Z" },
  { id: "wi-2", instrument_id: "inst-2", symbol: "HDFCBANK", fyers_symbol: "NSE:HDFCBANK-EQ", name: "HDFC Bank", added_at: "2026-09-01T00:00:01Z" },
]

describe("watchlist api hooks", () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("loads the watchlist list and resolves the active watchlist items", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith("/watchlists")) return Promise.resolve(jsonResponse(WATCHLISTS))
      if (url.includes("/watchlists/wl-1/items")) return Promise.resolve(jsonResponse(ITEMS))
      return Promise.resolve(jsonResponse({ detail: `unexpected ${url}` }, 404))
    })

    const wrapper = createWrapper()
    const { result: listResult } = renderHook(() => useWatchlists(), { wrapper })
    await waitFor(() => expect(listResult.current.isSuccess).toBe(true))
    expect(listResult.current.data?.[0]?.name).toBe("Core")

    const { result: itemsResult } = renderHook(() => useWatchlistItems(), { wrapper })
    await waitFor(() => expect(itemsResult.current.isSuccess).toBe(true))
    expect(itemsResult.current.data).toHaveLength(2)
    expect(itemsResult.current.data?.[1]?.symbol).toBe("HDFCBANK")
  })

  it("queries instrument search with the encoded q and limit", async () => {
    fetchMock.mockResolvedValue(jsonResponse([]))
    const wrapper = createWrapper()
    const { result } = renderHook(() => useInstrumentSearch("HDFC BANK", true), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const called = fetchMock.mock.calls[0]?.[0] as string
    expect(called).toContain("/instruments/search?")
    expect(called).toContain("q=HDFC%20BANK")
    expect(called).toContain("limit=25")
  })

  it("keeps the search query disabled for an empty string", () => {
    const wrapper = createWrapper()
    const { result } = renderHook(() => useInstrumentSearch("  ", true), { wrapper })
    expect(result.current.isFetching).toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
