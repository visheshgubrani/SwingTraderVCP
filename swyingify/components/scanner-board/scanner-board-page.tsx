import { GeistMono } from "geist/font/mono"
import { GeistSans } from "geist/font/sans"
import { Suspense } from "react"

import { LandingNav } from "@/components/landing/landing-nav"
import "@/components/landing/landing.css"
import { ScannerBoard } from "@/components/scanner-board/scanner-board"
import "@/components/scanner-board/scanner-board.css"
import { LandingFooter } from "@/components/landing/landing-footer"
import type { ScannerPreset, ScannerResultPreview } from "@/lib/scanner/types"

type ScannerBoardPageProps = {
  initialPreset: Exclude<ScannerPreset, "custom">
  initialResults: ScannerResultPreview[]
  asOfDate: string
  isLiveData: boolean
  accessNotice?: string
  historical?: boolean
}

export function ScannerBoardPage({
  initialPreset,
  initialResults,
  asOfDate,
  isLiveData,
  accessNotice,
  historical = false,
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
      {accessNotice ? (
        <div className="border-b border-[var(--landing-border)] bg-[var(--landing-surface-warm)]">
          <p className="mx-auto max-w-[1200px] px-6 py-2.5 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)] max-sm:px-3">
            {accessNotice}
          </p>
        </div>
      ) : null}
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
          historical={historical}
        />
      </Suspense>
      <LandingFooter />
    </div>
  )
}
