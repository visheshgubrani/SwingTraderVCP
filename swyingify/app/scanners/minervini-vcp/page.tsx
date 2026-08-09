import type { Metadata } from "next"

import { ScannerBoardPage } from "@/components/scanner-board/scanner-board-page"
import { getScannerBoardData, stockPath } from "@/lib/scanner/board-data"
import type { ScannerPreset } from "@/lib/scanner/types"
import { CANONICAL_SCANNER_PATH } from "@/lib/seo/config"
import { JsonLd, scannerCollectionJsonLd } from "@/lib/seo/json-ld"
import { buildPageMetadata, scannerQueryIsFiltered } from "@/lib/seo/metadata"

type SearchParams = Record<string, string | string[] | undefined>

export async function generateMetadata({
  searchParams,
}: {
  searchParams: Promise<SearchParams>
}): Promise<Metadata> {
  const params = await searchParams
  const filtered = scannerQueryIsFiltered(params)

  return buildPageMetadata({
    title: "Minervini VCP scanner — Nifty 500",
    description:
      "Browse tonight’s Minervini VCP shortlist for the Nifty 500. Independent Stage 2 / volatility contraction approximation with Wide and Standard boards after every close.",
    path: CANONICAL_SCANNER_PATH,
    noIndex: filtered,
    robots: filtered ? { index: false, follow: true } : undefined,
  })
}

export default async function MinerviniVcpScannerPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>
}) {
  const params = await searchParams
  const preset: ScannerPreset = params.preset === "wide" ? "wide" : "standard"
  const board = getScannerBoardData(preset)

  return (
    <>
      {board.isLiveData ? (
        <JsonLd
          data={scannerCollectionJsonLd({
            name: "Minervini VCP scanner — Nifty 500",
            description:
              "Independent rule-based Minervini VCP approximation results for the Nifty 500.",
            path: CANONICAL_SCANNER_PATH,
            asOfDate: board.asOfDate,
            items: board.results.slice(0, 20).map((row, index) => ({
              name: `${row.symbol} — ${row.companyName}`,
              url: stockPath(row.symbol),
              position: index + 1,
            })),
          })}
        />
      ) : null}
      <ScannerBoardPage
        initialPreset={board.preset}
        initialResults={board.results}
        asOfDate={board.asOfDate}
        isLiveData={board.isLiveData}
      />
    </>
  )
}
