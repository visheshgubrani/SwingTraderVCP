import { sparklinePath, sparklineSeries } from "@/lib/scanner/sparkline"
import type { DailyCandle } from "@/lib/scanner/types"

function closesFromCandles(candles: DailyCandle[] | undefined): number[] | null {
  if (!candles || candles.length < 2) return null
  return candles.map((bar) => bar.close)
}

export function TrendSparkline({
  close,
  dayChangePct,
  sparkSeed = 0,
  sparkSeries,
  candles,
  isLiveData = false,
}: {
  close: number
  dayChangePct: number
  sparkSeed?: number
  sparkSeries?: number[]
  candles?: DailyCandle[]
  isLiveData?: boolean
}) {
  const series =
    sparkSeries && sparkSeries.length >= 2
      ? sparkSeries
      : closesFromCandles(candles) ??
        (isLiveData ? null : sparklineSeries(close, dayChangePct, sparkSeed))

  if (!series || series.length < 2) {
    return (
      <span
        className="block h-[30px] w-full font-[family-name:var(--font-landing-mono)] text-[10px] uppercase tracking-wider text-[var(--landing-muted)]"
        aria-hidden
      />
    )
  }

  const { line, area } = sparklinePath(series)
  const up = dayChangePct >= 0
  const stroke = up ? "rgba(255,255,255,0.7)" : "rgba(255,255,255,0.5)"
  const fill = up ? "rgba(255,255,255,0.08)" : "rgba(255,255,255,0.05)"

  if (!line) return null

  return (
    <svg
      viewBox="0 0 96 30"
      preserveAspectRatio="none"
      className="h-[30px] w-full"
      aria-label="Daily close trend"
      role="img"
    >
      <path d={area} fill={fill} />
      <polyline points={line} fill="none" stroke={stroke} strokeWidth="1.5" />
    </svg>
  )
}
