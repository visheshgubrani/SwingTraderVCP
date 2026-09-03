import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate } from "react-router"
import { PlusIcon, SearchIcon, XIcon } from "lucide-react"

import { useToast } from "@/components/terminal/toast"
import {
  useAddWatchlistItem,
  useCreateWatchlist,
  useInstrumentSearch,
  useRemoveWatchlistItem,
  useUpdateWatchlist,
  useWatchlistItems,
  useWatchlists,
  type InstrumentSearchHit,
} from "@/features/watchlist/api"
import { useMarketData } from "@/lib/MarketWSContext"
import { fmtNum, fmtPct, toneCls } from "@/lib/format"
import { cn } from "@/lib/utils"
import { useTradingAppContext } from "@/features/dashboard/app-context"

function WatchRow({
  symbol,
  fyersSymbol,
  name,
  onOpen,
  onBuy,
  onSell,
  onRemove,
  active,
  removing,
}: {
  symbol: string
  fyersSymbol: string
  name: string | null
  onOpen: (symbol: string) => void
  onBuy: (symbol: string) => void
  onSell: (symbol: string) => void
  onRemove: () => void
  active: boolean
  removing: boolean
}) {
  const { ltpMap } = useMarketData()
  const tick = ltpMap.get(fyersSymbol)
  const chg = tick?.change_pct ?? null
  const tone = toneCls(chg)
  const isIndex = fyersSymbol.includes("INDEX") || symbol.includes("NIFTY") || symbol === "FINNIFTY"

  return (
    <div
      aria-current={active ? "true" : undefined}
      className={cn("wrow", active && "on")}
      onClick={() => onOpen(fyersSymbol)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault()
          onOpen(fyersSymbol)
        }
      }}
      role="button"
      tabIndex={0}
      title={`${symbol} · ${name ?? fyersSymbol}${isIndex ? " (Index)" : ""}`}
    >
      <span className="ws">
        <span className="wt flex items-center gap-1.5">
          {symbol}
          {isIndex && (
            <span className="rounded bg-[#1e293b] px-1 py-0.2 text-[9px] font-mono text-[#38bdf8]">
              IDX
            </span>
          )}
        </span>
        <span className="wn">{name ?? (isIndex ? "NSE Index" : "NSE")}</span>
      </span>

      {/* Normal View: Price & Change */}
      <span className="wv">
        <span className={cn("wltp", tick ? toneCls(chg ?? 0) : "flat")}>
          {fmtNum(tick?.ltp ?? null)}
        </span>
        <span className={cn("wchg", tick && chg !== null ? tone : "flat")}>{fmtPct(chg)}</span>
      </span>

      {/* Kite-style Hover Action Buttons */}
      <div className="wact-bar">
        {!isIndex && (
          <>
            <button
              className="wbtn wbtn-buy"
              onClick={(e) => {
                e.stopPropagation()
                onBuy(fyersSymbol)
              }}
              title={`Buy ${symbol}`}
              type="button"
            >
              B
            </button>
            <button
              className="wbtn wbtn-sell"
              onClick={(e) => {
                e.stopPropagation()
                onSell(fyersSymbol)
              }}
              title={`Sell ${symbol}`}
              type="button"
            >
              S
            </button>
          </>
        )}
        <button
          aria-label={`Remove ${symbol} from watchlist`}
          className="wbtn wbtn-del"
          disabled={removing}
          onClick={(e) => {
            e.stopPropagation()
            onRemove()
          }}
          title="Remove from watchlist"
          type="button"
        >
          {removing ? "…" : "×"}
        </button>
      </div>
    </div>
  )
}

function KiteWatchlistFooter({
  selectedIdx,
  onSelect,
  count,
}: {
  selectedIdx: number
  onSelect: (idx: number) => void
  count: number
}) {
  const titles = ["Watchlist 1: Indices", "Watchlist 2", "Watchlist 3", "Watchlist 4"]
  return (
    <div className="kw-bar">
      <div className="kw-tabs" role="tablist" aria-label="Watchlist tabs">
        {[1, 2, 3, 4].map((num, idx) => {
          const isActive = selectedIdx === idx
          return (
            <button
              aria-selected={isActive}
              className={cn("kw-btn", isActive && "active")}
              key={num}
              onClick={() => onSelect(idx)}
              role="tab"
              title={titles[idx]}
              type="button"
            >
              {num}
            </button>
          )
        })}
      </div>
      <div className="kw-info">
        <span className="mono">{count}/50</span>
      </div>
    </div>
  )
}

