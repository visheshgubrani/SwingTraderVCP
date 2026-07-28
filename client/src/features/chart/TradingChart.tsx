import {
  CrosshairMode,
  ColorType,
  CandlestickSeries,
  HistogramSeries,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type Time,
} from "lightweight-charts"
import {
  EraserIcon,
  MousePointer2Icon,
  SquareIcon,
  TrendingUpIcon,
  ScaleIcon,
} from "lucide-react"
import React, { useEffect, useRef, useState } from "react"

import { ChartDrawingController } from "@/features/chart/plugins/drawing-controller"
import type { DrawingTool } from "@/features/chart/plugins/types"
import { cn } from "@/lib/utils"

export interface CandleData {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume?: number
}

interface TradingChartProps {
  symbol: string
  data: CandleData[]
  stopLossPrice?: number
  targetPrice?: number
  liveLtp?: number
}

const drawingTools: {
  id: DrawingTool
  label: string
  icon: React.ComponentType<{ className?: string }>
}[] = [
  { id: "cursor", label: "Cursor", icon: MousePointer2Icon },
  { id: "trendline", label: "Trendline", icon: TrendingUpIcon },
  { id: "rectangle", label: "Rectangle", icon: SquareIcon },
  { id: "risk-reward", label: "Risk / Reward", icon: ScaleIcon },
]

