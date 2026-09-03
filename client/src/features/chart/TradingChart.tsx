import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  PriceScaleMode,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type LogicalRange,
  type MouseEventParams,
  type Time,
} from "lightweight-charts"
import {
  CandlestickChartIcon,
  EraserIcon,
  LineChartIcon,
  MinusIcon,
  MousePointer2Icon,
  RefreshCwIcon,
  RulerIcon,
  ScaleIcon,
  SquareIcon,
  TrendingUpIcon,
} from "lucide-react"
import React, { useEffect, useRef, useState } from "react"

import { DrawingPropertiesBar } from "@/features/chart/DrawingPropertiesBar"
import { ChartDrawingController } from "@/features/chart/plugins/drawing-controller"
import type { DrawingRecord, DrawingStyle, DrawingTool } from "@/features/chart/plugins/types"
import {
  VisionOverlayPrimitive,
  type VisionContractionBand,
} from "@/features/chart/plugins/vision-overlay"
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
  volumeVisible?: boolean
  smaOverlays?: { period: number; color: string }[]
  visionOverlay?: {
    contractions: VisionContractionBand[]
    pivotPrice?: number | null
  } | null
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
  { id: "horizontal-line", label: "Horizontal Line", icon: MinusIcon },
  { id: "price-range", label: "Price Range Ruler", icon: RulerIcon },
]

const FUTURE_WHITESPACE_SLOTS = 120
const INITIAL_RIGHT_SLOTS = 24

function nextWeekdays(lastDate: string, count: number): Time[] {
  const dates: Time[] = []
  const cursor = new Date(`${lastDate}T00:00:00Z`)
  while (dates.length < count) {
    cursor.setUTCDate(cursor.getUTCDate() + 1)
    const day = cursor.getUTCDay()
    if (day !== 0 && day !== 6) dates.push(cursor.toISOString().slice(0, 10) as Time)
  }
  return dates
}

function seriesData(data: CandleData[], type: "candlestick" | "line" | "area") {
  if (type === "candlestick") {
    return data.map((candle) => ({ time: candle.time as Time, open: candle.open, high: candle.high, low: candle.low, close: candle.close }))
  }
  return data.map((candle) => ({ time: candle.time as Time, value: candle.close }))
}

