import type { Metadata } from "next"
import Link from "next/link"
import { headers } from "next/headers"

import { MarketingShell } from "@/components/site/marketing-shell"
import { listStandardHistory, resolveAccess } from "@/lib/entitlements"
import { CANONICAL_SCANNER_PATH } from "@/lib/seo/config"
import { buildPageMetadata } from "@/lib/seo/metadata"

export const metadata: Metadata = buildPageMetadata({
  title: "Past scans",
  description: "Browse prior Minervini Standard EOD shortlists.",
  path: "/past-scans",
  noIndex: true,
})

export const dynamic = "force-dynamic"

export default async function PastScansPage() {
  const access = await resolveAccess(await headers())
  const sessions = access.isAuthenticated || access.isBypassed
    ? await listStandardHistory(access.limits.historySessions)
    : []

  return (
    <MarketingShell active="scanner">
      <section className="landing-block">
        <div className="landing-container max-w-4xl">
          <p className="landing-kicker">Archive</p>
          <h1 className="landing-h2 mt-5">Past Standard scans</h1>
          <p className="landing-lead mt-6">
            Today stays public. Free accounts keep the latest 20 trading sessions; Pro keeps the complete archive.
          </p>

          {access.isAuthenticated || access.isBypassed ? (
            <div className="mt-10 border-t border-[var(--landing-border)]">
              {sessions.length > 0 ? sessions.map((session) => (
                <Link
                  key={session.asOfDate}
                  href={`/past-scans/${session.asOfDate}`}
                  className="grid gap-2 border-b border-[var(--landing-border)] px-2 py-5 text-[var(--landing-fg-2)] transition-colors hover:bg-[var(--landing-surface)] hover:no-underline sm:grid-cols-[1fr_auto_auto] sm:items-center sm:gap-8"
                >
                  <time className="font-[family-name:var(--font-landing-mono)] text-[var(--landing-fg)]">{session.asOfDate}</time>
                  <span className="text-sm">{session.resultCount} setups</span>
                  <span className="font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">Open board →</span>
                </Link>
              )) : (
                <p className="border-b border-[var(--landing-border)] px-2 py-8 text-sm text-[var(--landing-muted)]">No completed historical scans yet.</p>
              )}
            </div>
          ) : (
            <div className="mt-10 border border-[var(--landing-border)] bg-[var(--landing-surface)] p-6">
              <p className="text-sm leading-relaxed text-[var(--landing-fg-2)]">Sign in to unlock the latest 20 prior EOD shortlists.</p>
              <div className="mt-6 flex flex-wrap gap-3">
                <Link href="/sign-in?next=/past-scans" className="landing-btn landing-btn-primary">Sign in</Link>
                <Link href="/sign-up" className="landing-btn landing-btn-ghost">Create account</Link>
                <Link href={CANONICAL_SCANNER_PATH} className="landing-text-link self-center">Browse today</Link>
              </div>
            </div>
          )}
        </div>
      </section>
    </MarketingShell>
  )
}
