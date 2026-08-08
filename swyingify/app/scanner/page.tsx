import type { Metadata } from "next"

import { ScannerBoardPage } from "@/components/scanner-board/scanner-board-page"

export const metadata: Metadata = {
  title: "The daily setup board",
  description:
    "Tonight's Minervini VCP shortlist across the Nifty 500, ranked with trend, price, volume and RS rating for every setup.",
}

export default function ScannerRoutePage() {
  return <ScannerBoardPage />
}
