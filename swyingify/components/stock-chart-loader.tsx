"use client"

import dynamic from "next/dynamic"

import type { DailyCandle } from "@/lib/scanner/types"

const ClientStockChart = dynamic(
  () => import("@/components/stock-chart").then((module) => module.StockChart),
  { ssr: false, loading: () => <div className="h-[330px] w-full animate-pulse rounded-xl bg-muted/50" /> },
)

export function StockChartLoader({ candles }: { candles: DailyCandle[] }) {
  return <ClientStockChart candles={candles} />
}

