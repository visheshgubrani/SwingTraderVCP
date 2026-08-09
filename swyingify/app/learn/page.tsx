import type { Metadata } from "next"

import { LearnHub } from "@/components/learn/learn-hub"
import { MarketingShell } from "@/components/site/marketing-shell"
import { buildPageMetadata } from "@/lib/seo/metadata"

export const metadata: Metadata = buildPageMetadata({
  title: "Swing trading strategies and scanner techniques",
  description:
    "Educational hub for VCP, Minervini trend template, Stage 2, relative strength, volume dry-up, and research guides for future scanner families.",
  path: "/learn",
})

export default function LearnPage() {
  return (
    <MarketingShell active="learn">
      <LearnHub />
    </MarketingShell>
  )
}
