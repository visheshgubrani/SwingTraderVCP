import { headers } from "next/headers"
import { NextResponse } from "next/server"

import { resolveAccess } from "@/lib/entitlements"

export const dynamic = "force-dynamic"

export async function GET() {
  const access = await resolveAccess(await headers())
  return NextResponse.json(access, {
    headers: { "Cache-Control": "private, no-store" },
  })
}
