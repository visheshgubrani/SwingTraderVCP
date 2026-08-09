import type { Metadata } from "next"
import { notFound } from "next/navigation"

import { StockPageShell } from "@/components/stock/stock-page-shell"
import { getPreviewResult, getPreviewStockSlugs } from "@/lib/scanner/fixtures"
import { normalizeStockSymbol } from "@/lib/scanner/board-data"
import { buildPageMetadata } from "@/lib/seo/metadata"

export const dynamicParams = false

export function generateStaticParams() {
  return getPreviewStockSlugs().map((symbol) => ({ symbol }))
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ symbol: string }>
}): Promise<Metadata> {
  const { symbol } = await params
  const canonical = normalizeStockSymbol(symbol)
  const result = getPreviewResult(canonical)
  if (!result) notFound()

  return buildPageMetadata({
    title: `${result.symbol} setup details`,
    description: `Daily chart, scanner checks, and key levels for ${result.companyName} (${result.symbol}). Preview data — not indexed.`,
    path: `/stocks/${canonical}`,
    noIndex: true,
    robots: { index: false, follow: true },
  })
}

export default async function StockPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params
  const canonical = normalizeStockSymbol(symbol)
  const result = getPreviewResult(canonical)
  if (!result) notFound()

  // Fixture / preview stock pages stay noindex until real dated backend records exist.
  // Uppercase URLs are 308'd to lowercase by middleware.
  return <StockPageShell result={result} />
}
