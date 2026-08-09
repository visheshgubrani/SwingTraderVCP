import type { Metadata } from "next"
import Link from "next/link"

import { MarketingShell } from "@/components/site/marketing-shell"
import { buildPageMetadata } from "@/lib/seo/metadata"
import { EDITORIAL } from "@/lib/seo/editorial"

export const metadata: Metadata = buildPageMetadata({
  title: "About Swyingify",
  description:
    "Who builds Swyingify, our editorial policy for educational market content, and how we approach legend-trader scanner approximations for Indian equities.",
  path: "/about",
})

export default function AboutPage() {
  return (
    <MarketingShell active="trust">
      <article className="landing-block">
        <div className="landing-container max-w-[760px]">
          <p className="landing-kicker">About</p>
          <h1 className="landing-display mt-5">Built for calmer swing research</h1>
          <p className="landing-lead mt-6">
            Swyingify is an educational scanner product for Indian equities. We publish independent rule-based
            approximations of well-known swing frameworks — starting with Minervini Stage 2 / VCP on the Nifty 500.
          </p>

          <section className="mt-12 space-y-4 text-base leading-relaxed text-[var(--landing-fg-2)]">
            <h2 className="landing-h2 text-[clamp(22px,3vw,30px)]">Ownership & editorial</h2>
            <p>
              Public guides are authored as <strong className="font-medium text-[var(--landing-fg)]">{EDITORIAL.name}</strong>
              — a transparent product byline, not invented individual credentials. We revise methodology pages when
              template versions change.
            </p>
            <p>
              We do not claim SEBI registration, guaranteed accuracy, or endorsement by any named trader. Marketing
              copy must stay educational: no “best stocks,” “buy signal,” or unsubstantiated “high probability”
              language.
            </p>
          </section>

          <section className="mt-12 space-y-4 text-base leading-relaxed text-[var(--landing-fg-2)]">
            <h2 className="landing-h2 text-[clamp(22px,3vw,30px)]">What we ship</h2>
            <p>
              V1 focuses on one live family (Minervini) plus a focused educational library. Future legends receive
              guides first — never empty scanner URLs. Read the{" "}
              <Link href="/methodology" className="underline underline-offset-4">
                methodology
              </Link>{" "}
              and{" "}
              <Link href="/disclaimer" className="underline underline-offset-4">
                disclaimer
              </Link>
              .
            </p>
          </section>
        </div>
      </article>
    </MarketingShell>
  )
}
