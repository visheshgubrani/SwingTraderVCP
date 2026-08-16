import { useEffect, useRef } from "react"
import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  PriceScaleMode,
  type IPriceLine,
  type ISeriesApi,
  type Time,
} from "lightweight-charts"

import { VisionOverlayPrimitive } from "@/features/chart/plugins/vision-overlay"
import type {
  VcpVisionContraction,
  VcpVisionFrozen,
} from "@/features/screener/vcpVision"
import { cn } from "@/lib/utils"

interface VcpVisionOverlayChartProps {
  frozen: VcpVisionFrozen
  contractions: VcpVisionContraction[]
  pivotPrice?: number | null
  className?: string
}

export function VcpVisionOverlayChart({
  frozen,
  contractions,
  pivotPrice,
  className,
}: VcpVisionOverlayChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ReturnType<typeof createChart> | null>(null)
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null)
  const primitiveRef = useRef<VisionOverlayPrimitive | null>(null)
  const pivotLineRef = useRef<IPriceLine | null>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#131722" },
        textColor: "#787b86",
        fontSize: 11,
        fontFamily: "'JetBrains Mono', system-ui, monospace",
      },
      grid: {
        vertLines: { color: "#1e222d" },
        horzLines: { color: "#1e222d" },
      },
      rightPriceScale: {
        borderColor: "#2a2e39",
        mode: PriceScaleMode.Logarithmic,
        scaleMargins: { top: 0.1, bottom: 0.25 },
      },
      timeScale: { borderColor: "#2a2e39", timeVisible: false },
      handleScroll: false,
      handleScale: false,
    })
    chartRef.current = chart

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#089981",
      downColor: "#f23645",
      borderVisible: false,
      wickUpColor: "#089981",
      wickDownColor: "#f23645",
    })
    seriesRef.current = candleSeries

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    })
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    })

    candleSeries.setData(
      frozen.candles.map((c) => ({
        time: c.date as Time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      })),
    )
    volumeSeries.setData(
      frozen.candles.map((c) => ({
        time: c.date as Time,
        value: c.volume,
        color: c.close >= c.open ? "#08998155" : "#f2364555",
      })),
    )
    chart.timeScale().fitContent()

    const primitive = new VisionOverlayPrimitive(chart, candleSeries, {
      bands: contractions.map((contraction) => ({
        label: contraction.label,
        start: contraction.start,
        end: contraction.end,
        high: contraction.high,
        low: contraction.low,
      })),
    })
    candleSeries.attachPrimitive(primitive)
    primitiveRef.current = primitive

    if (pivotPrice != null && pivotPrice > 0) {
      pivotLineRef.current = candleSeries.createPriceLine({
        price: pivotPrice,
        color: "#eab308",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "Pivot",
      })
    }

    return () => {
      if (pivotLineRef.current && seriesRef.current) {
        seriesRef.current.removePriceLine(pivotLineRef.current)
      }
      pivotLineRef.current = null
      primitiveRef.current = null
      seriesRef.current = null
      chart.remove()
      chartRef.current = null
    }
  }, [frozen, contractions, pivotPrice])

  return <div ref={containerRef} className={cn("h-full w-full", className)} />
}
