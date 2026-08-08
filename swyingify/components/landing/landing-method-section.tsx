import { Reveal } from "@/components/landing/reveal"
import { METHOD_STEPS } from "@/lib/landing/demo-data"

export function LandingMethodSection() {
  return (
    <section id="method" className="landing-block">
      <div className="landing-container">
        <div className="landing-sec-head">
          <Reveal>
            <p className="landing-kicker">The method</p>
          </Reveal>
          <Reveal>
            <h2 className="landing-h2 mt-5">A base is supply running out.</h2>
          </Reveal>
          <Reveal>
            <p className="landing-lead mt-6">
              Minervini&apos;s volatility contraction pattern, stated plainly. Five checks, in the order the
              scanner reads them — a decision you can follow, not a black box.
            </p>
          </Reveal>
        </div>
        <Reveal>
          <div className="mt-14 border-t border-[var(--landing-border)]">
            {METHOD_STEPS.map((step) => (
              <article
                key={step.num}
                className="landing-step grid gap-4 border-b border-[var(--landing-border)] py-7 transition-colors max-sm:grid-cols-[32px_1fr] sm:grid-cols-[88px_1fr] sm:gap-7 sm:px-2"
              >
                <span className="font-[family-name:var(--font-landing-mono)] text-xl font-light text-[var(--landing-muted)]">
                  {step.num}
                </span>
                <div className="grid items-baseline gap-2.5 lg:grid-cols-[240px_1fr] lg:gap-8">
                  <h3 className="font-[family-name:var(--font-landing-mono)] text-lg uppercase tracking-wide text-[var(--landing-fg)]">
                    {step.name}
                  </h3>
                  <p className="max-w-[58ch] text-base leading-relaxed text-[var(--landing-fg-2)]">{step.copy}</p>
                </div>
              </article>
            ))}
          </div>
        </Reveal>
      </div>
    </section>
  )
}