export const TradingChart: React.FC<TradingChartProps> = ({
  symbol,
  data,
  stopLossPrice,
  targetPrice,
  liveLtp,
}) => {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null)
  const drawingControllerRef = useRef<ChartDrawingController | null>(null)
  const symbolRef = useRef(symbol)
  const [activeTool, setActiveTool] = useState<DrawingTool>("cursor")

  useEffect(() => {
    if (!chartContainerRef.current) return

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#080a0e" },
        textColor: "#8b949e",
        fontSize: 11,
        fontFamily: "'JetBrains Mono', monospace",
      },
      grid: {
        vertLines: { color: "#161b22" },
        horzLines: { color: "#161b22" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: "#3b82f6",
          width: 1,
          style: 3,
          labelBackgroundColor: "#1c2128",
        },
        horzLine: {
          color: "#3b82f6",
          width: 1,
          style: 3,
          labelBackgroundColor: "#1c2128",
        },
      },
      rightPriceScale: {
        borderColor: "#252932",
        scaleMargins: {
          top: 0.1,
          bottom: 0.2,
        },
      },
      timeScale: {
        borderColor: "#252932",
        timeVisible: false,
        secondsVisible: false,
      },
      handleScroll: true,
      handleScale: true,
    })

    chartRef.current = chart

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    })
    candleSeriesRef.current = candleSeries

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: {
        type: "volume",
      },
      priceScaleId: "volume",
    })
    volumeSeriesRef.current = volumeSeries

    chart.priceScale("volume").applyOptions({
      scaleMargins: {
        top: 0.8,
        bottom: 0,
      },
    })

    const controller = new ChartDrawingController()
    drawingControllerRef.current = controller
    controller.bind(chart, candleSeries, symbolRef.current, setActiveTool)

    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        chartRef.current.applyOptions({
          width: chartContainerRef.current.clientWidth,
          height: chartContainerRef.current.clientHeight,
        })
      }
    }

    const resizeObserver = new ResizeObserver(handleResize)
    resizeObserver.observe(chartContainerRef.current)

    return () => {
      controller.unbind()
      drawingControllerRef.current = null
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
      volumeSeriesRef.current = null
    }
  }, [])

  useEffect(() => {
    if (symbolRef.current === symbol) return
    symbolRef.current = symbol
    drawingControllerRef.current?.switchSymbol(symbol)
  }, [symbol])

  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current) return

    const candles = data.map((d) => ({
      time: d.time as Time,
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
    }))
    candleSeriesRef.current.setData(candles)

    const volumes = data.map((d) => ({
      time: d.time as Time,
      value: d.volume || 0,
      color:
        d.close >= d.open
          ? "rgba(34, 197, 94, 0.3)"
          : "rgba(239, 68, 68, 0.3)",
    }))
    volumeSeriesRef.current.setData(volumes)

    if (chartRef.current && data.length > 0) {
      candleSeriesRef.current.priceScale().applyOptions({ autoScale: true })
      chartRef.current.timeScale().fitContent()
    }
  }, [data, symbol])

  useEffect(() => {
    if (!candleSeriesRef.current) return

    const series = candleSeriesRef.current
    const lines: IPriceLine[] = []

    if (liveLtp && liveLtp > 0) {
      lines.push(
        series.createPriceLine({
          price: liveLtp,
          color: "#fbbf24",
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: "LTP",
        }),
      )
    }

    if (stopLossPrice && stopLossPrice > 0) {
      lines.push(
        series.createPriceLine({
          price: stopLossPrice,
          color: "#ef4444",
          lineWidth: 2,
          lineStyle: 0,
          axisLabelVisible: true,
          title: "STOP LOSS",
        }),
      )
    }

    if (targetPrice && targetPrice > 0) {
      lines.push(
        series.createPriceLine({
          price: targetPrice,
          color: "#10b981",
          lineWidth: 2,
          lineStyle: 0,
          axisLabelVisible: true,
          title: "TARGET",
        }),
      )
    }

    return () => {
      for (const line of lines) {
        try {
          series.removePriceLine(line)
        } catch (e) {
          console.warn(
            "Failed to remove price line (series may be disposed):",
            e,
          )
        }
      }
    }
  }, [stopLossPrice, targetPrice, liveLtp])

  const selectTool = (tool: DrawingTool) => {
    if (tool === activeTool && tool !== "cursor") {
      drawingControllerRef.current?.setActiveTool(tool)
      setActiveTool("cursor")
      return
    }
    setActiveTool(tool)
    drawingControllerRef.current?.setActiveTool(tool)
  }

  const clearDrawings = () => {
    drawingControllerRef.current?.clearDrawings()
  }

  return (
    <div className="relative flex h-full flex-col bg-[#080a0e] select-none">
      <div className="z-10 flex h-9 shrink-0 items-center justify-between border-b border-[#252932] bg-[#0d1117] px-3 font-mono text-xs">
        <div className="flex items-center gap-3">
          <span className="font-bold text-[#3b82f6]">{symbol}</span>
          <span className="rounded border border-[#252932] bg-[#080a0e] px-2 py-0.5 text-[11px] font-bold text-[#3b82f6]">
            1D
          </span>
          <div className="ml-2 flex items-center gap-1 border-l border-[#252932] pl-3">
            {drawingTools.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                aria-label={label}
                aria-pressed={activeTool === id}
                className={cn(
                  "inline-flex h-6 w-6 items-center justify-center rounded border transition-colors",
                  activeTool === id
                    ? "border-[#3b82f6] bg-[#3b82f6]/15 text-[#3b82f6]"
                    : "border-transparent text-[#8b949e] hover:border-[#252932] hover:bg-[#161b22] hover:text-[#d1d5db]",
                )}
                onClick={() => selectTool(id)}
                title={label}
                type="button"
              >
                <Icon className="h-3.5 w-3.5" />
              </button>
            ))}
            <button
              aria-label="Clear drawings"
              className="inline-flex h-6 w-6 items-center justify-center rounded border border-transparent text-[#8b949e] transition-colors hover:border-[#252932] hover:bg-[#161b22] hover:text-[#ef4444]"
              onClick={clearDrawings}
              title="Clear drawings"
              type="button"
            >
              <EraserIcon className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        <div className="flex items-center gap-2 text-[11px] text-[#8b949e]">
          {activeTool !== "cursor" ? (
            <span className="text-[#3b82f6]">
              {activeTool === "risk-reward"
                ? "Click entry → stop → target · Esc cancels"
                : "Click two points · Esc cancels"}
            </span>
          ) : (
            <span>POSTGRES EOD · LIVE LTP OVERLAY</span>
          )}
        </div>
      </div>

      <div
        ref={chartContainerRef}
        className="h-full min-h-[300px] w-full flex-1"
      />
    </div>
  )
}
