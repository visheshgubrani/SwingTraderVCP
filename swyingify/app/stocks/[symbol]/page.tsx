import type { Metadata } from "next"
import { notFound } from "next/navigation"

import { StockPageShell } from "@/components/stock/stock-page-shell"
import { getScannerResult, getScannerStockSlugs, normalizeStockSymbol } from "@/lib/scanner/board-data"
import { buildPageMetadata } from "@/lib/seo/metadata"

export const dynamicParams = true

export async function generateStaticParams() {
  // Prefer fixture slugs for static builds; live symbols resolve dynamically.
  if (process.env.API_URL?.trim() || process.env.NEXT_PUBLIC_API_URL?.trim()) {
    return []
  }
  const slugs = await getScannerStockSlugs()
  return slugs.map((symbol) => ({ symbol }))
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ symbol: string }>
}): Promise<Metadata> {
  const { symbol } = await params
  const canonical = normalizeStockSymbol(symbol)
  const result = await getScannerResult(canonical)
  if (!result) notFound()

  return buildPageMetadata({
    title: `${result.symbol} setup details`,
    description: `Daily chart, scanner checks, and key levels for ${result.companyName} (${result.symbol}). Educational only — not indexed.`,
    path: `/stocks/${canonical}`,
    noIndex: true,
    robots: { index: false, follow: true },
  })
}

export default async function StockPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params
  const canonical = normalizeStockSymbol(symbol)
  const result = await getScannerResult(canonical)
  if (!result) notFound()

  return <StockPageShell result={result} />
}
