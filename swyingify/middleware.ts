import { NextResponse } from "next/server"
import type { NextRequest } from "next/server"

/** Normalize stock slugs to one lowercase canonical form. */
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl
  const match = pathname.match(/^\/stocks\/([^/]+)\/?$/)
  if (!match) return NextResponse.next()

  const raw = match[1]
  const canonical = decodeURIComponent(raw).trim().toLowerCase()
  if (!canonical || raw === canonical) return NextResponse.next()

  const url = request.nextUrl.clone()
  url.pathname = `/stocks/${canonical}`
  return NextResponse.redirect(url, 308)
}

export const config = {
  matcher: ["/stocks/:symbol"],
}