/** Persistent watchlist aside with Zerodha Kite layout (design .watch). */
export function WatchlistSidebar() {
  const { openChart, chartSymbol } = useTradingAppContext()
  const navigate = useNavigate()
  const lists = useWatchlists()
  const createWatchlist = useCreateWatchlist()
  const updateWatchlist = useUpdateWatchlist()
  const { toast } = useToast()

  const sortedLists = useMemo(() => {
    return [...(lists.data ?? [])].sort((a, b) => a.created_at.localeCompare(b.created_at))
  }, [lists.data])

  const [selectedIdx, setSelectedIdx] = useState(0)

  // Align with active list on initial load
  useEffect(() => {
    if (sortedLists.length > 0) {
      const activeDbIdx = sortedLists.findIndex((l) => l.is_active)
      if (activeDbIdx >= 0 && activeDbIdx !== selectedIdx) {
        setSelectedIdx(activeDbIdx)
      }
    }
  }, [sortedLists])

  const currentList = sortedLists[selectedIdx] ?? sortedLists[0] ?? null
  const activeId = currentList?.id ?? null

  const items = useWatchlistItems(activeId)
  const remove = useRemoveWatchlistItem(activeId)
  const addMutation = useAddWatchlistItem(activeId)

  const handleSelectWatchlist = async (targetIdx: number) => {
    setSelectedIdx(targetIdx)
    const target = sortedLists[targetIdx]
    if (target) {
      if (!target.is_active) {
        updateWatchlist.mutate({ id: target.id, is_active: true })
      }
    } else {
      try {
        const created = await createWatchlist.mutateAsync({
          name: targetIdx === 0 ? "Indices" : `Watchlist ${targetIdx + 1}`,
        })
        updateWatchlist.mutate({ id: created.id, is_active: true })
      } catch {
        toast("bad", { title: `Failed to open ${targetIdx === 0 ? "Indices" : `Watchlist ${targetIdx + 1}`}` })
      }
    }
  }

  const [query, setQuery] = useState("")
  const [open, setOpen] = useState(false)
  const [cursor, setCursor] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const search = useInstrumentSearch(query, open)
  const hits = search.data ?? []

  const itemsList = items.data ?? []
  const existingSymbols = useMemo(() => new Set(itemsList.map((i) => i.fyers_symbol)), [itemsList])

  useEffect(() => {
    setCursor(0)
  }, [query])

  useEffect(() => {
    const onPointer = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    window.addEventListener("pointerdown", onPointer)
    return () => window.removeEventListener("pointerdown", onPointer)
  }, [])

  const handleAdd = useCallback(
    async (hit: InstrumentSearchHit) => {
      if (!activeId) {
        toast("warn", { title: "No active watchlist available" })
        return
      }
      if (existingSymbols.has(hit.fyers_symbol)) {
        toast("info", { title: `${hit.symbol} is already in the watchlist` })
        setQuery("")
        setOpen(false)
        return
      }
      try {
        await addMutation.mutateAsync(hit.fyers_symbol)
        toast("ok", { title: `${hit.symbol} added to ${selectedIdx === 0 ? "Indices" : `Watchlist ${selectedIdx + 1}`}` })
        setQuery("")
        setOpen(false)
      } catch {
        toast("bad", { title: `Failed to add ${hit.symbol} to watchlist` })
      }
    },
    [activeId, addMutation, existingSymbols, selectedIdx, toast],
  )

  const isIndicesTab = selectedIdx === 0
  const headerTitle = isIndicesTab
    ? "INDICES"
    : (currentList?.name?.toUpperCase() ?? `WATCHLIST ${selectedIdx + 1}`)

  return (
    <aside className="watch max-[720px]:w-full max-[720px]:border-r-0 max-[720px]:border-b max-[720px]:max-h-[320px]">
      <div className="whead">
        <h2>{headerTitle}</h2>
        <span className="mono">{itemsList.length} · {isIndicesTab ? "IDX" : "EQ"}</span>
      </div>

      {/* Broker-grade symbol search & add bar */}
      <div className="wadd-wrap" ref={containerRef}>
        <div className="wadd-bar">
          <SearchIcon aria-hidden="true" className="wadd-ic" />
          <input
            aria-label="Search and add symbol to watchlist"
            autoComplete="off"
            onChange={(event) => {
              setQuery(event.target.value)
              setOpen(event.target.value.trim().length > 0)
            }}
            onFocus={() => {
              if (query.trim().length > 0) setOpen(true)
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                setOpen(false)
                setQuery("")
                inputRef.current?.blur()
              } else if (event.key === "ArrowDown") {
                event.preventDefault()
                setCursor((prev) => Math.min(prev + 1, Math.max(0, hits.length - 1)))
              } else if (event.key === "ArrowUp") {
                event.preventDefault()
                setCursor((prev) => Math.max(prev - 1, 0))
              } else if (event.key === "Enter" && open && hits[cursor]) {
                event.preventDefault()
                void handleAdd(hits[cursor])
              }
            }}
            placeholder={isIndicesTab ? "+ Add index or symbol (e.g. NIFTY, BANK)" : "+ Add symbol (e.g. TCS, INFY)"}
            ref={inputRef}
            type="text"
            value={query}
          />
          {query.length > 0 && (
            <button
              aria-label="Clear search"
              className="wadd-clr"
              onClick={() => {
                setQuery("")
                setOpen(false)
                inputRef.current?.focus()
              }}
              type="button"
            >
              <XIcon className="size-3" />
            </button>
          )}
        </div>

        {open && (
          <div className="wadd-pop" role="listbox">
            {search.isLoading && (
              <div className="px-3 py-2 text-[11px] text-muted-text">Searching instruments…</div>
            )}
            {!search.isLoading && hits.length === 0 && (
              <div className="px-3 py-2 text-[11px] text-muted-text">No matching symbols</div>
            )}
            {hits.map((hit, index) => {
              const inList = existingSymbols.has(hit.fyers_symbol)
              return (
                <button
                  aria-selected={cursor === index}
                  className={cn("wadd-hit", cursor === index && "k")}
                  key={hit.id}
                  onClick={() => void handleAdd(hit)}
                  onMouseEnter={() => setCursor(index)}
                  type="button"
                >
                  <span className="min-w-0 flex-1">
                    <span className="wt">{hit.symbol}</span>
                    <span className="wn">{hit.name ?? hit.fyers_symbol}</span>
                  </span>
                  {inList ? (
                    <span className="added">✓ Added</span>
                  ) : (
                    <span className="wact">
                      <PlusIcon className="size-3" /> Add
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        )}
      </div>

      <div className="wlist">
        {items.isLoading && (
          <p className="px-2.5 py-3.5 text-[11.5px] text-muted-text">Loading {isIndicesTab ? "indices" : "watchlist"}…</p>
        )}
        {!items.isLoading && itemsList.length === 0 && (
          <p className="px-2.5 py-3.5 text-[11.5px] leading-relaxed text-muted-text">
            {isIndicesTab ? "Indices list empty — search and add indices above." : `Watchlist ${selectedIdx + 1} is empty — search and add symbols above.`}
          </p>
        )}
        {itemsList.map((item) => (
          <WatchRow
            active={chartSymbol === item.fyers_symbol}
            fyersSymbol={item.fyers_symbol}
            key={item.id}
            name={item.name}
            onBuy={(fyersSym) => navigate(`/?symbol=${encodeURIComponent(fyersSym)}&plan=1`)}
            onOpen={(fyersSym) => openChart(fyersSym)}
            onRemove={() => {
              void remove
                .mutateAsync(item.instrument_id)
                .then(() => toast("info", { title: `${item.symbol} removed from ${isIndicesTab ? "Indices" : `Watchlist ${selectedIdx + 1}`}` }))
            }}
            onSell={(fyersSym) => openChart(fyersSym)}
            removing={remove.isPending}
            symbol={item.symbol}
          />
        ))}
      </div>

      {/* Zerodha Kite-style 4 Numbered Watchlist Bar */}
      <KiteWatchlistFooter
        count={itemsList.length}
        onSelect={handleSelectWatchlist}
        selectedIdx={selectedIdx}
      />
    </aside>
  )
}
