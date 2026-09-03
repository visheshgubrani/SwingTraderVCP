import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router"
import { SearchIcon } from "lucide-react"

import { useToast } from "@/components/terminal/toast"
import { useInstrumentSearch, useWatchlists, useAddWatchlistItem, useRemoveWatchlistItem } from "@/features/watchlist/api"
import { cn } from "@/lib/utils"

function symbolShort(fyersSymbol: string): string {
  // NSE:RELIANCE-EQ → RELIANCE
  return fyersSymbol.replace(/^[A-Z0-9]+:/, "").replace(/-EQ$/, "")
}

/** Terminal top-bar global search: instruments lookup → chart / watchlist. */
export function GlobalSearch() {
  const navigate = useNavigate()
  const { toast } = useToast()
  const [q, setQ] = useState("")
  const [open, setOpen] = useState(false)
  const [cursor, setCursor] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  const lists = useWatchlists(q.length > 0 || open)
  const activeId = lists.data?.find((list) => list.is_active)?.id ?? lists.data?.[0]?.id ?? null
  const search = useInstrumentSearch(q, open)
  const addMutation = useAddWatchlistItem(activeId)
  const removeMutation = useRemoveWatchlistItem(activeId)
  const hits = search.data ?? []

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      const typing =
        target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) && target !== inputRef.current
      if (event.key === "/" && !typing) {
        event.preventDefault()
        inputRef.current?.focus()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  useEffect(() => setCursor(0), [q])

  useEffect(() => {
    const onPointer = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    window.addEventListener("pointerdown", onPointer)
    return () => window.removeEventListener("pointerdown", onPointer)
  }, [])

  const pick = useCallback(
    (hit: { symbol: string; fyers_symbol: string }) => {
      setOpen(false)
      setQ("")
      navigate(`/?symbol=${encodeURIComponent(hit.fyers_symbol)}`)
      toast("info", { title: `${symbolShort(hit.fyers_symbol)} loaded on chart` })
    },
    [navigate, toast],
  )

  const toggleWatch = useCallback(
    async (hit: { id: string; symbol: string; fyers_symbol: string }) => {
      if (!activeId) {
        toast("warn", { title: "No watchlist available" })
        return
      }
      try {
        // Attempt to add; the server 409s when the item is already active, in
        // which case the click means "remove" (search hits carry instrument id).
        await addMutation.mutateAsync(hit.fyers_symbol)
        toast("ok", { title: `${hit.symbol} added to watchlist` })
      } catch {
        try {
          await removeMutation.mutateAsync(hit.id)
          toast("info", { title: `${hit.symbol} removed from watchlist` })
        } catch {
          toast("bad", { title: `Watchlist update failed — ${hit.symbol}` })
        }
      }
    },
    [activeId, addMutation, removeMutation, toast],
  )

  return (
    <div className="srch" ref={containerRef}>
      <SearchIcon aria-hidden="true" className="srch-ic" />
      <input
        aria-label="Search symbols and sectors"
        autoComplete="off"
        onChange={(event) => {
          setQ(event.target.value)
          setOpen(event.target.value.trim().length > 0)
        }}
        onFocus={() => {
          if (q.trim()) setOpen(true)
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") setOpen(false)
          if (event.key === "ArrowDown") {
            event.preventDefault()
            setCursor((c) => Math.min(c + 1, hits.length - 1))
          }
          if (event.key === "ArrowUp") {
            event.preventDefault()
            setCursor((c) => Math.max(c - 1, 0))
          }
          if (event.key === "Enter" && hits[cursor]) {
            event.preventDefault()
            pick(hits[cursor])
          }
        }}
        placeholder="Search symbols, sectors"
        ref={inputRef}
        type="text"
        value={q}
      />
      <kbd>/</kbd>
      {open && (
        <div className="srch-pop">
          {search.isLoading && <div className="none">Searching instruments…</div>}
          {!search.isLoading && search.isError && (
            <div className="none">Search unavailable — is the backend running?</div>
          )}
          {!search.isLoading && !search.isError && hits.length === 0 && (
            <div className="none">No match for “{q.trim()}” — try a NSE ticker or company name.</div>
          )}
          {hits.map((hit, index) => (
            <div className={cn("flex items-center gap-2 pr-1", index === cursor && "k")} key={hit.id}>
              <button
                className="min-w-0 flex-1 items-baseline"
                onClick={() => pick(hit)}
                type="button"
              >
                <span className="t">{symbolShort(hit.fyers_symbol)}</span>
                <span className="n">{hit.name}</span>
                <span className="e">{hit.exchange}</span>
              </button>
              <button
                aria-label={`Toggle ${hit.symbol} in watchlist`}
                className="rounded px-1 text-muted-text hover:text-wa"
                onClick={() => void toggleWatch(hit)}
                title={activeId ? "Toggle in watchlist" : "No watchlist available"}
                type="button"
              >
                ♥
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
