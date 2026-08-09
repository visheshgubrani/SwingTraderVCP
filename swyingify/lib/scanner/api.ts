import type {
  DailyCandle,
  QualificationFingerprint,
  ScannerLatestMeta,
  ScannerResultPreview,
} from "./types"

function apiBaseUrl(): string {
  // Prefer same-origin BFF in the browser; direct FastAPI URL on the server.
  if (typeof window !== "undefined") {
    return ""
  }
  const fromEnv =
    process.env.API_URL?.trim() ||
    process.env.NEXT_PUBLIC_API_URL?.trim() ||
    ""
  return fromEnv.replace(/\/$/, "")
}

export function getScannerApiBaseUrl(): string {
  // Empty string means use same-origin `/api/saas/...` BFF (browser + Next).
  // Server components still need a backend URL unless they call the BFF absolute.
  if (typeof window !== "undefined") return ""
  return apiBaseUrl()
}

function saasPath(path: string): string {
  const base = apiBaseUrl()
  if (base) return `${base}${path}`
  // Same-origin Next BFF
  return `/api${path}`
}

async function saasFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const url = saasPath(path)
  if (typeof window === "undefined" && !apiBaseUrl() && url.startsWith("/api")) {
    // Server-side without API_URL: try absolute self if SITE_URL is set, else fail.
    const site = process.env.BETTER_AUTH_URL || process.env.SITE_URL || "http://localhost:3000"
    const response = await fetch(`${site.replace(/\/$/, "")}${url}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.headers ?? {}),
      },
      cache: "no-store",
    })
    if (!response.ok) {
      const detail = await response.text().catch(() => "")
      throw new Error(`SaaS API ${path} failed (${response.status}): ${detail}`)
    }
    return response.json() as Promise<T>
  }

  const response = await fetch(url, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => "")
    throw new Error(`SaaS API ${path} failed (${response.status}): ${detail}`)
  }
  return response.json() as Promise<T>
}

type ApiResultsPayload = {
  asOfDate: string
  status: string
  completedAt: string | null
  scanRunId: string
  results: Array<{
    id: string
    symbol: string
    companyName: string
    sector: string
    preset: "standard"
    rank: number
    asOfDate: string
    close: number
    technicalScore: number
    grade: string
    rsRating: number
    pctFrom52WeekHigh: number
    adtvCrore: number
    dayChangePct: number
    sparkSeries?: number[]
    atrRatio: number
    volumeDryUpRatio: number
    fingerprint: QualificationFingerprint
    candles?: DailyCandle[]
  }>
}

export async function fetchStandardLatest(options?: {
  cache?: RequestCache
}): Promise<ScannerLatestMeta> {
  return saasFetch<ScannerLatestMeta>("/saas/scans/minervini/standard/latest", {
    cache: options?.cache ?? "no-store",
  })
}

export async function fetchStandardResults(options?: {
  cache?: RequestCache
  asOfDate?: string
  internalKey?: string
}): Promise<ScannerResultPreview[]> {
  const params = new URLSearchParams()
  if (options?.asOfDate) params.set("asOfDate", options.asOfDate)
  const qs = params.toString()
  const path = `/saas/scans/minervini/standard/results${qs ? `?${qs}` : ""}`
  const headers: HeadersInit = {}
  if (options?.internalKey) {
    headers["X-Swyingify-Internal-Key"] = options.internalKey
  }
  const payload = await saasFetch<ApiResultsPayload>(path, {
    cache: options?.cache ?? "no-store",
    headers,
  })
  return payload.results.map((row) => ({
    ...row,
    sparkSeed: undefined,
    sparkSeries: row.sparkSeries ?? [],
    candles: row.candles ?? [],
  }))
}

export async function fetchSymbolCandles(symbol: string): Promise<{
  symbol: string
  companyName: string
  asOfDate: string | null
  candles: DailyCandle[]
}> {
  return saasFetch(`/saas/candles/${encodeURIComponent(symbol)}`, {
    cache: "no-store",
  })
}
