import Link from "next/link"

import { Reveal } from "@/components/landing/reveal"

export function LandingCtaSection() {
  return (
    <section id="cta" className="landing-block text-center">
      <div className="landing-container">
        <Reveal>
          <h2 className="landing-h2 mx-auto">Tomorrow&apos;s base is already forming.</h2>
        </Reveal>
        <Reveal>
          <p className="landing-lead mx-auto mt-6">
            The scan runs at every market close. Come back in the evening for today&apos;s shortlist — or keep
            an account for watchlists and the stricter scans.
          </p>
        </Reveal>
        <Reveal>
          <div className="mt-10 flex flex-wrap justify-center gap-[18px]">
            <Link href="/scanners/minervini-vcp" className="landing-btn landing-btn-primary">
              Browse today&apos;s scan
            </Link>
          </div>
        </Reveal>
        <Reveal>
          <p className="landing-kicker mt-6">
            Standard scans are free in beta. No account needed for today&apos;s top 25.
          </p>
        </Reveal>
      </div>
    </section>
  )
}
