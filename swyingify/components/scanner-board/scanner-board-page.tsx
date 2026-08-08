import { GeistMono } from "geist/font/mono"
import { GeistSans } from "geist/font/sans"
import { Suspense } from "react"

import { LandingNav } from "@/components/landing/landing-nav"
import "@/components/landing/landing.css"
import { ScannerBoard } from "@/components/scanner-board/scanner-board"
import "@/components/scanner-board/scanner-board.css"

export function ScannerBoardPage() {
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
      <Suspense fallback={<div className="mx-auto max-w-[1200px] px-6 py-24"><div className="h-[520px] animate-pulse bg-[var(--landing-surface)]" /></div>}>
        <ScannerBoard />
      </Suspense>
    </div>
  )
}
