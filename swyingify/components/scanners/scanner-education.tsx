import Link from "next/link"

import { Breadcrumbs } from "@/components/site/breadcrumbs"
import { CANONICAL_SCANNER_PATH } from "@/lib/seo/config"

export function ScannerEducation({ asOfDate, isLiveData }: { asOfDate: string; isLiveData: boolean }) {
  return (
    <section className="border-t border-[var(--landing-border)] pb-20 pt-16">
      <div className="mx-auto max-w-[760px] px-6 max-sm:px-3">
        <Breadcrumbs
          items={[
            { label: "Home", href: "/" },
            { label: "Scanners", href: "/scanners" },
            { label: "Minervini VCP" },
          ]}
        />

        <h2 className="mt-10 font-[family-name:var(--font-landing-mono)] text-[clamp(24px,3vw,34px)] font-light text-[var(--landing-fg)]">
          How this VCP scanner works
        </h2>
        <p className="mt-4 text-base leading-relaxed text-[var(--landing-fg-2)]">
          Swyingify runs an independent rule-based approximation of Mark Minervini&apos;s Stage 2 / volatility
          contraction ideas over the Nifty 500 after each cash-market close. The beta board ships the Standard
          shortlist (top 25). Neither the board nor any future preset places orders or issues buy signals.
        </p>
        <p className="mt-4 text-sm text-[var(--landing-muted)]">
          Latest close date on this board: <time dateTime={asOfDate}>{asOfDate}</time>
          {isLiveData ? "" : " · illustrative preview rows until live backend results replace them"}.
        </p>

        <h3 className="mt-10 font-[family-name:var(--font-landing-mono)] text-lg text-[var(--landing-fg)]">
          Rules the board approximates
        </h3>
        <ul className="mt-3 list-disc space-y-2 pl-5 text-[var(--landing-fg-2)]">
          <li>Stage 2-style trend structure via moving-average relationships.</li>
          <li>Proximity to a recent high / pivot context.</li>
          <li>Volatility contraction proxies (ATR / band width style checks).</li>
          <li>Volume dry-up during the base.</li>
          <li>Relative strength versus the screened universe.</li>
        </ul>

        <h3 className="mt-10 font-[family-name:var(--font-landing-mono)] text-lg text-[var(--landing-fg)]">
          Limitations
        </h3>
        <ul className="mt-3 list-disc space-y-2 pl-5 text-[var(--landing-fg-2)]">
          <li>End-of-day only — intraday breaks are not monitored here.</li>
          <li>Independent engineering thresholds, not a licensed Minervini product.</li>
          <li>No fundamentals, news, or broker connectivity on this page.</li>
          <li>Rank and score describe rule fit, not expected return.</li>
        </ul>

        <h3 className="mt-10 font-[family-name:var(--font-landing-mono)] text-lg text-[var(--landing-fg)]">
          Common questions
        </h3>
        <dl className="mt-4 space-y-5 text-[var(--landing-fg-2)]">
          <div>
            <dt className="font-medium text-[var(--landing-fg)]">Is this investment advice?</dt>
            <dd className="mt-1">
              No. Swyingify is educational software, not SEBI-registered, and not endorsed by Mark Minervini or any
              named trader.
            </dd>
          </div>
          <div>
            <dt className="font-medium text-[var(--landing-fg)]">Why are some legend scanners missing?</dt>
            <dd className="mt-1">
              V1 ships Minervini only. Other legends have{" "}
              <Link href="/learn" className="underline underline-offset-4">
                educational guides
              </Link>{" "}
              until researched templates exist — we do not publish empty scanner pages.
            </dd>
          </div>
          <div>
            <dt className="font-medium text-[var(--landing-fg)]">Where can I learn the pattern language?</dt>
            <dd className="mt-1">
              Start with{" "}
              <Link href="/learn/vcp-pattern" className="underline underline-offset-4">
                What is VCP?
              </Link>{" "}
              and the{" "}
              <Link href="/learn/minervini-trend-template" className="underline underline-offset-4">
                trend template
              </Link>{" "}
              guide, then return to{" "}
              <Link href={CANONICAL_SCANNER_PATH} className="underline underline-offset-4">
                this board
              </Link>
              .
            </dd>
          </div>
        </dl>
      </div>
    </section>
  )
}
