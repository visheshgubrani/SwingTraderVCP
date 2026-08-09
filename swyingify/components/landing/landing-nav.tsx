"use client"

import Link from "next/link"
import { useRouter } from "next/navigation"

import { LandingBrand } from "@/components/landing/landing-brand"
import { authClient } from "@/lib/auth-client"
import { CANONICAL_SCANNER_PATH } from "@/lib/seo/config"
import { cn } from "@/lib/utils"

type LandingNavProps = {
  showScannerCta?: boolean
  active?: "home" | "scanners" | "learn" | "trust" | "scanner"
}

export function LandingNav({ showScannerCta = true, active = "home" }: LandingNavProps) {
  const scannersActive = active === "scanners" || active === "scanner"
  const router = useRouter()
  const { data: session, isPending } = authClient.useSession()
  const user = session?.user

  async function handleSignOut() {
    await authClient.signOut()
    router.refresh()
  }

  return (
    <nav className="landing-nav">
      <div className="landing-container landing-nav-inner">
        <LandingBrand />
        <div className="landing-nav-links" aria-label="Primary">
          <Link href="/scanners" className={cn(scannersActive && "is-active")}>
            Scanners
          </Link>
          <Link href="/learn" className={cn(active === "learn" && "is-active")}>
            Learn
          </Link>
          <Link href="/past-scans" className={cn(active === "scanner" && "is-active")}>
            Past scans
          </Link>
          <Link href="/methodology" className={cn(active === "trust" && "is-active")}>
            Methodology
          </Link>
        </div>
        <div className="landing-nav-auth">
          {!isPending && user ? (
            <>
              <span className="landing-nav-signin max-w-[12rem] truncate" title={user.email}>
                {user.name || user.email}
              </span>
              <button type="button" className="landing-btn landing-btn-ghost landing-nav-cta" onClick={handleSignOut}>
                Sign out
              </button>
            </>
          ) : (
            <>
              <Link href="/sign-in" className="landing-nav-signin">
                Sign in
              </Link>
              {showScannerCta ? (
                <Link href={CANONICAL_SCANNER_PATH} className="landing-btn landing-btn-ghost landing-nav-cta">
                  Open scanner
                </Link>
              ) : (
                <Link href="/sign-up" className="landing-btn landing-btn-ghost landing-nav-cta">
                  Create account
                </Link>
              )}
            </>
          )}
        </div>
      </div>
    </nav>
  )
}
