"use client"

import { useEffect, useRef } from "react"
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
} from "lightweight-charts"

import type { DailyCandle } from "@/lib/scanner/types"

export function StockChart({ candles }: { candles: DailyCandle[] }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#617068",
        fontFamily: "IBM Plex Mono, monospace",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "rgba(97,112,104,0.08)" },
        horzLines: { color: "rgba(97,112,104,0.08)" },
      },
      rightPriceScale: { borderColor: "rgba(97,112,104,0.16)" },
      timeScale: {
        borderColor: "rgba(97,112,104,0.16)",
        rightOffset: 4,
        barSpacing: 10,
      },
      crosshair: { vertLine: { color: "rgba(68,86,217,0.3)" }, horzLine: { color: "rgba(68,86,217,0.3)" } },
    })

    const candlesSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#16856f",
      downColor: "#c6535f",
      borderVisible: false,
      wickUpColor: "#16856f",
      wickDownColor: "#c6535f",
    })
    const sma50 = chart.addSeries(LineSeries, { color: "#4456d9", lineWidth: 2, title: "SMA 50" })
    const sma150 = chart.addSeries(LineSeries, { color: "#c47a18", lineWidth: 1, title: "SMA 150" })
    const sma200 = chart.addSeries(LineSeries, { color: "#17201b", lineWidth: 1, lineStyle: 2, title: "SMA 200" })
    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "" }, 1)

    candlesSeries.setData(candles.map((item) => ({
      time: item.time,
      open: item.open,
      high: item.high,
      low: item.low,
      close: item.close,
    })))
    sma50.setData(candles.map((item) => ({ time: item.time, value: item.sma50 })))
    sma150.setData(candles.map((item) => ({ time: item.time, value: item.sma150 })))
    sma200.setData(candles.map((item) => ({ time: item.time, value: item.sma200 })))
    volume.setData(candles.map((item, index) => ({
      time: item.time,
      value: item.volume,
      color: index === 0 || item.close >= candles[index - 1].close ? "rgba(22,133,111,0.34)" : "rgba(198,83,95,0.28)",
    })))
    chart.panes()[1]?.setHeight(78)
    chart.timeScale().fitContent()
    chartRef.current = chart

    return () => {
      chartRef.current = null
      chart.remove()
    }
  }, [candles])

  return <div ref={containerRef} className="h-[330px] w-full" aria-label="Daily price and volume chart" />
}
