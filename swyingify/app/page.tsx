import type { Metadata } from "next"

import { LandingPage } from "@/components/landing/landing-page"
import { buildPageMetadata } from "@/lib/seo/metadata"

export const metadata: Metadata = buildPageMetadata({
  title: "Swing trading stock scanner for Indian stocks",
  description:
    "Swyingify checks the Nifty 500 at every market close for an independent Minervini VCP approximation, then shows the shortlist and the reasoning behind it. Educational only — not SEBI-registered.",
  path: "/",
})

export default function Home() {
  return <LandingPage />
}
