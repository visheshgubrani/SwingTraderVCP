import Link from "next/link"

import { Reveal } from "@/components/landing/reveal"
import { getAllGuides } from "@/lib/learn/registry"
import { CANONICAL_SCANNER_PATH } from "@/lib/seo/config"

export function LearnHub() {
  const guides = getAllGuides()
  const liveLinked = guides.filter((g) => g.status === "live-linked")
  const research = guides.filter((g) => g.status !== "live-linked")

  return (
    <div className="landing-block">
      <div className="landing-container">
        <Reveal>
          <p className="landing-kicker">Learn</p>
        </Reveal>
        <Reveal>
          <h1 className="landing-display mt-5">Swing trading strategies and scanner techniques</h1>
        </Reveal>
        <Reveal>
          <p className="landing-lead mt-6 max-w-[62ch]">
            Plain-language guides for the ideas behind Swyingify scanners. Each page is individually written —
            educational, not SEBI-registered, and not endorsed by any named trader.
          </p>
        </Reveal>
        <Reveal>
          <p className="mt-8">
            <Link href={CANONICAL_SCANNER_PATH} className="landing-btn landing-btn-primary">
              Open the live Minervini VCP scanner
            </Link>
          </p>
        </Reveal>

        <section className="mt-16">
          <h2 className="landing-h2">Linked to the live scanner</h2>
          <ul className="mt-8 divide-y divide-[var(--landing-border)] border-y border-[var(--landing-border)]">
            {liveLinked.map((guide) => (
              <li key={guide.slug} className="py-6">
                <Link href={`/learn/${guide.slug}`} className="font-[family-name:var(--font-landing-mono)] text-xl text-[var(--landing-fg)] hover:underline">
                  {guide.title}
                </Link>
                <p className="mt-2 max-w-[60ch] text-[var(--landing-muted)]">{guide.definition}</p>
              </li>
            ))}
          </ul>
        </section>

        <section className="mt-16">
          <h2 className="landing-h2">Educational & research guides</h2>
          <p className="landing-lead mt-4 max-w-[60ch]">
            Future legend families stay educational until documented rules ship as versioned templates. No empty
            scanner pages.
          </p>
          <ul className="mt-8 divide-y divide-[var(--landing-border)] border-y border-[var(--landing-border)]">
            {research.map((guide) => (
              <li key={guide.slug} className="py-6">
                <div className="flex flex-wrap items-baseline gap-3">
                  <Link href={`/learn/${guide.slug}`} className="font-[family-name:var(--font-landing-mono)] text-xl text-[var(--landing-fg)] hover:underline">
                    {guide.title}
                  </Link>
                  <span className="border border-[var(--landing-border)] px-2 py-0.5 font-[family-name:var(--font-landing-mono)] text-[10px] uppercase tracking-widest text-[var(--landing-muted)]">
                    {guide.statusLabel}
                  </span>
                </div>
                <p className="mt-2 max-w-[60ch] text-[var(--landing-muted)]">{guide.definition}</p>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  )
}
