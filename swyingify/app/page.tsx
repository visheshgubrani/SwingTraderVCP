import type { Metadata } from "next"

import { LandingPage } from "@/components/landing/landing-page"

export const metadata: Metadata = {
  title: "Swyingify — Find the few stocks worth your attention.",
  description:
    "Swyingify checks the Nifty 500 at every market close for Mark Minervini's volatility contraction pattern, then shows the shortlist and the reasoning behind it.",
}

export default function Home() {
  return <LandingPage />
}
