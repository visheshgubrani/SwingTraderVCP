import Link from "next/link"

import { LandingBrand } from "@/components/landing/landing-brand"
import { CANONICAL_SCANNER_PATH } from "@/lib/seo/config"

export function LandingFooter() {
  return (
    <footer className="landing-footer">
      <div className="landing-container">
        <div className="landing-footer-top">
          <div>
            <LandingBrand href="/" compact />
            <p className="landing-footer-tag">A calmer research desk for Indian equities.</p>
          </div>
          <div className="landing-footer-links">
            <Link href="/scanners">Scanners</Link>
            <Link href="/learn">Learn</Link>
            <Link href="/methodology">Methodology</Link>
            <Link href="/about">About</Link>
            <Link href="/disclaimer">Disclaimer</Link>
            <Link href={CANONICAL_SCANNER_PATH}>Minervini VCP</Link>
          </div>
        </div>
        <p className="landing-footer-legal">
          Swyingify is educational software, not SEBI-registered and not investment advice. Strategy rules
          are independent approximations and are not endorsed by any named trader. ©{" "}
          {new Date().getFullYear()} Swyingify.
        </p>
      </div>
    </footer>
  )
}
