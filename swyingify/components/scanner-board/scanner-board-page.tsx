import { GeistMono } from "geist/font/mono"
import { GeistSans } from "geist/font/sans"
import { Suspense } from "react"

import { LandingNav } from "@/components/landing/landing-nav"
import "@/components/landing/landing.css"
import { ScannerBoard } from "@/components/scanner-board/scanner-board"
import "@/components/scanner-board/scanner-board.css"
import { ScannerEducation } from "@/components/scanners/scanner-education"
import { LandingFooter } from "@/components/landing/landing-footer"
import type { ScannerPreset, ScannerResultPreview } from "@/lib/scanner/types"

type ScannerBoardPageProps = {
  initialPreset: ScannerPreset
  initialResults: ScannerResultPreview[]
  asOfDate: string
  isLiveData: boolean
}

export function ScannerBoardPage({
  initialPreset,
  initialResults,
  asOfDate,
  isLiveData,
}: ScannerBoardPageProps) {
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
      <LandingNav showScannerCta={false} active="scanners" />
      <Suspense
        fallback={
          <div className="mx-auto max-w-[1200px] px-6 py-24">
            <div className="h-[520px] animate-pulse bg-[var(--landing-surface)]" />
          </div>
        }
      >
        <ScannerBoard
          initialPreset={initialPreset}
          initialResults={initialResults}
          asOfDate={asOfDate}
          isLiveData={isLiveData}
        />
      </Suspense>
      <ScannerEducation asOfDate={asOfDate} isLiveData={isLiveData} />
      <LandingFooter />
    </div>
  )
}
