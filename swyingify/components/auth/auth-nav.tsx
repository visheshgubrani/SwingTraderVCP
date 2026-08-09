import Link from "next/link"

import { LandingBrand } from "@/components/landing/landing-brand"

export function AuthNav({ mode }: { mode: "sign-in" | "sign-up" }) {
  return (
    <nav className="landing-nav">
      <div className="landing-container landing-nav-inner">
        <LandingBrand />
        <div className="landing-nav-auth">
          {mode === "sign-up" ? (
            <Link href="/sign-in" className="landing-nav-signin hidden sm:inline-flex">
              Sign in
            </Link>
          ) : (
            <Link href="/sign-up" className="landing-nav-signin hidden sm:inline-flex">
              Create account
            </Link>
          )}
          <Link href="/scanners/minervini-vcp" className="landing-btn landing-btn-ghost landing-nav-cta">
            Open scanner
          </Link>
        </div>
      </div>
    </nav>
  )
}
