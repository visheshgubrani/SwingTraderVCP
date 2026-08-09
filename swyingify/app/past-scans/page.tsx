import type { Metadata } from "next"
import Link from "next/link"
import { headers } from "next/headers"
import { GeistMono } from "geist/font/mono"
import { GeistSans } from "geist/font/sans"

import { LandingFooter } from "@/components/landing/landing-footer"
import { LandingNav } from "@/components/landing/landing-nav"
import "@/components/landing/landing.css"
import { auth } from "@/lib/auth"
import { CANONICAL_SCANNER_PATH } from "@/lib/seo/config"
import { buildPageMetadata } from "@/lib/seo/metadata"

export const metadata: Metadata = buildPageMetadata({
  title: "Past scans",
  description: "Browse prior Minervini Standard EOD shortlists. Sign in required during beta.",
  path: "/past-scans",
  noIndex: true,
})

export default async function PastScansPage() {
  const session = await auth.api.getSession({ headers: await headers() })

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
      <LandingNav showScannerCta active="scanner" />
      <main className="landing-block">
        <div className="landing-container max-w-3xl">
          <p className="landing-kicker">Archive</p>
          <h1 className="landing-h2 mt-5">Past scans</h1>
          <p className="landing-lead mt-6">
            Today&apos;s Standard board stays public. Prior as-of dates require a free account during beta.
          </p>

          {session?.user ? (
            <div className="mt-10 border border-[var(--landing-border)] bg-[var(--landing-surface)] p-6">
              <p className="font-[family-name:var(--font-landing-mono)] text-sm text-[var(--landing-fg)]">
                Signed in as {session.user.email}
              </p>
              <p className="mt-3 text-sm leading-relaxed text-[var(--landing-fg-2)]">
                Historical date picker is coming next. For now, open tonight&apos;s live Standard shortlist.
              </p>
              <Link href={CANONICAL_SCANNER_PATH} className="landing-btn landing-btn-primary mt-6 inline-flex">
                Open today&apos;s scanner
              </Link>
            </div>
          ) : (
            <div className="mt-10 border border-[var(--landing-border)] bg-[var(--landing-surface)] p-6">
              <p className="text-sm leading-relaxed text-[var(--landing-fg-2)]">
                Sign in to unlock past EOD shortlists. Today&apos;s Standard top 25 does not require an account.
              </p>
              <div className="mt-6 flex flex-wrap gap-3">
                <Link href="/sign-in" className="landing-btn landing-btn-primary">
                  Sign in
                </Link>
                <Link href="/sign-up" className="landing-btn landing-btn-ghost">
                  Create account
                </Link>
                <Link href={CANONICAL_SCANNER_PATH} className="landing-text-link self-center">
                  Browse today instead
                </Link>
              </div>
            </div>
          )}
        </div>
      </main>
      <LandingFooter />
    </div>
  )
}
