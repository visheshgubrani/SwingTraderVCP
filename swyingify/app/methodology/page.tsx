import type { Metadata } from "next"
import Link from "next/link"

import { MarketingShell } from "@/components/site/marketing-shell"
import { CANONICAL_SCANNER_PATH } from "@/lib/seo/config"
import { buildPageMetadata } from "@/lib/seo/metadata"

export const metadata: Metadata = buildPageMetadata({
  title: "Scanner methodology",
  description:
    "How Swyingify versions Minervini VCP scan templates, what data we use, and how educational guides relate to live boards.",
  path: "/methodology",
})

export default function MethodologyPage() {
  return (
    <MarketingShell active="trust">
      <article className="landing-block">
        <div className="landing-container max-w-[760px]">
          <p className="landing-kicker">Methodology</p>
          <h1 className="landing-display mt-5">How the scanners are built</h1>
          <p className="landing-lead mt-6">
            Swyingify screens are independent rule-based approximations. Template configs are versioned and
            snapshotted per run so a published board remains reproducible.
          </p>

          <section className="mt-12 space-y-4 text-base leading-relaxed text-[var(--landing-fg-2)]">
            <h2 className="landing-h2 text-[clamp(22px,3vw,30px)]">Universe & cadence</h2>
            <p>
              V1 covers Indian cash equities in the Nifty 500 universe using end-of-day candles. Global daily
              templates run after the close; results are browsable without placing orders.
            </p>
          </section>

          <section className="mt-12 space-y-4 text-base leading-relaxed text-[var(--landing-fg-2)]">
            <h2 className="landing-h2 text-[clamp(22px,3vw,30px)]">Minervini family (live)</h2>
            <p>
              Wide and Standard presets approximate Stage 2 trend structure, near-high / pivot context, volatility
              contraction proxies, volume dry-up, and relative strength. Exact thresholds live in versioned template
              config — not in marketing copy.
            </p>
            <p>
              Open the live board:{" "}
              <Link href={CANONICAL_SCANNER_PATH} className="underline underline-offset-4">
                Minervini VCP scanner
              </Link>
              .
            </p>
          </section>

          <section className="mt-12 space-y-4 text-base leading-relaxed text-[var(--landing-fg-2)]">
            <h2 className="landing-h2 text-[clamp(22px,3vw,30px)]">Sources & education</h2>
            <p>
              Guides cite primary public sources (books and established teaching). They explain what screens can
              measure and what still requires human judgment. Research-status legends link to education only until a
              tested template exists.
            </p>
          </section>

          <section className="mt-12 space-y-4 text-base leading-relaxed text-[var(--landing-fg-2)]">
            <h2 className="landing-h2 text-[clamp(22px,3vw,30px)]">Versioning</h2>
            <p>
              When gates change, we bump the template version and note material changes on this page. Scores from
              different versions should not be compared as if they were identical.
            </p>
          </section>
        </div>
      </article>
    </MarketingShell>
  )
}
