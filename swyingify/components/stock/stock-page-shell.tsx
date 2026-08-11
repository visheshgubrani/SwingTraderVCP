import { GeistMono } from "geist/font/mono"
import { GeistSans } from "geist/font/sans"

import { LandingNav } from "@/components/landing/landing-nav"
import "@/components/landing/landing.css"
import "@/components/scanner-board/scanner-board.css"
import { StockDetailView } from "@/components/stock/stock-detail-view"
import "@/components/stock/stock.css"
import type { ScannerResultPreview } from "@/lib/scanner/types"

export function StockPageShell({
  result,
  isLiveData = false,
}: {
  result: ScannerResultPreview
  isLiveData?: boolean
}) {
  return (
    <div
      className={`landing scanner-board min-h-screen overflow-x-hidden ${GeistSans.variable} ${GeistMono.variable}`}
      style={
        {
          ["--font-landing-body" as string]: "var(--font-geist-sans)",
          ["--font-landing-mono" as string]: "var(--font-geist-mono)",
        } as React.CSSProperties
      }
    >
      <LandingNav showScannerCta={false} active="scanner" />
      <main>
        <StockDetailView result={result} isLiveData={isLiveData} />
      </main>
    </div>
  )
}
