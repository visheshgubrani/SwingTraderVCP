import Link from "next/link"

import { LandingBrand } from "@/components/landing/landing-brand"
import { cn } from "@/lib/utils"

type LandingNavProps = {
  showScannerCta?: boolean
  active?: "home" | "scanner"
}

export function LandingNav({ showScannerCta = true, active = "home" }: LandingNavProps) {
  return (
    <nav className="landing-nav">
      <div className="landing-container landing-nav-inner">
        <LandingBrand />
        <div className="landing-nav-links" aria-label="Primary">
          <Link href="/#scan">The scan</Link>
          <Link href="/#method">The method</Link>
          <Link href="/#roadmap">Roadmap</Link>
          <Link href="/scanner" className={cn(active === "scanner" && "is-active")}>
            Daily board
          </Link>
        </div>
        <div className="landing-nav-auth">
          <Link href="/sign-in" className="landing-nav-signin">
            Sign in
          </Link>
          {showScannerCta ? (
            <Link href="/scanner" className="landing-btn landing-btn-ghost landing-nav-cta">
              Open scanner
            </Link>
          ) : (
            <Link href="/sign-up" className="landing-btn landing-btn-ghost landing-nav-cta">
              Create account
            </Link>
          )}
        </div>
      </div>
    </nav>
  )
}
