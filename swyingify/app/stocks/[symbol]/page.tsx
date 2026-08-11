import type { Metadata } from "next"
import { headers } from "next/headers"
import { notFound, redirect } from "next/navigation"

import { StockPageShell } from "@/components/stock/stock-page-shell"
import { hasFeature, resolveAccess } from "@/lib/entitlements"
import { createInternalAccessToken } from "@/lib/internal-api-access"
import {
  getScannerResult,
  getScannerStockSlugs,
  hasBackendConfigured,
  normalizeStockSymbol,
} from "@/lib/scanner/board-data"
import { fetchSymbolCandles } from "@/lib/scanner/api"
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

  return buildPageMetadata({
    title: `${canonical.toUpperCase()} setup details`,
    description: `Daily chart, scanner checks, and key levels for ${canonical.toUpperCase()}. Educational only — not indexed.`,
    path: `/stocks/${canonical}`,
    noIndex: true,
    robots: { index: false, follow: true },
  })
}

export default async function StockPage({
  params,
  searchParams,
}: {
  params: Promise<{ symbol: string }>
  searchParams: Promise<{ preset?: string }>
}) {
  const { symbol } = await params
  const canonical = normalizeStockSymbol(symbol)
  const requestedPreset = (await searchParams).preset
  const preset = requestedPreset === "strict" ? "strict" : "standard"
  let accessToken: string | undefined

  if (preset === "strict") {
    const access = await resolveAccess(await headers())
    if (!hasFeature(access, "scanner.strict")) {
      redirect("/scanners/minervini-vcp/strict")
    }
    accessToken = createInternalAccessToken(access) ?? undefined
  }

  const result = await getScannerResult(canonical, preset, { accessToken })
  if (!result) notFound()

  const isLiveData = hasBackendConfigured()
  let enriched = result
  if (isLiveData) {
    try {
      const candlePayload = await fetchSymbolCandles(canonical.toUpperCase())
      enriched = { ...result, candles: candlePayload.candles }
    } catch {
      enriched = { ...result, candles: [] }
    }
  }

  return <StockPageShell result={enriched} isLiveData={isLiveData} />
}
