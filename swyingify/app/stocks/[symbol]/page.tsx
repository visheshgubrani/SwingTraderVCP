import { notFound } from "next/navigation"

import { StockPageShell } from "@/components/stock/stock-page-shell"
import { getPreviewResult } from "@/lib/scanner/fixtures"

export async function generateMetadata({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params
  const result = getPreviewResult(symbol)
  return {
    title: result ? `${result.symbol} setup details` : "Stock setup",
    description: result
      ? `Daily chart, scanner checks, and key levels for ${result.companyName} (${result.symbol}).`
      : undefined,
  }
}

export default async function StockPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params
  const result = getPreviewResult(symbol)
  if (!result) notFound()

  return <StockPageShell result={result} />
}
