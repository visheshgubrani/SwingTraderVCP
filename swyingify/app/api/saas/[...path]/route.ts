import { NextRequest, NextResponse } from "next/server"

import {
  hasFeature,
  isLatestStandardDate,
  isRecentHistoryDate,
  resolveAccess,
  type Feature,
} from "@/lib/entitlements"
import { createInternalAccessToken } from "@/lib/internal-api-access"

function backendBase(): string {
  return (
    process.env.API_URL?.trim() ||
    process.env.NEXT_PUBLIC_API_URL?.trim() ||
    ""
  ).replace(/\/$/, "")
}

async function proxySaas(
  request: NextRequest,
  pathSegments: string[],
): Promise<NextResponse> {
  const base = backendBase()
  if (!base) {
    return NextResponse.json(
      { detail: "API_URL is not configured" },
      { status: 503 },
    )
  }

  const upstreamPath = `/saas/${pathSegments.join("/")}`
  const url = new URL(upstreamPath, `${base}/`)
  request.nextUrl.searchParams.forEach((value, key) => {
    url.searchParams.set(key, value)
  })

  const asOfDate = url.searchParams.get("asOfDate")
  const isStrictResults =
    pathSegments[0] === "scans" &&
    pathSegments[1] === "minervini" &&
    pathSegments[2] === "strict" &&
    pathSegments[3] === "results"
  const isStrictLatest =
    pathSegments[0] === "scans" &&
    pathSegments[1] === "minervini" &&
    pathSegments[2] === "strict" &&
    pathSegments[3] === "latest"
  const isStandardResults =
    pathSegments[0] === "scans" &&
    pathSegments[1] === "minervini" &&
    pathSegments[2] === "standard" &&
    pathSegments[3] === "results"
  const isVariant =
    pathSegments[0] === "scans" &&
    pathSegments[1] === "minervini" &&
    pathSegments[2] === "variants"

  const forwardHeaders: HeadersInit = {
    Accept: "application/json",
  }
  const access = await resolveAccess(request.headers)

  function upgradeRequired(feature: Feature, detail: string) {
    return NextResponse.json(
      { detail, code: "upgrade_required", feature },
      { status: 403 },
    )
  }

  if (isStrictResults && !hasFeature(access, "scanner.strict")) {
    return upgradeRequired(
      "scanner.strict",
      "Minervini Strict is included with Swyingify Pro.",
    )
  }
  if (isStrictLatest && !hasFeature(access, "scanner.strict.preview")) {
    return upgradeRequired(
      "scanner.strict.preview",
      "The Strict result count is not available for this account.",
    )
  }
  if (isVariant && !hasFeature(access, "scanner.custom")) {
    return upgradeRequired(
      "scanner.custom",
      "Custom Nifty 500 scans are included with Swyingify Pro.",
    )
  }

  const isPublicLatestDate = Boolean(
    asOfDate &&
    isStandardResults &&
    (await isLatestStandardDate(asOfDate)),
  )
  const isProtectedHistory = Boolean(asOfDate && !isPublicLatestDate)

  if (isProtectedHistory && asOfDate) {
    const hasFullHistory = hasFeature(access, "scanner.history.full")
    const recentLimit = access.limits.historySessions
    const hasRecentHistory =
      hasFeature(access, "scanner.history.recent") &&
      access.isAuthenticated &&
      recentLimit !== null &&
      (await isRecentHistoryDate(asOfDate, recentLimit))
    if (!hasFullHistory && !hasRecentHistory) {
      if (!access.isAuthenticated) {
        return NextResponse.json(
          { detail: "Sign in required for past scan dates." },
          { status: 401 },
        )
      }
      return upgradeRequired(
        "scanner.history.full",
        `Free accounts include the latest ${recentLimit ?? 20} trading sessions.`,
      )
    }
  }

  if (isProtectedHistory || isStrictResults || isVariant) {
    const accessToken = createInternalAccessToken(access)
    if (!accessToken) {
      return NextResponse.json(
        { detail: "Protected SaaS APIs are not configured." },
        { status: 503 },
      )
    }
    forwardHeaders["X-Swyingify-Access"] = accessToken
  }

  if (request.method !== "GET") {
    forwardHeaders["Content-Type"] =
      request.headers.get("Content-Type") || "application/json"
  }

  const upstream = await fetch(url.toString(), {
    method: request.method,
    headers: forwardHeaders,
    body: request.method === "GET" ? undefined : await request.text(),
    cache: "no-store",
  })

  const body = await upstream.text()
  return new NextResponse(body, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("Content-Type") || "application/json",
    },
  })
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params
  return proxySaas(request, path)
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params
  return proxySaas(request, path)
}
