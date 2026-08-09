import Link from "next/link"

import { Reveal } from "@/components/landing/reveal"
import { CANONICAL_SCANNER_PATH } from "@/lib/seo/config"

const ROWS = [
  {
    name: "Minervini VCP",
    status: "Live",
    live: true,
    href: CANONICAL_SCANNER_PATH,
    desc: "Stage 2 and volatility contraction pattern approximation for the Nifty 500. Standard top 25 updates after the close.",
  },
  {
    name: "William O’Neil (CAN SLIM)",
    status: "Educational",
    live: false,
    href: "/learn/william-oneil-can-slim",
    desc: "Research guide only. No live O’Neil scanner in V1.",
  },
  {
    name: "Qullamaggie",
    status: "Educational",
    live: false,
    href: "/learn/qullamaggie-breakout-strategy",
    desc: "Momentum-base education. Scanner family arrives only after documented rules.",
  },
  {
    name: "Darvas",
    status: "Educational",
    live: false,
    href: "/learn/darvas-box-strategy",
    desc: "Box theory education. Not a live scanner page.",
  },
  {
    name: "Livermore",
    status: "Educational",
    live: false,
    href: "/learn/jesse-livermore-pivotal-points",
    desc: "Pivotal-points education. Queued for research, not live screening.",
  },
] as const

export function ScannersDirectory() {
  return (
    <div className="landing-block">
      <div className="landing-container">
        <Reveal>
          <p className="landing-kicker">Scanners</p>
        </Reveal>
        <Reveal>
          <h1 className="landing-display mt-5">Swing trading stock scanners</h1>
        </Reveal>
        <Reveal>
          <p className="landing-lead mt-6 max-w-[62ch]">
            Rule-based strategy scanners for Indian equities. Minervini is live today. Every other legend is an
            educational guide until its template is researched, versioned, and tested.
          </p>
        </Reveal>

        <div className="mt-14 border-t border-[var(--landing-border)]">
          {ROWS.map((row) => (
            <div
              key={row.name}
              className="landing-roster-row grid items-baseline gap-2 border-b border-[var(--landing-border)] px-2 py-6 max-sm:grid-cols-1 sm:grid-cols-[240px_1fr_auto] sm:gap-7"
            >
              <Link
                href={row.href}
                className="font-[family-name:var(--font-landing-mono)] text-[22px] font-light text-[var(--landing-fg)] hover:underline max-sm:order-2"
              >
                {row.name}
              </Link>
              <p className="max-w-[52ch] text-base leading-relaxed text-[var(--landing-muted)] max-sm:order-3">
                {row.desc}
              </p>
              <span
                className={`w-fit whitespace-nowrap border border-[var(--landing-border)] px-2.5 py-1 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)] max-sm:order-1 ${row.live ? "border-white/20 text-[var(--landing-fg)]" : ""}`}
              >
                {row.status}
              </span>
            </div>
          ))}
        </div>

        <p className="mt-10 max-w-[62ch] text-sm leading-relaxed text-[var(--landing-muted)]">
          Independent rule-based approximations. Educational only. Not SEBI-registered. Not endorsed by the named
          traders. Read the{" "}
          <Link href="/methodology" className="underline underline-offset-4">
            methodology
          </Link>{" "}
          and{" "}
          <Link href="/disclaimer" className="underline underline-offset-4">
            disclaimer
          </Link>
          .
        </p>
      </div>
    </div>
  )
}
