import { GeistMono } from "geist/font/mono"
import { GeistSans } from "geist/font/sans"
import type { ReactNode } from "react"

import { LandingFooter } from "@/components/landing/landing-footer"
import { LandingNav } from "@/components/landing/landing-nav"
import "@/components/landing/landing.css"

type MarketingShellProps = {
  children: ReactNode
  active?: "home" | "scanners" | "learn" | "trust"
  showScannerCta?: boolean
  /** When false, omit the shared footer (pages that render their own). */
  withFooter?: boolean
}

export function MarketingShell({
  children,
  active = "home",
  showScannerCta = true,
  withFooter = true,
}: MarketingShellProps) {
  return (
    <div
      className={`landing min-h-screen overflow-x-hidden ${GeistSans.variable} ${GeistMono.variable}`}
      style={
        {
          ["--font-landing-body" as string]: "var(--font-geist-sans)",
          ["--font-landing-mono" as string]: "var(--font-geist-mono)",
        } as React.CSSProperties
      }
    >
      <LandingNav showScannerCta={showScannerCta} active={active} />
      <main id="top">{children}</main>
      {withFooter ? <LandingFooter /> : null}
    </div>
  )
}
