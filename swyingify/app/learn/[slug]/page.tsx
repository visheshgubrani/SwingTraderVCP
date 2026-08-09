import type { Metadata } from "next"
import { notFound } from "next/navigation"

import { GuideArticle } from "@/components/learn/guide-article"
import { MarketingShell } from "@/components/site/marketing-shell"
import { getAllGuides, getGuide, LEARN_GUIDE_SLUGS } from "@/lib/learn/registry"
import { buildPageMetadata } from "@/lib/seo/metadata"

export const dynamicParams = false

export function generateStaticParams() {
  return LEARN_GUIDE_SLUGS.map((slug) => ({ slug }))
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>
}): Promise<Metadata> {
  const { slug } = await params
  const guide = getGuide(slug)
  if (!guide) return {}

  return buildPageMetadata({
    title: guide.metaTitle,
    description: guide.description,
    path: `/learn/${guide.slug}`,
    ogType: "article",
    publishedTime: guide.publishedAt,
    modifiedTime: guide.reviewedAt,
  })
}

export default async function LearnGuidePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params
  const guide = getGuide(slug)
  if (!guide) notFound()

  // Touch registry so seo:check and builds keep guide count aligned.
  void getAllGuides()

  return (
    <MarketingShell active="learn">
      <GuideArticle guide={guide} />
    </MarketingShell>
  )
}
