import Link from "next/link"

import { Reveal } from "@/components/landing/reveal"
import { HERO_PREVIEW } from "@/lib/landing/demo-data"

function padRank(n: number) {
  return (n < 10 ? "0" : "") + n
}

export function LandingHero() {
  return (
    <header className="landing-hero">
      <div className="landing-container">
        <div className="landing-hero-layout">
          <div>
            <Reveal>
              <p className="landing-kicker">Minervini VCP · Nifty 500 · End of day</p>
            </Reveal>
            <Reveal>
              <h1 className="landing-display mt-[18px]">Find the few stocks worth your attention.</h1>
            </Reveal>
            <Reveal>
              <p className="landing-lead mt-[22px]">
                Stop opening 500 charts looking for a clean base. Swyingify checks the Nifty 500 at every
                market close for Mark Minervini&apos;s volatility contraction pattern, then shows the
                shortlist and the reasoning behind it.
              </p>
            </Reveal>
            <Reveal>
              <div className="landing-hero-actions">
                <Link href="/scanner" className="landing-btn landing-btn-primary">
                  Browse today&apos;s scan
                </Link>
                <Link href="#method" className="landing-text-link">
                  Read the method
                </Link>
              </div>
            </Reveal>
            <Reveal>
              <p className="landing-hero-meta">Educational only · No orders · No broker</p>
            </Reveal>
          </div>

          <Reveal>
            <aside className="landing-hero-aside" aria-label="Illustrative shortlist preview">
              <div className="landing-hero-aside-head">
                <span className="landing-hero-aside-title">Tonight&apos;s output</span>
                <span className="landing-hero-aside-note">Preview · Real symbols</span>
              </div>
              <ol className="landing-mini-list">
                {HERO_PREVIEW.map((row, i) => (
                  <li key={row.sym}>
                    <span className="landing-mini-rank">{padRank(i + 1)}</span>
                    <span>
                      <span className="landing-mini-symbol">{row.sym}</span>
                      <span className="landing-mini-state">{row.stage}</span>
                    </span>
                    <span className="landing-mini-score">{row.score}</span>
                  </li>
                ))}
              </ol>
            </aside>
          </Reveal>
        </div>
      </div>
    </header>
  )
}
