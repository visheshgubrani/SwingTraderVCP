import type {
  DailyCandle,
  QualificationFingerprint,
  ScannerLatestMeta,
  ScannerResultPreview,
  ScannerPreset,
  ScannerVariantInput,
  ScannerVariantRun,
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
    preset: ScannerPreset
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
  return fetchScannerLatest("standard", options)
}

export async function fetchScannerLatest(
  preset: Exclude<ScannerPreset, "custom">,
  options?: { cache?: RequestCache },
): Promise<ScannerLatestMeta> {
  return saasFetch<ScannerLatestMeta>(`/saas/scans/minervini/${preset}/latest`, {
    cache: options?.cache ?? "no-store",
  })
}

export async function fetchStandardResults(options?: {
  cache?: RequestCache
  asOfDate?: string
  accessToken?: string
}): Promise<ScannerResultPreview[]> {
  return fetchScannerResults("standard", options)
}

export async function fetchScannerResults(
  preset: Exclude<ScannerPreset, "custom">,
  options?: {
    cache?: RequestCache
    asOfDate?: string
    accessToken?: string
  },
): Promise<ScannerResultPreview[]> {
  const params = new URLSearchParams()
  if (options?.asOfDate) params.set("asOfDate", options.asOfDate)
  const qs = params.toString()
  const path = `/saas/scans/minervini/${preset}/results${qs ? `?${qs}` : ""}`
  const headers: HeadersInit = {}
  if (options?.accessToken) {
    headers["X-Swyingify-Access"] = options.accessToken
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

function normalizeVariantRun(payload: ScannerVariantRun): ScannerVariantRun {
  return {
    ...payload,
    results: (payload.results ?? []).map((row) => ({
      ...row,
      sparkSeries: row.sparkSeries ?? [],
      candles: row.candles ?? [],
    })),
  }
}

export async function createScannerVariant(
  input: ScannerVariantInput,
): Promise<ScannerVariantRun> {
  const payload = await saasFetch<ScannerVariantRun>(
    "/saas/scans/minervini/variants",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  )
  return normalizeVariantRun(payload)
}

export async function fetchScannerVariant(runId: string): Promise<ScannerVariantRun> {
  const payload = await saasFetch<ScannerVariantRun>(
    `/saas/scans/minervini/variants/${encodeURIComponent(runId)}`,
    { cache: "no-store" },
  )
  return normalizeVariantRun(payload)
}

export const STOCK_CHART_CANDLE_LIMIT = 252

export async function fetchSymbolCandles(
  symbol: string,
  options?: { limit?: number },
): Promise<{
  symbol: string
  companyName: string
  asOfDate: string | null
  candles: DailyCandle[]
}> {
  const limit = options?.limit ?? STOCK_CHART_CANDLE_LIMIT
  const qs = new URLSearchParams({ limit: String(limit) })
  return saasFetch(`/saas/candles/${encodeURIComponent(symbol)}?${qs}`, {
    cache: "no-store",
  })
}
