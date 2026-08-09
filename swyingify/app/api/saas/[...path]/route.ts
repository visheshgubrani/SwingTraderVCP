import { headers } from "next/headers"
import { NextRequest, NextResponse } from "next/server"

import { auth } from "@/lib/auth"

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
  const forwardHeaders: HeadersInit = {
    Accept: "application/json",
  }

  // Past / as-of history requires a Better Auth session; then we attach the
  // shared internal key so FastAPI will serve historical dates.
  if (asOfDate) {
    const session = await auth.api.getSession({ headers: await headers() })
    if (!session) {
      return NextResponse.json(
        { detail: "Sign in required for past scan dates." },
        { status: 401 },
      )
    }
    const internalKey = process.env.SAAS_INTERNAL_API_KEY?.trim()
    if (!internalKey) {
      return NextResponse.json(
        { detail: "Historical SaaS scans are not configured." },
        { status: 503 },
      )
    }
    forwardHeaders["X-Swyingify-Internal-Key"] = internalKey
  }

  const upstream = await fetch(url.toString(), {
    method: "GET",
    headers: forwardHeaders,
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