export const TradingChart: React.FC<TradingChartProps> = ({
  symbol,
  data,
  stopLossPrice,
  targetPrice,
  liveLtp,
  volumeVisible = true,
  smaOverlays = [],
  visionOverlay,
}) => {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick" | "Line" | "Area"> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null)
  const smaSeriesRef = useRef<Map<number, ISeriesApi<"Line">>>(new Map())
  const futureSeriesRef = useRef<ISeriesApi<"Line"> | null>(null)
  const drawingControllerRef = useRef<ChartDrawingController | null>(null)
  const symbolRef = useRef(symbol)
  const renderedSymbolRef = useRef("")
  const dataRef = useRef(data)
  const chartTypeRef = useRef<"candlestick" | "line" | "area">("candlestick")
  const priceLinesRef = useRef<Record<"ltp" | "stop" | "target", IPriceLine | null>>({ ltp: null, stop: null, target: null })
  const visionPrimitiveRef = useRef<VisionOverlayPrimitive | null>(null)
  const visionPivotLineRef = useRef<IPriceLine | null>(null)
  const visionSeriesRef = useRef<ISeriesApi<"Candlestick" | "Line" | "Area"> | null>(null)
  const hoverFrameRef = useRef<number | null>(null)

  const [activeTool, setActiveTool] = useState<DrawingTool>("cursor")
  const [selectedRecord, setSelectedRecord] = useState<DrawingRecord | null>(null)
  const [chartType, setChartType] = useState<"candlestick" | "line" | "area">("candlestick")
  const [isLogScale, setIsLogScale] = useState(false)
  const [isAutoScale, setIsAutoScale] = useState(true)
  const [showVisionOverlay, setShowVisionOverlay] = useState(false)

  // OHLCV Legend Hover Overlay State
  const [hoverCandle, setHoverCandle] = useState<{
    open: number
    high: number
    low: number
    close: number
    volume?: number
    change: number
    changePct: number
  } | null>(null)

  // Chart initialization effect
  useEffect(() => {
    if (!chartContainerRef.current) return

    const chart = createChart(chartContainerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#070b12" },
        textColor: "#8492a6",
        fontSize: 11,
        fontFamily: "'Roboto Mono', system-ui, monospace",
      },
      grid: {
        vertLines: { color: "#101826" },
        horzLines: { color: "#101826" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: "#38bdf8",
          width: 1,
          style: 3,
          labelBackgroundColor: "#263246",
        },
        horzLine: {
          color: "#38bdf8",
          width: 1,
          style: 3,
          labelBackgroundColor: "#263246",
        },
      },
      rightPriceScale: {
        borderColor: "#263246",
        scaleMargins: { top: 0.1, bottom: 0.2 },
        autoScale: true,
      },
      timeScale: {
        borderColor: "#263246",
        timeVisible: false,
        secondsVisible: false,
        shiftVisibleRangeOnNewBar: false,
        rightOffset: INITIAL_RIGHT_SLOTS,
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: true,
      },
      handleScale: {
        axisPressedMouseMove: { time: true, price: true },
        mouseWheel: true,
        pinch: true,
      },
      kineticScroll: {
        touch: true,
        mouse: true,
      },
    })

    chartRef.current = chart

    // Add initial Candlestick series
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    })
    candleSeriesRef.current = candleSeries

    // An invisible whitespace series makes future weekday coordinates real
    // chart coordinates, so every drawing tool can extend past the last bar.
    const futureSeries = chart.addSeries(LineSeries, {
      color: "transparent",
      lineVisible: false,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    futureSeriesRef.current = futureSeries

    // Bind Drawing Controller
    const controller = new ChartDrawingController()
    drawingControllerRef.current = controller
    controller.bind(
      chart,
      candleSeries,
      chartContainerRef.current,
      symbolRef.current,
      setActiveTool,
      setSelectedRecord,
    )

    // Crosshair Move OHLC Overlay Listener
    const handleCrosshairMove = (param: MouseEventParams<Time>) => {
      if (!param.time || !param.seriesData) {
        if (hoverFrameRef.current !== null) cancelAnimationFrame(hoverFrameRef.current)
        hoverFrameRef.current = requestAnimationFrame(() => setHoverCandle(null))
        return
      }

      const activeSeries = candleSeriesRef.current
      if (!activeSeries) return
      const dataPoint = param.seriesData.get(activeSeries) as
        | { open: number; high: number; low: number; close: number }
        | { value: number }
        | undefined

      const volPoint = volumeSeriesRef.current
        ? (param.seriesData.get(volumeSeriesRef.current) as { value: number } | undefined)
        : undefined

      if (dataPoint && "open" in dataPoint) {
        const change = dataPoint.close - dataPoint.open
        const changePct =
          dataPoint.open > 0 ? (change / dataPoint.open) * 100 : 0

        const nextHover = {
          open: dataPoint.open,
          high: dataPoint.high,
          low: dataPoint.low,
          close: dataPoint.close,
          volume: volPoint?.value,
          change,
          changePct,
        }
        if (hoverFrameRef.current !== null) cancelAnimationFrame(hoverFrameRef.current)
        hoverFrameRef.current = requestAnimationFrame(() => setHoverCandle(nextHover))
      } else {
        if (hoverFrameRef.current !== null) cancelAnimationFrame(hoverFrameRef.current)
        hoverFrameRef.current = requestAnimationFrame(() => setHoverCandle(null))
      }
    }
    chart.subscribeCrosshairMove(handleCrosshairMove)

    const resizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry || !chartRef.current) return
      const { width, height } = entry.contentRect
      if (width > 0 && height > 0) {
        chartRef.current.resize(width, height)
      }
    })
    resizeObserver.observe(chartContainerRef.current)

    return () => {
      resizeObserver.disconnect()
      controller.unbind()
      drawingControllerRef.current = null
      chart.unsubscribeCrosshairMove(handleCrosshairMove)
      if (hoverFrameRef.current !== null) cancelAnimationFrame(hoverFrameRef.current)
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
      volumeSeriesRef.current = null
      futureSeriesRef.current = null
    }
  }, [])

  // Symbol switch effect
  useEffect(() => {
    if (symbolRef.current === symbol) return
    symbolRef.current = symbol
    drawingControllerRef.current?.switchSymbol(symbol)
  }, [symbol])

  // Data update effect
  useEffect(() => {
    if (!candleSeriesRef.current || !chartRef.current) return
    dataRef.current = data
    const chart = chartRef.current
    const symbolChanged = renderedSymbolRef.current !== symbol
    const previousRange: LogicalRange | null = symbolChanged ? null : chart.timeScale().getVisibleLogicalRange()

    candleSeriesRef.current.setData(seriesData(data, chartTypeRef.current))

    if (volumeSeriesRef.current) {
      volumeSeriesRef.current.setData(
        data.map((d) => ({
          time: d.time as Time,
          value: d.volume || 0,
          color:
            d.close >= d.open
              ? "rgba(34, 197, 94, 0.45)"
              : "rgba(239, 68, 68, 0.45)",
        })),
      )
    }
    futureSeriesRef.current?.setData(
      data.length > 0
        ? nextWeekdays(data[data.length - 1]!.time, FUTURE_WHITESPACE_SLOTS).map((time) => ({ time }))
        : [],
    )

    if (data.length > 0) {
      candleSeriesRef.current.priceScale().applyOptions({ autoScale: true })
      if (previousRange) {
        chart.timeScale().setVisibleLogicalRange(previousRange)
      } else {
        chart.timeScale().setVisibleLogicalRange({
          from: 0,
          to: data.length - 1 + INITIAL_RIGHT_SLOTS,
        })
      }
    }
    renderedSymbolRef.current = symbol
  }, [data, symbol])

  // Volume visibility — the histogram pane is added/removed on demand without
  // touching candle data or user drawings.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    if (volumeVisible && !volumeSeriesRef.current) {
      const series = chart.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
      })
      volumeSeriesRef.current = series
      series.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })
      const bars = dataRef.current
      if (bars.length > 0) {
        series.setData(
          bars.map((d) => ({
            time: d.time as Time,
            value: d.volume || 0,
            color: d.close >= d.open ? "rgba(34, 197, 94, 0.45)" : "rgba(239, 68, 68, 0.45)",
          })),
        )
      }
    } else if (!volumeVisible && volumeSeriesRef.current) {
      chart.removeSeries(volumeSeriesRef.current)
      volumeSeriesRef.current = null
    }
  }, [volumeVisible])

  // SMA overlays (design toolbar): one thin line per enabled window.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const seriesMap = smaSeriesRef.current
    const wanted = new Set(smaOverlays.map((overlay) => overlay.period))
    for (const [period, series] of [...seriesMap]) {
      if (!wanted.has(period)) {
        chart.removeSeries(series)
        seriesMap.delete(period)
      }
    }
    for (const overlay of smaOverlays) {
      let series = seriesMap.get(overlay.period)
      if (!series) {
        series = chart.addSeries(LineSeries, {
          color: overlay.color,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        })
        seriesMap.set(overlay.period, series)
      } else {
        series.applyOptions({ color: overlay.color })
      }
      const period = overlay.period
      const points: { time: Time; value: number }[] = []
      let sum = 0
      for (let index = 0; index < data.length; index++) {
        sum += data[index]!.close
        if (index >= period) sum -= data[index - period]!.close
        if (index >= period - 1) {
          points.push({ time: data[index]!.time as Time, value: sum / period })
        }
      }
      series.setData(points)
    }
  }, [data, smaOverlays])

  // Chart type switch effect
  useEffect(() => {
    if (!chartRef.current || !candleSeriesRef.current) return

    const chart = chartRef.current
    chart.removeSeries(candleSeriesRef.current)

    let newSeries: ISeriesApi<"Candlestick" | "Line" | "Area">
    if (chartType === "line") {
      newSeries = chart.addSeries(LineSeries, {
        color: "#38bdf8",
        lineWidth: 2,
      })
    } else if (chartType === "area") {
      newSeries = chart.addSeries(AreaSeries, {
        topColor: "rgba(41, 98, 255, 0.4)",
        bottomColor: "rgba(41, 98, 255, 0.04)",
        lineColor: "#38bdf8",
        lineWidth: 2,
      })
    } else {
      newSeries = chart.addSeries(CandlestickSeries, {
        upColor: "#22c55e",
        downColor: "#ef4444",
        borderVisible: false,
        wickUpColor: "#22c55e",
        wickDownColor: "#ef4444",
      })
    }

    candleSeriesRef.current = newSeries

    const visibleRange = chart.timeScale().getVisibleLogicalRange()
    chartTypeRef.current = chartType
    newSeries.setData(seriesData(dataRef.current, chartType))
    priceLinesRef.current = { ltp: null, stop: null, target: null }

    // Re-bind drawing controller to new series
    if (drawingControllerRef.current && chartContainerRef.current) {
      drawingControllerRef.current.bind(
        chart,
        newSeries,
        chartContainerRef.current,
        symbolRef.current,
        setActiveTool,
        setSelectedRecord,
      )
    }
    if (visibleRange) chart.timeScale().setVisibleLogicalRange(visibleRange)
  }, [chartType])

  // Price lines effect (LTP, Stop Loss, Target)
  useEffect(() => {
    if (!candleSeriesRef.current) return

    const series = candleSeriesRef.current
    const syncLine = (
      key: "ltp" | "stop" | "target",
      price: number | undefined,
      options: Omit<Parameters<typeof series.createPriceLine>[0], "price">,
    ) => {
      const existing = priceLinesRef.current[key]
      if (!price || price <= 0) {
        if (existing) series.removePriceLine(existing)
        priceLinesRef.current[key] = null
        return
      }
      if (existing) existing.applyOptions({ price, ...options })
      else priceLinesRef.current[key] = series.createPriceLine({ price, ...options })
    }

    syncLine("ltp", liveLtp, { color: "#f59e0b", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "LTP" })
    syncLine("stop", stopLossPrice, { color: "#ef4444", lineWidth: 2, lineStyle: 0, axisLabelVisible: true, title: "STOP LOSS" })
    syncLine("target", targetPrice, { color: "#22c55e", lineWidth: 2, lineStyle: 0, axisLabelVisible: true, title: "TARGET" })
  }, [chartType, stopLossPrice, targetPrice, liveLtp])

  // Read-only AI VCP overlay (contraction bands + pivot), visually separate
  // from user drawings. Re-attaches when the series is swapped on type change.
  useEffect(() => {
    if (visionPrimitiveRef.current && visionSeriesRef.current) {
      try {
        visionSeriesRef.current.detachPrimitive(visionPrimitiveRef.current)
      } catch {
        // The series was already removed from the chart; nothing to detach.
      }
      visionPrimitiveRef.current = null
    }
    if (visionPivotLineRef.current && visionSeriesRef.current) {
      try {
        visionSeriesRef.current.removePriceLine(visionPivotLineRef.current)
      } catch {
        // The series was already removed from the chart.
      }
      visionPivotLineRef.current = null
    }
    visionSeriesRef.current = null

    const series = candleSeriesRef.current
    const chart = chartRef.current
    if (!showVisionOverlay || !visionOverlay || !chart || !series) return
    const bands = visionOverlay.contractions
    if (bands.length === 0) return

    const primitive = new VisionOverlayPrimitive(chart, series, { bands })
    series.attachPrimitive(primitive)
    visionPrimitiveRef.current = primitive
    visionSeriesRef.current = series
    if (visionOverlay.pivotPrice != null && visionOverlay.pivotPrice > 0) {
      visionPivotLineRef.current = series.createPriceLine({
        price: visionOverlay.pivotPrice,
        color: "#eab308",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "VCP PIVOT",
      })
    }
  }, [chartType, showVisionOverlay, visionOverlay])

  // Chart Controls Callbacks
  const toggleLogScale = () => {
    if (!chartRef.current) return
    const next = !isLogScale
    setIsLogScale(next)
    chartRef.current.priceScale("right").applyOptions({
      mode: next ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
    })
  }

  const toggleAutoScale = () => {
    if (!chartRef.current) return
    const next = !isAutoScale
    setIsAutoScale(next)
    chartRef.current.priceScale("right").applyOptions({
      autoScale: next,
    })
  }

  const resetZoom = () => {
    if (!chartRef.current) return
    if (dataRef.current.length === 0) {
      chartRef.current.timeScale().fitContent()
      return
    }
    chartRef.current.timeScale().setVisibleLogicalRange({
      from: 0,
      to: dataRef.current.length - 1 + INITIAL_RIGHT_SLOTS,
    })
  }

  const selectTool = (tool: DrawingTool) => {
    if (tool === activeTool && tool !== "cursor") {
      drawingControllerRef.current?.setActiveTool("cursor")
      setActiveTool("cursor")
      return
    }
    setActiveTool(tool)
    drawingControllerRef.current?.setActiveTool(tool)
  }

  const clearDrawings = () => {
    drawingControllerRef.current?.clearDrawings()
  }

  const handleUpdateStyle = (style: Partial<DrawingStyle>) => {
    drawingControllerRef.current?.updateSelectedStyle(style)
  }

  const handleDeleteSelected = () => {
    drawingControllerRef.current?.deleteSelectedDrawing()
  }

  // Active or latest candle fallback for OHLC header legend
  const latestCandle = data.length > 0 ? data[data.length - 1] : null
  const displayCandle = hoverCandle || (latestCandle
    ? {
        open: latestCandle.open,
        high: latestCandle.high,
        low: latestCandle.low,
        close: latestCandle.close,
        volume: latestCandle.volume,
        change: latestCandle.close - latestCandle.open,
        changePct:
          latestCandle.open > 0
            ? ((latestCandle.close - latestCandle.open) / latestCandle.open) * 100
            : 0,
      }
    : null)

  return (
    <div className="relative flex h-full w-full flex-1 flex-col bg-[#070b12] select-none font-mono">
      {/* Top Header Bar */}
      <div className="z-10 flex h-10 shrink-0 items-center justify-between border-b border-[#263246] bg-[#101826] px-3 text-xs">
        <div className="flex items-center gap-3">
          <span className="font-bold text-[#38bdf8] tracking-wide">
            {symbol}
          </span>
          <span className="rounded border border-[#263246] bg-[#070b12] px-2 py-0.5 text-[11px] font-bold text-[#38bdf8]">
            DAILY
          </span>

          <div className="h-4 w-px bg-[#263246]" />

          {/* Chart Type Toggle Buttons */}
          <div className="flex items-center gap-1">
            <button
              aria-label="Candlestick Chart"
              className={cn(
                "inline-flex h-6 w-6 items-center justify-center rounded border transition-colors",
                chartType === "candlestick"
                  ? "border-[#38bdf8] bg-[#38bdf8]/20 text-[#38bdf8]"
                  : "border-transparent text-[#8492a6] hover:border-[#263246] hover:bg-[#070b12] hover:text-[#cbd5e1]",
              )}
              onClick={() => setChartType("candlestick")}
              title="Candlestick Chart"
              type="button"
            >
              <CandlestickChartIcon className="h-3.5 w-3.5" />
            </button>
            <button
              aria-label="Line Chart"
              className={cn(
                "inline-flex h-6 w-6 items-center justify-center rounded border transition-colors",
                chartType === "line"
                  ? "border-[#38bdf8] bg-[#38bdf8]/20 text-[#38bdf8]"
                  : "border-transparent text-[#8492a6] hover:border-[#263246] hover:bg-[#070b12] hover:text-[#cbd5e1]",
              )}
              onClick={() => setChartType("line")}
              title="Line Chart"
              type="button"
            >
              <LineChartIcon className="h-3.5 w-3.5" />
            </button>
          </div>

          <div className="h-4 w-px bg-[#263246]" />

          {/* Drawing Tools Palette */}
          <div className="flex items-center gap-1">
            {drawingTools.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                aria-label={label}
                aria-pressed={activeTool === id}
                className={cn(
                  "inline-flex h-6.5 w-6.5 items-center justify-center rounded border transition-colors",
                  activeTool === id
                    ? "border-[#38bdf8] bg-[#38bdf8]/20 text-[#38bdf8]"
                    : "border-transparent text-[#8492a6] hover:border-[#263246] hover:bg-[#070b12] hover:text-[#cbd5e1]",
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
              className="inline-flex h-6.5 w-6.5 items-center justify-center rounded border border-transparent text-[#8492a6] transition-colors hover:border-[#263246] hover:bg-[#070b12] hover:text-[#ef4444]"
              onClick={clearDrawings}
              title="Clear all drawings"
              type="button"
            >
              <EraserIcon className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        {/* Right Side Controls */}
        <div className="flex items-center gap-2">
          <button
            className={cn(
              "inline-flex h-6 px-2 items-center justify-center rounded border text-[11px] font-semibold transition-colors",
              showVisionOverlay
                ? "border-[#eab308] bg-[#eab308]/20 text-[#eab308]"
                : "border-[#263246] text-[#8492a6] hover:bg-[#070b12] hover:text-[#cbd5e1]",
              !visionOverlay && "opacity-40",
            )}
            disabled={!visionOverlay}
            onClick={() => setShowVisionOverlay((value) => !value)}
            title="Toggle the AI VCP vision overlay"
            type="button"
          >
            VCP
          </button>
          <button
            className={cn(
              "inline-flex h-6 px-2 items-center justify-center rounded border text-[11px] font-semibold transition-colors",
              isAutoScale
                ? "border-[#38bdf8] bg-[#38bdf8]/20 text-[#38bdf8]"
                : "border-[#263246] text-[#8492a6] hover:bg-[#070b12] hover:text-[#cbd5e1]",
            )}
            onClick={toggleAutoScale}
            title="Auto Scale"
            type="button"
          >
            AUTO
          </button>
          <button
            className={cn(
              "inline-flex h-6 px-2 items-center justify-center rounded border text-[11px] font-semibold transition-colors",
              isLogScale
                ? "border-[#38bdf8] bg-[#38bdf8]/20 text-[#38bdf8]"
                : "border-[#263246] text-[#8492a6] hover:bg-[#070b12] hover:text-[#cbd5e1]",
            )}
            onClick={toggleLogScale}
            title="Logarithmic Scale"
            type="button"
          >
            LOG
          </button>
          <button
            aria-label="Reset view"
            className="inline-flex h-6 w-6 items-center justify-center rounded border border-[#263246] text-[#8492a6] transition-colors hover:bg-[#070b12] hover:text-[#cbd5e1]"
            onClick={resetZoom}
            title="Reset Zoom (Fit Content)"
            type="button"
          >
            <RefreshCwIcon className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Floating Property Toolbar when a drawing is selected */}
      <DrawingPropertiesBar
        onDelete={handleDeleteSelected}
        onUpdateStyle={handleUpdateStyle}
        selectedRecord={selectedRecord}
      />

      {/* Interactive OHLCV Legend Overlay (TradingView style) */}
      <div className="pointer-events-none absolute top-12 left-4 z-10 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] text-[#8492a6]">
        <span className="font-bold text-[#cbd5e1]">{symbol}</span>
        {displayCandle && (
          <>
            <span>
              O:{" "}
              <strong className="text-[#cbd5e1]">
                {displayCandle.open.toFixed(2)}
              </strong>
            </span>
            <span>
              H:{" "}
              <strong className="text-[#cbd5e1]">
                {displayCandle.high.toFixed(2)}
              </strong>
            </span>
            <span>
              L:{" "}
              <strong className="text-[#cbd5e1]">
                {displayCandle.low.toFixed(2)}
              </strong>
            </span>
            <span>
              C:{" "}
              <strong className="text-[#cbd5e1]">
                {displayCandle.close.toFixed(2)}
              </strong>
            </span>
            <span
              className={cn(
                "font-semibold",
                displayCandle.change >= 0 ? "text-[#22c55e]" : "text-[#ef4444]",
              )}
            >
              {displayCandle.change >= 0 ? "+" : ""}
              {displayCandle.change.toFixed(2)} (
              {displayCandle.changePct >= 0 ? "+" : ""}
              {displayCandle.changePct.toFixed(2)}%)
            </span>
            {displayCandle.volume !== undefined && (
              <span>
                Vol:{" "}
                <strong className="text-[#cbd5e1]">
                  {(displayCandle.volume / 1000).toFixed(1)}k
                </strong>
              </span>
            )}
          </>
        )}
      </div>

      {/* Main Canvas Container */}
      <div
        ref={chartContainerRef}
        className="h-full min-h-0 w-full flex-1 relative"
      />
    </div>
  )
}
