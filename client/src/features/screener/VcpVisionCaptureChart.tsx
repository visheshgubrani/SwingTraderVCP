import { useEffect, useRef } from "react"
import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  PriceScaleMode,
  type IChartApi,
} from "lightweight-charts"

import type { VcpVisionCandle, VcpVisionFrozen } from "@/features/screener/vcpVision"

const WIDTH = 1280
const HEIGHT = 720

function screenshotBlob(source: HTMLCanvasElement): Promise<Blob> {
  const output = document.createElement("canvas")
  output.width = WIDTH
  output.height = HEIGHT
  const context = output.getContext("2d")
  if (!context) {
    return Promise.reject(new Error("Could not create the screenshot canvas."))
  }
  context.imageSmoothingEnabled = true
  context.imageSmoothingQuality = "high"
  context.drawImage(source, 0, 0, WIDTH, HEIGHT)
  return new Promise<Blob>((resolve, reject) => {
    output.toBlob((blob) => {
      if (blob) resolve(blob)
      else reject(new Error("Screenshot produced an empty blob."))
    }, "image/png")
  })
}

function chartData(candles: VcpVisionCandle[], limit: number) {
  const window = candles.slice(-limit)
  return {
    candles: window.map((c) => ({
      time: c.date as unknown as import("lightweight-charts").Time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    })),
    volumes: window.map((c) => ({
      time: c.date as unknown as import("lightweight-charts").Time,
      value: c.volume,
      color: c.close >= c.open ? "#22c55e55" : "#ef444455",
    })),
  }
}

interface VcpVisionCaptureChartProps {
  frozen: VcpVisionFrozen
  onCaptured: (contextBlob: Blob, detailBlob: Blob) => void
  onError: (message: string) => void
}

export function VcpVisionCaptureChart({
  frozen,
  onCaptured,
  onError,
}: VcpVisionCaptureChartProps) {
  const contextRef = useRef<HTMLDivElement>(null)
  const detailRef = useRef<HTMLDivElement>(null)
  const capturedRef = useRef(false)
  const onCapturedRef = useRef(onCaptured)
  const onErrorRef = useRef(onError)
  onCapturedRef.current = onCaptured
  onErrorRef.current = onError

  useEffect(() => {
    const contextContainer = contextRef.current
    const detailContainer = detailRef.current
    if (!contextContainer || !detailContainer) return
    capturedRef.current = false

    const contextChart = createChart(contextContainer, {
      width: WIDTH,
      height: HEIGHT,
      layout: {
        background: { type: ColorType.Solid, color: "#070b12" },
        textColor: "#8492a6",
      },
      grid: {
        vertLines: { color: "#1c2331" },
        horzLines: { color: "#1c2331" },
      },
      rightPriceScale: {
        borderColor: "#263246",
        mode: PriceScaleMode.Logarithmic,
      },
      timeScale: { borderColor: "#263246" },
    })

    const detailChart = createChart(detailContainer, {
      width: WIDTH,
      height: HEIGHT,
      layout: {
        background: { type: ColorType.Solid, color: "#070b12" },
        textColor: "#8492a6",
      },
      grid: {
        vertLines: { color: "#1c2331" },
        horzLines: { color: "#1c2331" },
      },
      rightPriceScale: {
        borderColor: "#263246",
        mode: PriceScaleMode.Logarithmic,
      },
      timeScale: { borderColor: "#263246" },
    })

    const buildSeries = (chart: IChartApi) => {
      const candleSeries = chart.addSeries(CandlestickSeries, {
        upColor: "#22c55e",
        downColor: "#ef4444",
        borderVisible: false,
        wickUpColor: "#22c55e",
        wickDownColor: "#ef4444",
      })
      const volumeSeries = chart.addSeries(HistogramSeries, {
        color: "#38bdf8",
        priceFormat: { type: "volume" },
        priceScaleId: "",
      })
      volumeSeries.priceScale().applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      })
      return { candleSeries, volumeSeries }
    }

    const context = buildSeries(contextChart)
    const detail = buildSeries(detailChart)

    const contextData = chartData(frozen.candles, frozen.context_sessions)
    const detailData = chartData(frozen.candles, frozen.detail_sessions)
    context.candleSeries.setData(contextData.candles)
    context.volumeSeries.setData(contextData.volumes)
    detail.candleSeries.setData(detailData.candles)
    detail.volumeSeries.setData(detailData.volumes)

    contextChart.timeScale().fitContent()
    detailChart.timeScale().fitContent()
    let disposed = false

    const capture = async () => {
      if (capturedRef.current) return
      try {
        // Two animation frames let the charts finish their layout pass so the
        // screenshot matches the final rendered frame.
        await new Promise((resolve) => requestAnimationFrame(resolve))
        await new Promise((resolve) => requestAnimationFrame(resolve))
        if (disposed || capturedRef.current) return
        const contextCanvas = contextChart.takeScreenshot()
        const detailCanvas = detailChart.takeScreenshot()
        // Lightweight Charts screenshots use bitmap pixels, so a DPR=2
        // display returns 2560x1440 for a 1280x720 chart. Normalize the final
        // artifact to the renderer contract before upload.
        const contextBlob = await screenshotBlob(contextCanvas)
        const detailBlob = await screenshotBlob(detailCanvas)
        if (disposed || capturedRef.current) return
        capturedRef.current = true
        onCapturedRef.current(contextBlob, detailBlob)
      } catch (err) {
        if (disposed) return
        onErrorRef.current(err instanceof Error ? err.message : "Screenshot failed.")
      }
    }

    void capture()

    return () => {
      disposed = true
      contextChart.remove()
      detailChart.remove()
    }
  }, [frozen])

  return (
    <>
      <div
        ref={contextRef}
        aria-hidden="true"
        className="pointer-events-none fixed left-[-9999px] top-0 opacity-0"
        style={{ width: WIDTH, height: HEIGHT }}
      />
      <div
        ref={detailRef}
        aria-hidden="true"
        className="pointer-events-none fixed left-[-9999px] top-0 opacity-0"
        style={{ width: WIDTH, height: HEIGHT }}
      />
    </>
  )
}
