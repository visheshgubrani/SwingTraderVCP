import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useSearchParams } from "react-router"
import { PanelLeftCloseIcon, PlusIcon, SearchIcon, StarIcon, XIcon } from "lucide-react"

import { GradeChip } from "@/components/terminal/bits"
import { useToast } from "@/components/terminal/toast"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  WATCHLIST_TABS,
  watchlistTitle,
  type ChartListItem,
  type ChartListKey,
  type ChartListState,
} from "@/features/chart/chartList"
import {
  SCAN_GRADES,
  SCAN_SORT_LABEL,
  formatScanRun,
  resolveSort,
  type ScanSortKey,
} from "@/features/screener/scanList"
import {
  useAddWatchlistItem,
  useCreateWatchlist,
  useInstrumentSearch,
  useRemoveWatchlistItem,
  useUpdateWatchlist,
  type InstrumentSearchHit,
  type WatchlistItemView,
} from "@/features/watchlist/api"
import { useMarketData } from "@/lib/MarketWSContext"
import { fmtNum, fmtPct, toneCls } from "@/lib/format"
import { cn } from "@/lib/utils"

/** Update the chart symbol in place (keeps the list context, no new history entry). */
function useOpenInPlace() {
  const [, setSearchParams] = useSearchParams()
  return useCallback(
    (fyersSymbol: string, plan?: "buy" | "sell") => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.set("symbol", fyersSymbol)
          if (plan) next.set("plan", plan)
          return next
        },
        { replace: !plan },
      )
    },
    [setSearchParams],
  )
}

function useScrollActiveIntoView(activeSymbol: string | null) {
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!activeSymbol) return
    const row = listRef.current?.querySelector<HTMLElement>('[aria-current="true"]')
    row?.scrollIntoView({ block: "nearest" })
  }, [activeSymbol])
  return listRef
}

/* ── scan list ── */

function ScanRow({ item, active, onOpen }: { item: ChartListItem; active: boolean; onOpen: () => void }) {
  const row = item.scan!
  const off52w = Number.isFinite(row.pct_from_52w_high) ? Math.abs(row.pct_from_52w_high * 100) : null
  return (
    <button
      aria-current={active ? "true" : undefined}
      className={cn("wrow", active && "on")}
      onClick={onOpen}
      title={`${row.symbol} · ${row.name ?? row.fyers_symbol}`}
      type="button"
    >
      <span className="ws min-w-0">
        <span className="wt flex items-center gap-1.5">
          <span className="font-mono text-2xs font-medium text-muted-text">{row.rank}</span>
          <span className="truncate">{row.symbol}</span>
          {row.fundamental_selected && (
            <StarIcon aria-label="Fundamental shortlist" className="size-3 flex-none fill-current text-wa" />
          )}
          {row.vcp_vision?.ai_verdict === "valid" && (
            <span aria-label="VCP vision: valid" className="size-1.5 flex-none rounded-full bg-accent" />
          )}
        </span>
        <span className="wn">{row.name ?? row.fyers_symbol}</span>
      </span>
      <span className="text-right">
        <span className="wltp flex items-center justify-end gap-1.5 text-fg2">
          {row.technical_score?.toFixed(1) ?? "—"}
          <GradeChip grade={row.score_grade} />
        </span>
        <span className="wchg flat">
          RS {row.rs_rating} · {off52w === null ? "—" : `${off52w.toFixed(1)}%`}
        </span>
      </span>
    </button>
  )
}

