import type { Metadata } from "next"
import { headers } from "next/headers"
import Link from "next/link"

import { ScannerBoardPage } from "@/components/scanner-board/scanner-board-page"
import { MarketingShell } from "@/components/site/marketing-shell"
import { hasFeature, resolveAccess } from "@/lib/entitlements"
import { createInternalAccessToken } from "@/lib/internal-api-access"
import { fetchScannerLatest } from "@/lib/scanner/api"
import { getScannerBoardData } from "@/lib/scanner/board-data"
import { buildPageMetadata } from "@/lib/seo/metadata"

export const metadata: Metadata = buildPageMetadata({
  title: "Minervini Strict scanner",
  description: "A tighter paid Minervini VCP shortlist for Swyingify Pro members.",
  path: "/scanners/minervini-vcp/strict",
  noIndex: true,
})

export const dynamic = "force-dynamic"

export default async function MinerviniStrictPage() {
  const access = await resolveAccess(await headers())
  if (hasFeature(access, "scanner.strict")) {
    const accessToken = createInternalAccessToken(access) ?? undefined
    const board = await getScannerBoardData("strict", { accessToken })
    const accessNotice = access.bypassReason === "development"
      ? "Developer access · production paywalls are disabled"
      : access.bypassReason === "admin"
        ? "Admin access · Pro entitlement bypass active"
        : undefined
    return (
      <ScannerBoardPage
        initialPreset="strict"
        initialResults={board.results}
        asOfDate={board.asOfDate}
        isLiveData={board.isLiveData}
        accessNotice={accessNotice}
      />
    )
  }

  let resultCount = 0
  let asOfDate: string | null = null
  try {
    if (!hasFeature(access, "scanner.strict.preview")) {
      throw new Error("Strict preview is not available for this tier")
    }
    const latest = await fetchScannerLatest("strict", { cache: "no-store" })
    resultCount = latest.resultCount
    asOfDate = latest.asOfDate
  } catch {
    // The upgrade surface remains useful before the first Strict run exists.
  }

  return (
    <MarketingShell active="scanners" showScannerCta={false}>
      <section className="landing-block">
        <div className="landing-container">
          <p className="landing-kicker">Minervini VCP · Strict · Pro</p>
          <div className="mt-6 grid gap-12 lg:grid-cols-[minmax(0,1fr)_360px] lg:items-start">
            <div>
              <h1 className="landing-display">A shorter list. Stronger agreement.</h1>
              <p className="landing-lead mt-6">
                Strict requires all five Stage 2 checks, stronger relative strength, tighter contraction,
                higher liquidity, and clearer volume dry-up. It is built for the nights when Standard still
                leaves too much chart review.
              </p>
              <div className="mt-9 flex flex-wrap gap-3">
                <Link href={access.isAuthenticated ? "/pricing" : "/sign-up"} className="landing-btn landing-btn-primary">
                  {access.isAuthenticated ? "See Pro access" : "Create free account"}
                </Link>
                <Link href="/scanners/minervini-vcp" className="landing-btn landing-btn-ghost">
                  Open Standard free
                </Link>
              </div>
            </div>

            <aside className="border border-[var(--landing-border)] bg-[var(--landing-surface)] p-6">
              <p className="landing-kicker">Tonight&apos;s locked board</p>
              <p className="mt-5 font-[family-name:var(--font-landing-mono)] text-5xl font-light text-[var(--landing-fg)]">
                {String(resultCount).padStart(2, "0")}
              </p>
              <p className="mt-2 text-sm text-[var(--landing-muted)]">
                Strict setups{asOfDate ? ` · ${asOfDate}` : " · awaiting the next EOD run"}
              </p>
              <div className="mt-7 border-t border-[var(--landing-border)] pt-5 text-sm leading-relaxed text-[var(--landing-fg-2)]">
                The count is public so you can judge whether Strict is useful tonight. Symbols and charts stay
                available to Pro members.
              </div>
            </aside>
          </div>
        </div>
      </section>
    </MarketingShell>
  )
}
