import { ScannerDemo } from "@/components/landing/scanner-demo"
import { Reveal } from "@/components/landing/reveal"
import type { ScanDemoItem } from "@/lib/landing/demo-data"

type LandingScanSectionProps = {
  scans: ScanDemoItem[]
  asOfDate: string
  isLiveData: boolean
}

export function LandingScanSection({ scans, asOfDate, isLiveData }: LandingScanSectionProps) {
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
              The Standard template checks the Nifty 500, ranks the candidates, and exposes the checks behind each
              result. Use it to decide which charts deserve your time. The board below shows tonight&apos;s Standard
              shortlist{isLiveData ? "" : " preview"} after every cash-market close.
            </p>
          </Reveal>
        </div>
        <ScannerDemo scans={scans} asOfDate={asOfDate} isLiveData={isLiveData} />
      </div>
    </section>
  )
}
