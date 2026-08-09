import type { Metadata } from "next"

import { LandingPage } from "@/components/landing/landing-page"
import { toHeroPreview, toLandingScanItem } from "@/lib/landing/from-scanner"
import { HERO_PREVIEW, LANDING_SCANS } from "@/lib/landing/demo-data"
import { getScannerBoardData } from "@/lib/scanner/board-data"
import { buildPageMetadata } from "@/lib/seo/metadata"

export const metadata: Metadata = buildPageMetadata({
  title: "Swing trading stock scanner for Indian stocks",
  description:
    "Swyingify checks the Nifty 500 at every market close for an independent Minervini VCP approximation, then shows the shortlist and the reasoning behind it. Educational only — not SEBI-registered.",
  path: "/",
})

export default async function Home() {
  const board = await getScannerBoardData("standard")
  const scans =
    board.isLiveData && board.results.length > 0
      ? board.results.slice(0, 6).map(toLandingScanItem)
      : [...LANDING_SCANS]
  const hero =
    board.isLiveData && board.results.length > 0
      ? toHeroPreview(board.results)
      : [...HERO_PREVIEW]

  return (
    <LandingPage
      scans={scans}
      heroPreview={hero}
      asOfDate={board.asOfDate}
      isLiveData={board.isLiveData}
    />
  )
}
