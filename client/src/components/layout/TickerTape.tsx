import { useMemo } from "react"

import { useMarketData } from "@/lib/MarketWSContext"
import { shortSymbol, NIFTY50_INDEX } from "@/lib/marketSymbols"
import { fmtNum, fmtPct, toneCls } from "@/lib/format"
import { useWatchlistItems } from "@/features/watchlist/api"
import { cn } from "@/lib/utils"

interface TapeEntry {
  symbol: string
  label: string
  value: number | null
  pct: number | null
}

function TapeCell({ entry }: { entry: TapeEntry }) {
  const tone = toneCls(entry.pct)
  return (
    <span className="tape-item">
      <span className="t">{entry.label}</span>
      <span className="v">{fmtNum(entry.value)}</span>
      <span className={cn("c", tone)}>{fmtPct(entry.pct)}</span>
    </span>
  )
}

/** Scrolling ticker tape of benchmarks + watchlist quotes (design .tape). */
export function TickerTape() {
  const { ltpMap } = useMarketData()
  const itemsQuery = useWatchlistItems()

  const entries = useMemo(() => {
    const list: TapeEntry[] = []
    const bench = ltpMap.get(NIFTY50_INDEX)
    if (bench) {
      list.push({ symbol: NIFTY50_INDEX, label: "NIFTY 50", value: bench.ltp, pct: bench.change_pct ?? null })
    }
    for (const item of itemsQuery.data ?? []) {
      if (list.length >= 9) break
      const tick = ltpMap.get(item.fyers_symbol)
      list.push({
        symbol: item.fyers_symbol,
        label: shortSymbol(item.fyers_symbol),
        value: tick?.ltp ?? null,
        pct: tick?.change_pct ?? null,
      })
    }
    return list
  }, [itemsQuery.data, ltpMap])

  if (entries.length === 0) return null

  return (
    <div className="tape max-[560px]:hidden" aria-hidden="true">
      <div className="tape-in">
        {entries.map((entry) => (
          <TapeCell entry={entry} key={`a-${entry.symbol}`} />
        ))}
        <span style={{ width: 34 }} />
        {entries.map((entry) => (
          <TapeCell entry={entry} key={`b-${entry.symbol}`} />
        ))}
      </div>
    </div>
  )
}
