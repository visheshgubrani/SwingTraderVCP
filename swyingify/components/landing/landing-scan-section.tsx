import { ScannerDemo } from "@/components/landing/scanner-demo"
import { Reveal } from "@/components/landing/reveal"

export function LandingScanSection() {
  return (
    <section id="scan" className="landing-block">
      <div className="landing-container">
        <div className="landing-sec-head">
          <Reveal>
            <p className="landing-kicker">The scan</p>
          </Reveal>
          <Reveal>
            <h2 className="landing-h2 mt-5">A shortlist, not another dashboard.</h2>
          </Reveal>
          <Reveal>
            <p className="landing-lead mt-6">
              The standard template checks the Nifty 500, ranks the candidates, and exposes the six checks
              behind each result. Use it to decide which charts deserve your time. The preview below uses
              real symbols; the live shortlist publishes after every close.
            </p>
          </Reveal>
        </div>
        <ScannerDemo />
      </div>
    </section>
  )
}
