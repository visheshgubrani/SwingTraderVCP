"use client"

import { useEffect, useRef } from "react"
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  createChart,
  type IChartApi,
} from "lightweight-charts"

import type { DailyCandle } from "@/lib/scanner/types"

export function StockChartDark({
  candles,
  symbol,
  lastClose,
  dayChangePct,
  rangeLabel,
  isLiveData = true,
}: {
  candles: DailyCandle[]
  symbol: string
  lastClose: number
  dayChangePct: number
  rangeLabel: string
  isLiveData?: boolean
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!containerRef.current || candles.length === 0) return

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "rgba(255,255,255,0.7)",
        fontFamily: "var(--font-geist-mono), ui-monospace, monospace",
        fontSize: 12,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.05)" },
        horzLines: { color: "rgba(255,255,255,0.05)" },
      },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.1)" },
      timeScale: { borderColor: "rgba(255,255,255,0.1)", rightOffset: 2 },
      crosshair: { mode: CrosshairMode.Normal },
      localization: {
        priceFormatter: (p: number) => `₹${p.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`,
      },
    })

    const candlesSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#ffffff",
      downColor: "rgba(255,255,255,0.42)",
      borderVisible: false,
      wickUpColor: "#ffffff",
      wickDownColor: "rgba(255,255,255,0.42)",
    })
    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "" })

    candlesSeries.setData(
      candles.map((item) => ({
        time: item.time,
        open: item.open,
        high: item.high,
        low: item.low,
        close: item.close,
      })),
    )
    volume.setData(
      candles.map((item) => ({
        time: item.time,
        value: item.volume,
        color: item.close >= item.open ? "rgba(255,255,255,0.16)" : "rgba(255,255,255,0.07)",
      })),
    )
    chart.priceScale("").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    chart.timeScale().fitContent()
    chartRef.current = chart

    return () => {
      chartRef.current = null
      chart.remove()
    }
  }, [candles])

  if (candles.length === 0) {
    return (
      <div
        className="flex h-[300px] w-full items-center justify-center min-[901px]:h-[460px]"
        role="status"
      >
        <p className="max-w-[36ch] text-center text-sm text-[var(--landing-muted)]">
          Daily chart data is not available for this symbol right now.
        </p>
      </div>
    )
  }

  const up = dayChangePct >= 0
  const dataNote = isLiveData ? "End-of-day data." : "Illustrative preview data."
  const ariaLabel = `Daily price chart for ${symbol}. Last close ₹${lastClose.toLocaleString("en-IN")}, ${up ? "up" : "down"} ${Math.abs(dayChangePct).toFixed(1)}% in the ${rangeLabel} view. ${dataNote}`

  return (
    <div
      ref={containerRef}
      className="h-[300px] w-full min-[901px]:h-[460px]"
      role="img"
      aria-label={ariaLabel}
    />
  )
}
