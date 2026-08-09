import Link from "next/link"

import { LandingBrand } from "@/components/landing/landing-brand"
import { CANONICAL_SCANNER_PATH } from "@/lib/seo/config"

export function AuthFooter() {
  return (
    <footer className="landing-footer">
      <div className="landing-container">
        <div className="landing-footer-top">
          <div>
            <LandingBrand compact />
            <p className="landing-footer-tag">A calmer research desk for Indian equities.</p>
          </div>
          <div className="landing-footer-links">
            <Link href="/scanners">Scanners</Link>
            <Link href="/learn">Learn</Link>
            <Link href="/disclaimer">Disclaimer</Link>
            <Link href={CANONICAL_SCANNER_PATH}>Minervini VCP</Link>
            <Link href="/sign-up">Sign up</Link>
            <Link href="/sign-in">Sign in</Link>
          </div>
        </div>
        <p className="landing-footer-legal">
          Swyingify is educational software, not SEBI-registered and not investment advice. ©{" "}
          {new Date().getFullYear()} Swyingify.
        </p>
      </div>
    </footer>
  )
}
