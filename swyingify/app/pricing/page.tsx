import type { Metadata } from "next"
import { headers } from "next/headers"
import Link from "next/link"

import { MarketingShell } from "@/components/site/marketing-shell"
import { resolveAccess } from "@/lib/entitlements"
import { buildPageMetadata } from "@/lib/seo/metadata"

export const metadata: Metadata = buildPageMetadata({
  title: "Swyingify pricing",
  description: "Compare the free Minervini Standard scanner with Swyingify Pro controls.",
  path: "/pricing",
})

export const dynamic = "force-dynamic"

const FREE = [
  "Today’s complete Standard top 25",
  "Charts, score, RS, liquidity and setup context",
  "Search, sort and result-side filters",
  "Latest 20 EOD sessions with a free account",
]

const PRO = [
  "Everything in free",
  "Minervini Strict symbols and charts",
  "Five guided Nifty 500 custom scans per day",
  "Complete scan archive",
  "Future paid legend scanners and alerts",
]

export default async function PricingPage() {
  const access = await resolveAccess(await headers())
  return (
    <MarketingShell active="home" showScannerCta={false}>
      <section className="landing-block">
        <div className="landing-container">
          <p className="landing-kicker">Access</p>
          <h1 className="landing-display mt-5">Free for discovery. Pro for your process.</h1>
          <p className="landing-lead mt-6">
            Standard stays free. Pro pays for tighter rules, complete-universe reruns, continuity, and automation—not for hiding basic stock information.
          </p>

          <div className="mt-14 grid gap-5 lg:grid-cols-2">
            <article className="border border-[var(--landing-border)] bg-[var(--landing-surface)] p-7">
              <p className="landing-kicker">Free</p>
              <h2 className="mt-4 font-[family-name:var(--font-landing-mono)] text-3xl font-light">Standard</h2>
              <p className="mt-3 text-sm text-[var(--landing-muted)]">₹0 · no card required</p>
              <ul className="mt-8 flex flex-col gap-3 text-sm text-[var(--landing-fg-2)]">
                {FREE.map((item) => <li key={item} className="border-t border-[var(--landing-border-soft)] pt-3">{item}</li>)}
              </ul>
              <Link href="/scanners/minervini-vcp" className="landing-btn landing-btn-ghost mt-8 w-full">Open Standard</Link>
            </article>

            <article className="border border-white/25 bg-[var(--landing-surface-warm)] p-7">
              <p className="landing-kicker">Pro</p>
              <h2 className="mt-4 font-[family-name:var(--font-landing-mono)] text-3xl font-light">Strict + custom</h2>
              <p className="mt-3 text-sm text-[var(--landing-muted)]">Launch price and checkout are being finalised</p>
              <ul className="mt-8 flex flex-col gap-3 text-sm text-[var(--landing-fg-2)]">
                {PRO.map((item) => <li key={item} className="border-t border-[var(--landing-border-soft)] pt-3">{item}</li>)}
              </ul>
              <Link
                href={access.isAuthenticated ? "/account" : "/sign-up"}
                className="landing-btn landing-btn-primary mt-8 w-full"
              >
                {access.tier === "pro" || access.isBypassed ? "View your access" : access.isAuthenticated ? "View plan status" : "Create free account"}
              </Link>
            </article>
          </div>

          <p className="mt-8 max-w-2xl text-sm leading-relaxed text-[var(--landing-muted)]">
            Checkout is intentionally not accepting money until the launch price and final payment provider are confirmed. The entitlement system already supports active Pro subscriptions, admin testing, and unrestricted development.
          </p>
        </div>
      </section>
    </MarketingShell>
  )
}
