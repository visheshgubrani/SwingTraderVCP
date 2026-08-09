import { sparklinePath, sparklineSeries } from "@/lib/scanner/sparkline"

export function TrendSparkline({
  close,
  dayChangePct,
  sparkSeed = 0,
  sparkSeries,
}: {
  close: number
  dayChangePct: number
  sparkSeed?: number
  sparkSeries?: number[]
}) {
  const series =
    sparkSeries && sparkSeries.length >= 2
      ? sparkSeries
      : sparklineSeries(close, dayChangePct, sparkSeed)
  const { line, area } = sparklinePath(series)
  const up = dayChangePct >= 0
  const stroke = up ? "rgba(255,255,255,0.7)" : "rgba(255,255,255,0.5)"
  const fill = up ? "rgba(255,255,255,0.08)" : "rgba(255,255,255,0.05)"

  if (!line) return null

  return (
    <svg viewBox="0 0 96 30" preserveAspectRatio="none" className="h-[30px] w-full" aria-hidden>
      <path d={area} fill={fill} />
      <polyline points={line} fill="none" stroke={stroke} strokeWidth="1.5" />
    </svg>
  )
}
