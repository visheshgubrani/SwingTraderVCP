import Link from "next/link"

import { Reveal } from "@/components/landing/reveal"
import { CANONICAL_SCANNER_PATH } from "@/lib/seo/config"

function padRank(n: number) {
  return (n < 10 ? "0" : "") + n
}

type LandingHeroProps = {
  heroPreview: Array<{ sym: string; stage: string; score: number }>
  isLiveData: boolean
}

export function LandingHero({ heroPreview, isLiveData }: LandingHeroProps) {
  return (
    <header className="landing-hero">
      <div className="landing-container">
        <div className="landing-hero-layout">
          <div>
            <Reveal>
              <p className="landing-kicker">Minervini VCP · Standard · Nifty 500 · End of day</p>
            </Reveal>
            <Reveal>
              <h1 className="landing-display mt-[18px]">Swing trading stock scanner for Indian stocks</h1>
            </Reveal>
            <Reveal>
              <p className="landing-lead mt-[22px]">
                Find the few stocks worth your attention. Stop opening 500 charts looking for a clean base.
                Swyingify checks the Nifty 500 at every market close for an independent approximation of Mark
                Minervini&apos;s volatility contraction pattern, then shows the Standard shortlist (top 25) and the
                reasoning behind it.
              </p>
            </Reveal>
            <Reveal>
              <div className="landing-hero-actions">
                <Link href={CANONICAL_SCANNER_PATH} className="landing-btn landing-btn-primary">
                  Browse today&apos;s scan
                </Link>
                <Link href="/learn" className="landing-text-link">
                  Read the guides
                </Link>
              </div>
            </Reveal>
            <Reveal>
              <p className="landing-hero-meta">Educational only · No orders · Not SEBI-registered · Beta</p>
            </Reveal>
          </div>

          <Reveal>
            <aside className="landing-hero-aside" aria-label="Tonight shortlist preview">
              <div className="landing-hero-aside-head">
                <span className="landing-hero-aside-title">Tonight&apos;s output</span>
                <span className="landing-hero-aside-note">
                  {isLiveData ? "Live · Standard top 25" : "Preview · Real symbols"}
                </span>
              </div>
              <ol className="landing-mini-list">
                {heroPreview.map((row, i) => (
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
