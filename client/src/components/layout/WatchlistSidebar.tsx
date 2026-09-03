import { useEffect, useState } from "react"
import { useNavigate } from "react-router"

import { useToast } from "@/components/terminal/toast"
import {
  useRemoveWatchlistItem,
  useWatchlistItems,
  useWatchlists,
} from "@/features/watchlist/api"
import { useMarketData } from "@/lib/MarketWSContext"
import { NIFTY50_INDEX } from "@/lib/marketSymbols"
import { fmtNum, fmtPct, toneCls } from "@/lib/format"
import { cn } from "@/lib/utils"
import { useTradingAppContext } from "@/features/dashboard/app-context"

function WatchRow({
  symbol,
  fyersSymbol,
  name,
  onOpen,
  onRemove,
  active,
  removing,
}: {
  symbol: string
  fyersSymbol: string
  name: string | null
  onOpen: (symbol: string) => void
  onRemove: () => void
  active: boolean
  removing: boolean
}) {
  const { ltpMap } = useMarketData()
  const tick = ltpMap.get(fyersSymbol)
  const chg = tick?.change_pct ?? null
  const tone = toneCls(chg)

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
      title={`${symbol} · ${name ?? fyersSymbol}`}
    >
      <span className="ws">
        <span className="wt">{symbol}</span>
        <span className="wn">{name ?? fyersSymbol}</span>
      </span>
      <span className="wv">
        <span className={cn("wltp", tick ? toneCls(chg ?? 0) : "flat")}>
          {fmtNum(tick?.ltp ?? null)}
        </span>
        <span className={cn("wchg", tick && chg !== null ? tone : "flat")}>{fmtPct(chg)}</span>
      </span>
      {removing ? (
        <span className="wrm" style={{ display: "grid" }}>…</span>
      ) : (
        <span
          aria-label={`Remove ${symbol} from watchlist`}
          className="wrm"
          onClick={(event) => {
            event.stopPropagation()
            onRemove()
          }}
          role="button"
          title="Remove from watchlist"
        >
          ×
        </span>
      )}
    </div>
  )
}

function MarketsFooter() {
  const { ltpMap } = useMarketData()
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 15_000)
    return () => clearInterval(timer)
  }, [])

  const navigate = useNavigate()
  const bench = ltpMap.get(NIFTY50_INDEX)
  const pct = bench?.change_pct ?? null
  const tone = toneCls(pct)

  return (
    <div className="mx">
      <div className="mxhead">
        <span>MARKETS · NSE</span>
        <span className="mono">
          {now.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false })} IST
        </span>
      </div>
      <div>
        <button
          className="idx"
          onClick={() => {
            navigate(`/?symbol=${encodeURIComponent(NIFTY50_INDEX)}`)
          }}
          title="Nifty 50 — open chart"
          type="button"
        >
          <span className="t">NIFTY 50</span>
          <span className={cn("v", tone)}>{fmtNum(bench?.ltp ?? null)}</span>
          <span className={cn("c", tone)}>{fmtPct(pct)}</span>
        </button>
        {!bench && (
          <div className="px-2 py-1.5 text-[10px] text-muted-text">
            Index quotes appear when the tick feed is live.
          </div>
        )}
      </div>
    </div>
  )
}

/** Persistent watchlist aside + markets footer (design .watch). */
export function WatchlistSidebar() {
  const { openChart, chartSymbol } = useTradingAppContext()
  const lists = useWatchlists()
  const activeId = lists.data?.find((list) => list.is_active)?.id ?? lists.data?.[0]?.id ?? null
  const items = useWatchlistItems()
  const remove = useRemoveWatchlistItem(activeId)
  const { toast } = useToast()

  const itemsList = items.data ?? []

  return (
    <aside className="watch max-[720px]:w-full max-[720px]:border-r-0 max-[720px]:border-b max-[720px]:max-h-[232px]">
      <div className="whead">
        <h2>WATCHLIST</h2>
        <span className="mono">{lists.data ? `${itemsList.length} · EQ` : "…"}</span>
      </div>
      <div className="wlist">
        {items.isLoading && (
          <p className="px-2.5 py-3.5 text-[11.5px] text-muted-text">Loading watchlist…</p>
        )}
        {!items.isLoading && itemsList.length === 0 && (
          <p className="px-2.5 py-3.5 text-[11.5px] leading-relaxed text-muted-text">
            Watchlist empty — mark symbols with the heart in the scanner or use the search box.
          </p>
        )}
        {itemsList.map((item) => (
          <WatchRow
            active={chartSymbol === item.fyers_symbol}
            fyersSymbol={item.fyers_symbol}
            key={item.id}
            name={item.name}
            onOpen={(symbol) => openChart(symbol)}
            onRemove={() => {
              void remove
                .mutateAsync(item.instrument_id)
                .then(() => toast("info", { title: `${item.symbol} removed from watchlist` }))
            }}
            removing={remove.isPending}
            symbol={item.symbol}
          />
        ))}
      </div>
      <MarketsFooter />
    </aside>
  )
}
