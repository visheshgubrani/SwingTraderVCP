import type { Metadata } from "next"
import Link from "next/link"

import { MarketingShell } from "@/components/site/marketing-shell"
import { buildPageMetadata } from "@/lib/seo/metadata"

export const metadata: Metadata = buildPageMetadata({
  title: "Disclaimer",
  description:
    "Educational use only. Swyingify is not SEBI-registered, not investment advice, and not endorsed by any named trader.",
  path: "/disclaimer",
})

export default function DisclaimerPage() {
  return (
    <MarketingShell active="trust">
      <article className="landing-block">
        <div className="landing-container max-w-[760px]">
          <p className="landing-kicker">Disclaimer</p>
          <h1 className="landing-display mt-5">Educational software — know the limits</h1>
          <div className="mt-8 space-y-4 text-base leading-relaxed text-[var(--landing-fg-2)]">
            <p>
              Swyingify provides educational market screening and explanatory content for Indian equities. Nothing on
              this site is investment advice, a recommendation to buy or sell any security, or an offer of portfolio
              management.
            </p>
            <p>
              Swyingify is <strong className="font-medium text-[var(--landing-fg)]">not SEBI-registered</strong>. We
              do not execute orders, manage positions, or connect your brokerage account from this product.
            </p>
            <p>
              Strategy names reference well-known traders for education. Implementations are{" "}
              <strong className="font-medium text-[var(--landing-fg)]">independent rule-based approximations</strong>{" "}
              and are <strong className="font-medium text-[var(--landing-fg)]">not endorsed</strong> by those traders
              or their publishers.
            </p>
            <p>
              Markets involve risk of loss. Past pattern language does not predict future results. You remain
              responsible for your own research and decisions.
            </p>
            <p>
              See also{" "}
              <Link href="/methodology" className="underline underline-offset-4">
                methodology
              </Link>{" "}
              and{" "}
              <Link href="/about" className="underline underline-offset-4">
                about
              </Link>
              .
            </p>
          </div>
        </div>
      </article>
    </MarketingShell>
  )
}
