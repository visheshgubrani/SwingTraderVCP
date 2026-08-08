import { Reveal } from "@/components/landing/reveal"
import { ROADMAP_ROWS } from "@/lib/landing/demo-data"
import { cn } from "@/lib/utils"

export function LandingRoadmapSection() {
  return (
    <section id="roadmap" className="landing-block">
      <div className="landing-container">
        <div className="landing-sec-head">
          <Reveal>
            <p className="landing-kicker">Roadmap</p>
          </Reveal>
          <Reveal>
            <h2 className="landing-h2 mt-5">One method now. The roster follows.</h2>
          </Reveal>
          <Reveal>
            <p className="landing-lead mt-6">
              Each strategy ships only when its rules are documented, versioned, and tested. One trader at a
              time.
            </p>
          </Reveal>
        </div>
        <Reveal>
          <div className="mt-14 border-t border-[var(--landing-border)]">
            {ROADMAP_ROWS.map((row) => (
              <div
                key={row.name}
                className="landing-roster-row grid items-baseline gap-2 border-b border-[var(--landing-border)] px-2 py-6 transition-colors max-sm:grid-cols-1 sm:grid-cols-[220px_1fr_auto] sm:gap-7"
              >
                <span className="font-[family-name:var(--font-landing-mono)] text-[22px] font-light text-[var(--landing-fg)] max-sm:order-2">
                  {row.name}
                </span>
                <p className="max-w-[52ch] text-base leading-relaxed text-[var(--landing-muted)] max-sm:order-3 sm:order-none">
                  {row.desc}
                </p>
                <span
                  className={cn(
                    "w-fit whitespace-nowrap border border-[var(--landing-border)] px-2.5 py-1 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)] max-sm:order-1",
                    row.live && "border-white/20 text-[var(--landing-fg)]",
                  )}
                >
                  {row.status}
                </span>
              </div>
            ))}
          </div>
        </Reveal>
      </div>
    </section>
  )
}