function ScanListPanel({ chartList, activeSymbol }: { chartList: ChartListState; activeSymbol: string | null }) {
  const openInPlace = useOpenInPlace()
  const { scan, params, setParams, items } = chartList
  const listRef = useScrollActiveIntoView(activeSymbol)
  const sort = resolveSort(params.sort, params.dir)

  return (
    <>
      <div className="flex flex-none flex-col gap-1.5 px-2 pt-2 pb-2">
        <select
          aria-label="Scan run"
          className="filter-select w-full"
          disabled={scan.runs.length === 0}
          onChange={(event) => setParams({ run: event.target.value })}
          value={scan.runId ?? ""}
        >
          {scan.runs.length === 0 && <option value="">No scanner runs</option>}
          {scan.runs.map((run) => (
            <option key={run.id} value={run.id}>
              {formatScanRun(run)}
            </option>
          ))}
        </select>
        <div className="wadd-bar">
          <SearchIcon aria-hidden="true" className="wadd-ic" />
          <input
            aria-label="Filter scan results"
            onChange={(event) => setParams({ q: event.target.value })}
            placeholder="Filter symbol or name"
            type="text"
            value={params.q}
          />
          {params.q && (
            <button aria-label="Clear filter" className="wadd-clr" onClick={() => setParams({ q: null })} type="button">
              <XIcon className="size-3" />
            </button>
          )}
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          <select
            aria-label="Filter by grade"
            className="filter-select w-full"
            onChange={(event) => setParams({ grade: event.target.value })}
            value={params.grade}
          >
            {SCAN_GRADES.map((grade) => (
              <option key={grade} value={grade}>
                {grade === "ALL" ? "Any grade" : `Grade ${grade}`}
              </option>
            ))}
          </select>
          <select
            aria-label="Sort scan results"
            className="filter-select w-full"
            onChange={(event) => setParams({ sort: event.target.value, dir: null })}
            value={sort.key}
          >
            {(Object.keys(SCAN_SORT_LABEL) as ScanSortKey[]).map((key) => (
              <option key={key} value={key}>
                Sort: {SCAN_SORT_LABEL[key]}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="wlist" ref={listRef}>
        {chartList.isLoading && <p className="px-2.5 py-3.5 text-xs text-muted-text">Loading scan results…</p>}
        {!chartList.isLoading && scan.run && scan.run.status !== "succeeded" && (
          <p className="px-2.5 py-3.5 text-xs text-muted-text">This scan run is {scan.run.status}.</p>
        )}
        {!chartList.isLoading && scan.run?.status === "succeeded" && items.length === 0 && (
          <p className="px-2.5 py-3.5 text-xs text-muted-text">
            {params.q || params.grade !== "ALL" ? "No setups match these filters." : "This run has no setups."}
          </p>
        )}
        {!chartList.isLoading && scan.runs.length === 0 && (
          <p className="px-2.5 py-3.5 text-xs text-muted-text">No scan yet — run the EOD scan from the Scanner tab.</p>
        )}
        {items.map((item) => (
          <ScanRow
            active={item.fyers_symbol === activeSymbol}
            item={item}
            key={item.fyers_symbol}
            onOpen={() => openInPlace(item.fyers_symbol)}
          />
        ))}
      </div>
    </>
  )
}

/* ── watchlists ── */

function WatchRow({
  item,
  active,
  removing,
  onOpen,
  onPlan,
  onRemove,
}: {
  item: WatchlistItemView
  active: boolean
  removing: boolean
  onOpen: () => void
  onPlan: (side: "buy" | "sell") => void
  onRemove: () => void
}) {
  const { ltpMap } = useMarketData()
  const tick = ltpMap.get(item.fyers_symbol)
  const chg = tick?.change_pct ?? null
  const isIndex = item.fyers_symbol.includes("INDEX")

  return (
    <div
      aria-current={active ? "true" : undefined}
      className={cn("wrow", active && "on")}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault()
          onOpen()
        }
      }}
      role="button"
      tabIndex={0}
      title={`${item.symbol} · ${item.name ?? item.fyers_symbol}`}
    >
      <span className="ws min-w-0">
        <span className="wt flex items-center gap-1.5">
          <span className="truncate">{item.symbol}</span>
          {isIndex && <span className="rounded bg-hl-2 px-1 font-mono text-2xs text-accent">IDX</span>}
        </span>
        <span className="wn">{item.name ?? (isIndex ? "NSE Index" : "NSE")}</span>
      </span>

      <span className="wv">
        <span className={cn("wltp", tick ? toneCls(chg ?? 0) : "flat")}>{fmtNum(tick?.ltp ?? null)}</span>
        <span className={cn("wchg", tick && chg !== null ? toneCls(chg) : "flat")}>{fmtPct(chg)}</span>
      </span>

      <div className="wact-bar">
        {!isIndex && (
          <>
            <button
              className="wbtn wbtn-buy"
              onClick={(event) => {
                event.stopPropagation()
                onPlan("buy")
              }}
              title={`Buy ticket for ${item.symbol}`}
              type="button"
            >
              B
            </button>
            <button
              className="wbtn wbtn-sell"
              onClick={(event) => {
                event.stopPropagation()
                onPlan("sell")
              }}
              title={`Sell ticket for ${item.symbol}`}
              type="button"
            >
              S
            </button>
          </>
        )}
        <button
          aria-label={`Remove ${item.symbol} from watchlist`}
          className="wbtn wbtn-del"
          disabled={removing}
          onClick={(event) => {
            event.stopPropagation()
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

function WatchlistPanel({ chartList, activeSymbol }: { chartList: ChartListState; activeSymbol: string | null }) {
  const { toast } = useToast()
  const openInPlace = useOpenInPlace()
  const { watchlist, watchlistIndex, watchlistItems } = chartList
  const watchlistId = watchlist?.id ?? null
  const remove = useRemoveWatchlistItem(watchlistId)
  const addMutation = useAddWatchlistItem(watchlistId)
  const listRef = useScrollActiveIntoView(activeSymbol)
  const title = watchlistTitle(watchlistIndex, watchlist)
  const isIndices = watchlistIndex === 0

  const [query, setQuery] = useState("")
  const [open, setOpen] = useState(false)
  const [cursor, setCursor] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const search = useInstrumentSearch(query, open)
  const hits = search.data ?? []
  const items = useMemo(() => watchlistItems.data ?? [], [watchlistItems.data])
  const existing = useMemo(() => new Set(items.map((item) => item.fyers_symbol)), [items])

  useEffect(() => setCursor(0), [query])

  useEffect(() => {
    const onPointer = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) setOpen(false)
    }
    window.addEventListener("pointerdown", onPointer)
    return () => window.removeEventListener("pointerdown", onPointer)
  }, [])

  const handleAdd = async (hit: InstrumentSearchHit) => {
    if (!watchlistId) {
      toast("warn", { title: "This watchlist is still being created — try again in a moment." })
      return
    }
    if (existing.has(hit.fyers_symbol)) {
      toast("info", { title: `${hit.symbol} is already in ${title}` })
    } else {
      try {
        await addMutation.mutateAsync(hit.fyers_symbol)
        toast("ok", { title: `${hit.symbol} added to ${title}` })
      } catch {
        toast("bad", { title: `Failed to add ${hit.symbol} to ${title}` })
        return
      }
    }
    setQuery("")
    setOpen(false)
  }

  return (
    <>
      <div className="wadd-wrap pt-2" ref={containerRef}>
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
            placeholder={isIndices ? "Add index (e.g. NIFTY, BANK)" : "Add symbol (e.g. TCS, INFY)"}
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
            {search.isLoading && <div className="px-3 py-2 text-xs text-muted-text">Searching instruments…</div>}
            {!search.isLoading && hits.length === 0 && (
              <div className="px-3 py-2 text-xs text-muted-text">No matching symbols</div>
            )}
            {hits.map((hit, index) => (
              <button
                aria-selected={cursor === index}
                className={cn("wadd-hit", cursor === index && "k")}
                key={hit.id}
                onClick={() => void handleAdd(hit)}
                onMouseEnter={() => setCursor(index)}
                role="option"
                type="button"
              >
                <span className="min-w-0 flex-1">
                  <span className="wt">{hit.symbol}</span>
                  <span className="wn">{hit.name ?? hit.fyers_symbol}</span>
                </span>
                {existing.has(hit.fyers_symbol) ? (
                  <span className="added">✓ Added</span>
                ) : (
                  <span className="wact">
                    <PlusIcon className="size-3" /> Add
                  </span>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="wlist" ref={listRef}>
        {chartList.isLoading && <p className="px-2.5 py-3.5 text-xs text-muted-text">Loading {title}…</p>}
        {!chartList.isLoading && items.length === 0 && (
          <p className="px-2.5 py-3.5 text-xs leading-relaxed text-muted-text">
            {title} is empty — search above to add {isIndices ? "indices" : "symbols"}.
          </p>
        )}
        {items.map((item) => (
          <WatchRow
            active={activeSymbol === item.fyers_symbol}
            item={item}
            key={item.id}
            onOpen={() => openInPlace(item.fyers_symbol)}
            onPlan={(side) => openInPlace(item.fyers_symbol, side)}
            onRemove={() => {
              void remove
                .mutateAsync(item.instrument_id)
                .then(() => toast("info", { title: `${item.symbol} removed from ${title}` }))
                .catch(() => toast("bad", { title: `Failed to remove ${item.symbol}` }))
            }}
            removing={remove.isPending && remove.variables === item.instrument_id}
          />
        ))}
      </div>
    </>
  )
}

/* ── sidebar shell ── */

/** Chart-page list: the scan (default) or Kite-style watchlists 1–4. */
export function ChartSidebar({
  chartList,
  activeSymbol,
  onCollapse,
}: {
  chartList: ChartListState
  activeSymbol: string | null
  onCollapse: () => void
}) {
  const { toast } = useToast()
  const createWatchlist = useCreateWatchlist()
  const updateWatchlist = useUpdateWatchlist()
  const { list, setList, watchlists, items, scan } = chartList

  // Watchlist tabs map onto the server watchlists by creation order; the chosen
  // one becomes the server "active" list (its symbols stay subscribed to LTP).
  const selectTab = async (key: ChartListKey) => {
    setList(key)
    if (key === "scan") return
    const index = Number(key.slice(2)) - 1
    const target = watchlists[index]
    if (target) {
      if (!target.is_active) updateWatchlist.mutate({ id: target.id, is_active: true })
      return
    }
    try {
      const created = await createWatchlist.mutateAsync({ name: watchlistTitle(index) })
      updateWatchlist.mutate({ id: created.id, is_active: true })
    } catch {
      toast("bad", { title: `Failed to open ${watchlistTitle(index)}` })
    }
  }

  const tabs: { key: ChartListKey; label: string; title: string }[] = [
    { key: "scan", label: "Scan", title: "Latest scan results" },
    ...WATCHLIST_TABS.map((key, index) => ({
      key,
      label: String(index + 1),
      title: watchlistTitle(index, watchlists[index]),
    })),
  ]

  const heading = list === "scan" ? "Scan results" : chartList.label
  const count = list === "scan" ? `${items.length} / ${scan.results.length}` : String(items.length)

  return (
    <aside className="watch max-[720px]:max-h-[320px] max-[720px]:w-full max-[720px]:border-r-0 max-[720px]:border-b">
      <div aria-label="Chart lists" className="stabs" role="tablist">
        {tabs.map((tab) => (
          <Tooltip key={tab.key}>
            <TooltipTrigger
              render={
                <button
                  aria-selected={list === tab.key}
                  className={cn("stab", list === tab.key && "on")}
                  onClick={() => void selectTab(tab.key)}
                  role="tab"
                  type="button"
                />
              }
            >
              {tab.label}
            </TooltipTrigger>
            <TooltipContent side="bottom">{tab.title}</TooltipContent>
          </Tooltip>
        ))}
        <button aria-label="Hide list" className="ibtn ml-auto" onClick={onCollapse} title="Hide list" type="button">
          <PanelLeftCloseIcon className="size-4" />
        </button>
      </div>

      <div className="whead">
        <h2>{heading.toUpperCase()}</h2>
        <span className="mono">{count}</span>
      </div>

      {list === "scan" ? (
        <ScanListPanel activeSymbol={activeSymbol} chartList={chartList} />
      ) : (
        <WatchlistPanel activeSymbol={activeSymbol} chartList={chartList} />
      )}

      <div className="flex-none border-t border-border-soft px-3 py-1.5 text-2xs text-muted-text">
        <kbd className="font-mono">↑</kbd> <kbd className="font-mono">↓</kbd> or <kbd className="font-mono">J</kbd>{" "}
        <kbd className="font-mono">K</kbd> to flip charts
      </div>
    </aside>
  )
}
