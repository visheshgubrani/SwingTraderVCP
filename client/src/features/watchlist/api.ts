import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiRequest } from "@/lib/api"

/* ── types (contract: server/app/schemas/watchlists.py, instruments search) ── */

export interface WatchlistSummary {
  id: string
  name: string
  description: string | null
  is_active: boolean
  item_count: number
  created_at: string
  updated_at: string
}

export interface WatchlistItemView {
  id: string
  instrument_id: string
  symbol: string
  fyers_symbol: string
  name: string | null
  added_at: string
}

export interface InstrumentSearchHit {
  id: string
  symbol: string
  trading_symbol: string
  fyers_symbol: string
  name: string | null
  exchange: string
  segment: string
}

export const watchlistKeys = {
  all: ["watchlists"] as const,
  list: () => [...watchlistKeys.all, "list"] as const,
  items: (watchlistId: string) => [...watchlistKeys.all, "items", watchlistId] as const,
  search: (q: string) => ["instruments", "search", q] as const,
}

/* ── queries ── */

export function useWatchlists(enabled = true) {
  return useQuery({
    queryKey: watchlistKeys.list(),
    queryFn: () => apiRequest<WatchlistSummary[]>("/watchlists"),
    enabled,
    staleTime: 15_000,
  })
}

/** Items of the active watchlist (falls back to the first list when none active). */
export function useWatchlistItems(enabled = true) {
  const lists = useWatchlists(enabled)
  const activeId = lists.data?.find((list) => list.is_active)?.id ?? lists.data?.[0]?.id ?? null
  return useQuery({
    queryKey: activeId ? watchlistKeys.items(activeId) : watchlistKeys.items("__none__"),
    queryFn: () => apiRequest<WatchlistItemView[]>(`/watchlists/${activeId}/items`),
    enabled: enabled && activeId !== null,
    staleTime: 15_000,
  })
}

export function useInstrumentSearch(q: string, enabled = true) {
  const trimmed = q.trim()
  return useQuery({
    queryKey: watchlistKeys.search(trimmed),
    queryFn: () =>
      apiRequest<InstrumentSearchHit[]>(
        `/instruments/search?q=${encodeURIComponent(trimmed)}&limit=25`,
      ),
    enabled: enabled && trimmed.length > 0,
    staleTime: 60_000,
  })
}

/* ── mutations ── */

function invalidateWatchlists(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: watchlistKeys.all })
}

export function useAddWatchlistItem(watchlistId: string | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (symbol: string) =>
      apiRequest<WatchlistItemView>(`/watchlists/${watchlistId}/items`, {
        method: "POST",
        body: JSON.stringify({ symbol }),
      }),
    onSuccess: () => invalidateWatchlists(queryClient),
  })
}

export function useRemoveWatchlistItem(watchlistId: string | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (instrumentId: string) =>
      apiRequest<void>(`/watchlists/${watchlistId}/items/${instrumentId}`, {
        method: "DELETE",
      }),
    onSuccess: () => invalidateWatchlists(queryClient),
  })
}

export function useCreateWatchlist() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { name: string; description?: string | null }) =>
      apiRequest<WatchlistSummary>("/watchlists", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => invalidateWatchlists(queryClient),
  })
}
