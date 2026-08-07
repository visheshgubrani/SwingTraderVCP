import { useEffect, useRef } from "react"
import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  type IChartApi,
} from "lightweight-charts"

import type { ChartArtifactClaim } from "@/features/journal/api"

const WIDTH = 1280
const HEIGHT = 720

interface EntryCaptureChartProps {
  artifact: ChartArtifactClaim
  onCaptured: (blob: Blob) => void
  onError: (message: string) => void
}

export function EntryCaptureChart({
  artifact,
  onCaptured,
  onError,
}: EntryCaptureChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const chart = createChart(container, {
      width: WIDTH,
      height: HEIGHT,
      layout: {
        background: { type: ColorType.Solid, color: "#080a0e" },
        textColor: "#8b949e",
      },
      grid: {
        vertLines: { color: "#161b22" },
        horzLines: { color: "#161b22" },
      },
      rightPriceScale: { borderColor: "#252932" },
      timeScale: { borderColor: "#252932" },
    })

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    })

    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: "#3b82f6",
      priceFormat: { type: "volume" },
      priceScaleId: "",
    })
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    })

    const { candles, entry_price, stop_loss, target } = artifact.chart_source
    candleSeries.setData(
      candles.map((c) => ({
        time: c.time as unknown as import("lightweight-charts").Time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      })),
    )
    volumeSeries.setData(
      candles.map((c) => ({
        time: c.time as unknown as import("lightweight-charts").Time,
        value: c.volume,
        color: c.close >= c.open ? "#22c55e55" : "#ef444455",
      })),
    )

    if (entry_price) {
      candleSeries.createPriceLine({
        price: Number(entry_price),
        color: "#eab308",
        lineWidth: 2,
        title: "Entry",
      })
    }
    if (stop_loss) {
      candleSeries.createPriceLine({
        price: Number(stop_loss),
        color: "#ef4444",
        lineWidth: 2,
        title: "SL",
      })
    }
    if (target) {
      candleSeries.createPriceLine({
        price: Number(target),
        color: "#22c55e",
        lineWidth: 2,
        title: "Target",
      })
    }

    chart.timeScale().fitContent()
    chartRef.current = chart

    const capture = async () => {
      try {
        await new Promise((resolve) => requestAnimationFrame(resolve))
        const canvas = chart.takeScreenshot()
        canvas.toBlob(
          (blob) => {
            if (!blob) {
              onError("Screenshot produced empty blob.")
              return
            }
            onCaptured(blob)
          },
          "image/png",
          1,
        )
      } catch (err) {
        onError(err instanceof Error ? err.message : "Screenshot failed.")
      }
    }

    void capture()

    return () => {
      chart.remove()
      chartRef.current = null
    }
  }, [artifact, onCaptured, onError])

  return (
    <div
      ref={containerRef}
      aria-hidden="true"
      className="pointer-events-none fixed left-[-9999px] top-0 opacity-0"
      style={{ width: WIDTH, height: HEIGHT }}
    />
  )
}
