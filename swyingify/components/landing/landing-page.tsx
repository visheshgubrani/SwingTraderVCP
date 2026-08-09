import { GeistMono } from "geist/font/mono"
import { GeistSans } from "geist/font/sans"

import { LandingCtaSection } from "@/components/landing/landing-cta-section"
import { LandingFooter } from "@/components/landing/landing-footer"
import { LandingHero } from "@/components/landing/landing-hero"
import { LandingMethodSection } from "@/components/landing/landing-method-section"
import { LandingNav } from "@/components/landing/landing-nav"
import { LandingRoadmapSection } from "@/components/landing/landing-roadmap-section"
import { LandingScanSection } from "@/components/landing/landing-scan-section"
import "@/components/landing/landing.css"
import type { ScanDemoItem } from "@/lib/landing/demo-data"

type LandingPageProps = {
  scans: ScanDemoItem[]
  heroPreview: Array<{ sym: string; stage: string; score: number }>
  asOfDate: string
  isLiveData: boolean
}

export function LandingPage({ scans, heroPreview, asOfDate, isLiveData }: LandingPageProps) {
  return (
    <div
      className={`landing min-h-screen overflow-x-hidden ${GeistSans.variable} ${GeistMono.variable}`}
      style={
        {
          ["--font-landing-body" as string]: "var(--font-geist-sans)",
          ["--font-landing-mono" as string]: "var(--font-geist-mono)",
        } as React.CSSProperties
      }
    >
      <LandingNav />
      <main id="top">
        <LandingHero heroPreview={heroPreview} isLiveData={isLiveData} />
        <LandingScanSection scans={scans} asOfDate={asOfDate} isLiveData={isLiveData} />
        <LandingMethodSection />
        <LandingRoadmapSection />
        <LandingCtaSection />
      </main>
      <LandingFooter />
    </div>
  )
}
