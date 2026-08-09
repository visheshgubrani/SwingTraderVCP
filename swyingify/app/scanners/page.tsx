import type { Metadata } from "next"

import { ScannersDirectory } from "@/components/scanners/scanners-directory"
import { MarketingShell } from "@/components/site/marketing-shell"
import { buildPageMetadata } from "@/lib/seo/metadata"

export const metadata: Metadata = buildPageMetadata({
  title: "Swing trading stock scanners",
  description:
    "Directory of Swyingify strategy scanners for Indian equities. Minervini VCP is live; other legends are educational guides until researched templates ship.",
  path: "/scanners",
})

export default function ScannersPage() {
  return (
    <MarketingShell active="scanners">
      <ScannersDirectory />
    </MarketingShell>
  )
}
