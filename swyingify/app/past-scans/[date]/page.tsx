import type { Metadata } from "next"
import { headers } from "next/headers"
import { notFound, redirect } from "next/navigation"

import { ScannerBoardPage } from "@/components/scanner-board/scanner-board-page"
import {
  hasFeature,
  isRecentHistoryDate,
  resolveAccess,
} from "@/lib/entitlements"
import { createInternalAccessToken } from "@/lib/internal-api-access"
import { getScannerBoardData } from "@/lib/scanner/board-data"
import { buildPageMetadata } from "@/lib/seo/metadata"

export const metadata: Metadata = buildPageMetadata({
  title: "Archived Minervini Standard scan",
  description: "A prior Swyingify Minervini Standard EOD shortlist.",
  path: "/past-scans",
  noIndex: true,
})

export const dynamic = "force-dynamic"

export default async function PastScanDatePage({
  params,
}: {
  params: Promise<{ date: string }>
}) {
  const { date } = await params
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(Date.parse(`${date}T00:00:00Z`))) {
    notFound()
  }

  const access = await resolveAccess(await headers())
  if (!access.isAuthenticated && !access.isBypassed) {
    redirect(`/sign-in?next=${encodeURIComponent(`/past-scans/${date}`)}`)
  }
  const full = hasFeature(access, "scanner.history.full")
  const recentLimit = access.limits.historySessions ?? 20
  if (!full && !(await isRecentHistoryDate(date, recentLimit))) {
    redirect("/pricing")
  }

  const accessToken = createInternalAccessToken(access) ?? undefined
  const board = await getScannerBoardData("standard", {
    accessToken,
    asOfDate: date,
  })
  if (board.status === "error") notFound()

  const accessNotice = access.bypassReason === "development"
    ? "Developer access · production history limits are disabled"
    : access.bypassReason === "admin"
      ? "Admin access · complete archive unlocked"
      : `Archived Standard board · ${date}`

  return (
    <ScannerBoardPage
      initialPreset="standard"
      initialResults={board.results}
      asOfDate={date}
      isLiveData
      historical
      accessNotice={accessNotice}
    />
  )
}
