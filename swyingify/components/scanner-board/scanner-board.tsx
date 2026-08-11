"use client"

import Link from "next/link"
import { useEffect, useMemo, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { usePathname, useRouter, useSearchParams } from "next/navigation"

import { ChangeCell, formatAdtv, formatInr, padRank, ScoreCell } from "@/components/scanner-board/board-formatters"
import { TrendSparkline } from "@/components/scanner-board/trend-sparkline"
import { Reveal } from "@/components/landing/reveal"
import { stockPath } from "@/lib/scanner/board-data"
import { scannerKeys, scannerLatestQuery, scannerResultsQuery } from "@/lib/scanner/queries"
import type { ScannerPreset, ScannerResultPreview } from "@/lib/scanner/types"

const sortOptions = ["score", "rs", "nearHigh", "price"] as const
type SortKey = (typeof sortOptions)[number]

const sortLabels: Record<SortKey, string> = {
  score: "Score",
  rs: "RS rating",
  nearHigh: "Near high",
  price: "Price",
}

type MoveFilter = "" | "up" | "dn"
type GradeFilter = "" | "A" | "B" | "C"

type BoardFilters = {
  q: string
  sort: SortKey
  sector: string
  grade: GradeFilter
  move: MoveFilter
  minRs: string
  maxHigh: string
  minAdtv: string
  minScore: string
}

const EMPTY_FILTERS: BoardFilters = {
  q: "",
  sort: "score",
  sector: "",
  grade: "",
  move: "",
  minRs: "",
  maxHigh: "",
  minAdtv: "",
  minScore: "",
}

function filtersFromParams(params: URLSearchParams): BoardFilters {
  const sortParam = params.get("sort")
  return {
    q: params.get("q") || "",
    sort: sortOptions.includes(sortParam as SortKey) ? (sortParam as SortKey) : "score",
    sector: params.get("sector") || "",
    grade: (params.get("grade") || "") as GradeFilter,
    move: (params.get("move") || "") as MoveFilter,
    minRs: params.get("minRs") || "",
    maxHigh: params.get("maxHigh") || "",
    minAdtv: params.get("minAdtv") || "",
    minScore: params.get("minScore") || "",
  }
}

function filtersToQuery(filters: BoardFilters): string {
  const params = new URLSearchParams()
  if (filters.q) params.set("q", filters.q)
  if (filters.sort !== "score") params.set("sort", filters.sort)
  if (filters.sector) params.set("sector", filters.sector)
  if (filters.grade) params.set("grade", filters.grade)
  if (filters.move) params.set("move", filters.move)
  if (filters.minRs) params.set("minRs", filters.minRs)
  if (filters.maxHigh) params.set("maxHigh", filters.maxHigh)
  if (filters.minAdtv) params.set("minAdtv", filters.minAdtv)
  if (filters.minScore) params.set("minScore", filters.minScore)
  return params.toString()
}

function hasActiveFilters(filters: BoardFilters): boolean {
  return Boolean(
    filters.q ||
      filters.sector ||
      filters.grade ||
      filters.move ||
      filters.minRs ||
      filters.maxHigh ||
      filters.minAdtv ||
      filters.minScore,
  )
}

function hasPanelFilters(filters: BoardFilters): boolean {
  return Boolean(
    filters.sector ||
      filters.grade ||
      filters.move ||
      filters.minRs ||
      filters.maxHigh ||
      filters.minAdtv ||
      filters.minScore,
  )
}

function formatBoardDate(isoDate: string) {
  const d = new Date(`${isoDate}T00:00:00.000Z`)
  if (Number.isNaN(d.getTime())) return isoDate
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
  const day = d.getUTCDate() < 10 ? `0${d.getUTCDate()}` : String(d.getUTCDate())
  return `${day} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()}`
}

type ScannerBoardProps = {
  initialPreset: Exclude<ScannerPreset, "custom">
  initialResults: ScannerResultPreview[]
  asOfDate: string
  isLiveData: boolean
  historical?: boolean
}

export function ScannerBoard({
  initialPreset,
  initialResults,
  asOfDate: initialAsOfDate,
  isLiveData,
  historical = false,
}: ScannerBoardProps) {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const queryClient = useQueryClient()
  const lastCompletedAt = useRef<string | null>(null)
  const lastWrittenQuery = useRef(filtersToQuery(filtersFromParams(searchParams)))
  const previousQueryText = useRef(searchParams.get("q") || "")
  const [filters, setFilters] = useState<BoardFilters>(() => filtersFromParams(searchParams))
  const [filtersOpen, setFiltersOpen] = useState(() => hasPanelFilters(filtersFromParams(searchParams)))
  const searchParamsKey = searchParams.toString()
  const presetLabel = initialPreset === "strict" ? "Strict" : "Standard"
  const presetDescription = initialPreset === "strict"
    ? "Tonight’s tighter shortlist after the cash-market close — all five Stage 2 checks plus stronger relative-strength, contraction, liquidity, and volume dry-up gates."
    : "Tonight’s Standard shortlist (top 25) after the cash-market close — an independent rule-based approximation of Stage 2 / volatility contraction conditions with trend, price, volume, and relative strength in one view."

  const { data, isLoading, isFetching } = useQuery({
    ...scannerResultsQuery(initialPreset),
    initialData: initialResults,
    enabled: !historical,
  })

  const { data: latest } = useQuery({
    ...scannerLatestQuery(initialPreset),
    enabled: isLiveData && !historical,
  })

  useEffect(() => {
    if (!latest?.completedAt) return
    if (lastCompletedAt.current === null) {
      lastCompletedAt.current = latest.completedAt
      return
    }
    if (lastCompletedAt.current !== latest.completedAt) {
      lastCompletedAt.current = latest.completedAt
      void queryClient.invalidateQueries({ queryKey: scannerKeys.results(initialPreset) })
    }
  }, [initialPreset, latest?.completedAt, queryClient])

  // Keep local filters in sync with back/forward and shared links.
  useEffect(() => {
    const fromUrl = filtersFromParams(new URLSearchParams(searchParamsKey))
    const fromUrlQuery = filtersToQuery(fromUrl)
    if (fromUrlQuery === lastWrittenQuery.current) return
    lastWrittenQuery.current = fromUrlQuery
    previousQueryText.current = fromUrl.q
    setFilters(fromUrl)
    if (hasPanelFilters(fromUrl)) setFiltersOpen(true)
  }, [searchParamsKey])

  // Mirror local filters into the URL without blocking the control UI.
  useEffect(() => {
    const nextQuery = filtersToQuery(filters)
    if (nextQuery === lastWrittenQuery.current) return
    const qChanged = previousQueryText.current !== filters.q
    previousQueryText.current = filters.q
    const timer = window.setTimeout(() => {
      lastWrittenQuery.current = nextQuery
      router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, { scroll: false })
    }, qChanged ? 200 : 0)
    return () => window.clearTimeout(timer)
  }, [filters, pathname, router])

  const asOfDate = latest?.asOfDate || data?.[0]?.asOfDate || initialAsOfDate

  const sectors = useMemo(() => {
    const seen = new Set<string>()
    ;(data ?? []).forEach((row) => seen.add(row.sector))
    return [...seen].sort()
  }, [data])

  const minRs = Number(filters.minRs || 0)
  const maxHigh = Number(filters.maxHigh || 0)
  const minAdtv = Number(filters.minAdtv || 0)
  const minScore = Number(filters.minScore || 0)

  const results = useMemo(() => {
    const filtered = (data ?? []).filter((row) => {
      const haystack = `${row.symbol} ${row.companyName} ${row.sector}`.toLowerCase()
      if (filters.q && !haystack.includes(filters.q.toLowerCase())) return false
      if (filters.sector && row.sector !== filters.sector) return false
      if (filters.grade && row.grade !== filters.grade) return false
      if (filters.move === "up" && row.dayChangePct < 0) return false
      if (filters.move === "dn" && row.dayChangePct >= 0) return false
      if (minRs && row.rsRating < minRs) return false
      if (maxHigh && row.pctFrom52WeekHigh > maxHigh) return false
      if (minAdtv && row.adtvCrore < minAdtv) return false
      if (minScore && row.technicalScore < minScore) return false
      return true
    })

    return [...filtered].sort((a, b) => {
      if (filters.sort === "rs") return b.rsRating - a.rsRating
      if (filters.sort === "nearHigh") return a.pctFrom52WeekHigh - b.pctFrom52WeekHigh
      if (filters.sort === "price") return b.close - a.close
      return b.technicalScore - a.technicalScore
    })
  }, [data, filters, minRs, maxHigh, minAdtv, minScore])

  const activeFilters = hasActiveFilters(filters)
  const activePanelCount = [
    filters.sector,
    filters.grade,
    filters.move,
    filters.minRs,
    filters.maxHigh,
    filters.minAdtv,
    filters.minScore,
  ].filter(Boolean).length

  function patchFilters(patch: Partial<BoardFilters>) {
    setFilters((current) => ({ ...current, ...patch }))
  }

  function clearFilters() {
    setFilters((current) => ({ ...EMPTY_FILTERS, sort: current.sort }))
  }

  const statusLabel = historical
    ? "Archived EOD board"
    : !isLiveData
    ? "Preview board · illustrative until live results"
    : latest?.status === "running" || latest?.status === "queued"
      ? "Scan running · updating after the close"
      : isFetching
        ? "Refreshing board…"
        : "Live board · updates after every close"

  return (
    <>
      <header className="pt-[clamp(48px,7vw,80px)]">
        <div className="mx-auto max-w-[1200px] px-6 max-sm:px-3">
          <Reveal>
            <p className="landing-kicker">Minervini VCP · {presetLabel} · Nifty 500 · End of day</p>
          </Reveal>
          <Reveal>
            <h1 className="mt-[26px] font-[family-name:var(--font-landing-mono)] text-[clamp(34px,4.4vw,56px)] font-light leading-[1.1] text-[var(--landing-fg)]">
              Minervini VCP {presetLabel.toLowerCase()} scanner
            </h1>
          </Reveal>
          <Reveal>
            <p className="landing-lead mt-6">
              {presetDescription}
            </p>
          </Reveal>
          <Reveal>
            <div className="mt-8 flex flex-wrap items-center justify-between gap-5 border border-[var(--landing-border)] bg-[var(--landing-surface)] px-[18px] py-3.5">
              <div className="flex flex-wrap items-center gap-3.5">
                <time
                  dateTime={asOfDate}
                  className="font-[family-name:var(--font-landing-mono)] text-sm tracking-wide text-[var(--landing-fg)]"
                >
                  {formatBoardDate(asOfDate)}
                </time>
                <span className="border border-[var(--landing-border)] px-2.5 py-1 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">
                  EOD snapshot
                </span>
                <span className="border border-[var(--landing-border)] px-2.5 py-1 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">
                  {presetLabel} · top 25
                </span>
              </div>
              <span className="font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-wider text-[var(--landing-muted)] max-sm:w-full">
                {statusLabel}
              </span>
            </div>
          </Reveal>
        </div>
      </header>

      <section className="pb-16 pt-12">
        <div className="mx-auto max-w-[1200px] px-6 max-sm:px-3">
          <div className="board-toolbar">
            <div className="board-toolbar-row">
              <p className="font-[family-name:var(--font-landing-mono)] text-sm uppercase tracking-widest text-[var(--landing-muted)]">
                {presetLabel} shortlist
              </p>
              <span className="whitespace-nowrap font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]" aria-live="polite">
                {padRank(results.length)} {results.length === 1 ? "setup" : "setups"}
              </span>
            </div>

            <div className="board-toolbar-controls">
              <label className="board-search">
                <SearchIcon />
                <input
                  type="search"
                  value={filters.q}
                  onChange={(e) => patchFilters({ q: e.target.value })}
                  placeholder="Search a stock or sector"
                  autoComplete="off"
                  aria-label="Search stocks"
                />
              </label>

              <label className="board-control">
                <span className="board-control-label">Sort</span>
                <select
                  value={filters.sort}
                  onChange={(e) => patchFilters({ sort: e.target.value as SortKey })}
                  aria-label="Sort results"
                >
                  {sortOptions.map((option) => (
                    <option key={option} value={option}>
                      {sortLabels[option]}
                    </option>
                  ))}
                </select>
              </label>

              <button
                type="button"
                className={`board-filter-toggle${filtersOpen ? " is-open" : ""}${activePanelCount ? " has-active" : ""}`}
                aria-expanded={filtersOpen}
                aria-controls="board-filter-panel"
                onClick={() => setFiltersOpen((open) => !open)}
              >
                Filters{activePanelCount ? ` · ${activePanelCount}` : ""}
              </button>

              {activeFilters ? (
                <button type="button" className="board-clear" onClick={clearFilters}>
                  Clear
                </button>
              ) : null}
            </div>

            {filtersOpen ? (
              <div id="board-filter-panel" className="board-filter-panel">
                <p className="board-filter-kicker">Filter this published shortlist</p>
                <div className="board-filter-grid">
                  <label className="board-control">
                    <span className="board-control-label">Sector</span>
                    <select
                      value={filters.sector}
                      onChange={(e) => patchFilters({ sector: e.target.value })}
                      aria-label="Filter by sector"
                    >
                      <option value="">All sectors</option>
                      {sectors.map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="board-control">
                    <span className="board-control-label">RS rating</span>
                    <select
                      value={filters.minRs}
                      onChange={(e) => patchFilters({ minRs: e.target.value })}
                      aria-label="Minimum RS rating"
                    >
                      <option value="">Any RS</option>
                      <option value="70">70+</option>
                      <option value="80">80+</option>
                      <option value="90">90+</option>
                    </select>
                  </label>

                  <label className="board-control">
                    <span className="board-control-label">Near 52w high</span>
                    <select
                      value={filters.maxHigh}
                      onChange={(e) => patchFilters({ maxHigh: e.target.value })}
                      aria-label="Maximum distance from 52-week high"
                    >
                      <option value="">Any proximity</option>
                      <option value="5">Within 5%</option>
                      <option value="10">Within 10%</option>
                      <option value="15">Within 15%</option>
                    </select>
                  </label>

                  <label className="board-control">
                    <span className="board-control-label">ADTV</span>
                    <select
                      value={filters.minAdtv}
                      onChange={(e) => patchFilters({ minAdtv: e.target.value })}
                      aria-label="Minimum average daily traded value"
                    >
                      <option value="">Any ADTV</option>
                      <option value="25">₹25cr+</option>
                      <option value="50">₹50cr+</option>
                      <option value="100">₹100cr+</option>
                    </select>
                  </label>

                  <label className="board-control">
                    <span className="board-control-label">Score</span>
                    <select
                      value={filters.minScore}
                      onChange={(e) => patchFilters({ minScore: e.target.value })}
                      aria-label="Minimum technical score"
                    >
                      <option value="">Any score</option>
                      <option value="70">70+</option>
                      <option value="80">80+</option>
                      <option value="90">90+</option>
                    </select>
                  </label>

                  <label className="board-control">
                    <span className="board-control-label">Grade</span>
                    <select
                      value={filters.grade}
                      onChange={(e) => patchFilters({ grade: e.target.value as GradeFilter })}
                      aria-label="Filter by grade"
                    >
                      <option value="">All grades</option>
                      <option value="A">A</option>
                      <option value="B">B</option>
                      <option value="C">C</option>
                    </select>
                  </label>

                  <label className="board-control">
                    <span className="board-control-label">Day move</span>
                    <select
                      value={filters.move}
                      onChange={(e) => patchFilters({ move: e.target.value as MoveFilter })}
                      aria-label="Filter by day move"
                    >
                      <option value="">All moves</option>
                      <option value="up">Up day</option>
                      <option value="dn">Down day</option>
                    </select>
                  </label>
                </div>
              </div>
            ) : null}
          </div>

          <div className="mt-5">
            {isLoading && !data?.length ? (
              <BoardSkeleton />
            ) : results.length > 0 ? (
              <>
                <BoardTable results={results} preset={initialPreset} isLiveData={isLiveData} />
                <BoardCards results={results} preset={initialPreset} isLiveData={isLiveData} />
              </>
            ) : (
              <EmptyBoard
                hasFilters={activeFilters}
                isLiveData={isLiveData}
                status={latest?.status}
              />
            )}
          </div>

          <p className="mt-4 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-wider text-[var(--landing-muted)]">
            {isLiveData
              ? "Educational screening only · not SEBI-registered · not a buy signal"
              : "Illustrative values on real Nifty 500 symbols · live dated results replace these numbers when the backend is connected"}
          </p>
        </div>
      </section>
    </>
  )
}

function BoardTable({
  results,
  preset,
  isLiveData,
}: {
  results: ScannerResultPreview[]
  preset: Exclude<ScannerPreset, "custom">
  isLiveData: boolean
}) {
  return (
    <div className="board-wrap">
      <div className="board-row head">
        <span className="font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">#</span>
        <span className="font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">Stock</span>
        <span className="font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">Sector</span>
        <span className="font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">Daily trend</span>
        <span className="text-right font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">Price</span>
        <span className="text-right font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">Change</span>
        <span className="text-right font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">ADTV</span>
        <span className="text-right font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">RS</span>
        <span className="text-right font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">Score</span>
      </div>
      {results.map((row, index) => (
        <Link
          key={row.id}
          href={stockPath(row.symbol, preset)}
          className="board-row data text-[var(--landing-fg-2)] hover:no-underline"
        >
          <span className="font-[family-name:var(--font-landing-mono)] text-xs tracking-wide text-[var(--landing-muted)]">
            {padRank(index + 1)}
          </span>
          <div className="min-w-0">
            <p className="truncate font-[family-name:var(--font-landing-mono)] text-[15px] tracking-wide text-[var(--landing-fg)]">
              {row.symbol}
            </p>
            <p className="truncate text-xs text-[var(--landing-muted)]">{row.companyName}</p>
          </div>
          <span className="truncate text-sm">{row.sector}</span>
          <span className="flex items-center">
            <TrendSparkline
              close={row.close}
              dayChangePct={row.dayChangePct}
              sparkSeed={row.sparkSeed}
              sparkSeries={row.sparkSeries}
              candles={row.candles}
              isLiveData={isLiveData}
            />
          </span>
          <span className="text-right font-[family-name:var(--font-landing-mono)] text-sm text-[var(--landing-fg)]">
            {formatInr(row.close)}
          </span>
          <span className="text-right text-sm">
            <ChangeCell value={row.dayChangePct} />
          </span>
          <span className="text-right font-[family-name:var(--font-landing-mono)] text-sm text-[var(--landing-fg)]">
            {formatAdtv(row.adtvCrore)}
          </span>
          <span className="text-right font-[family-name:var(--font-landing-mono)] text-sm text-[var(--landing-fg)]">
            {row.rsRating}
          </span>
          <span className="text-right">
            <ScoreCell result={row} />
          </span>
        </Link>
      ))}
    </div>
  )
}

function BoardCards({
  results,
  preset,
  isLiveData,
}: {
  results: ScannerResultPreview[]
  preset: Exclude<ScannerPreset, "custom">
  isLiveData: boolean
}) {
  return (
    <div className="board-cards mt-5">
      {results.map((row) => (
        <Link
          key={`card-${row.id}`}
          href={stockPath(row.symbol, preset)}
          className="mb-3 block border border-[var(--landing-border)] p-[18px] transition-colors last:mb-0 hover:bg-[var(--landing-surface-warm)] hover:no-underline"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <p className="font-[family-name:var(--font-landing-mono)] text-[15px] tracking-wide text-[var(--landing-fg)]">
                {row.symbol}
              </p>
              <p className="truncate text-xs text-[var(--landing-muted)]">{row.companyName}</p>
            </div>
            <ScoreCell result={row} />
          </div>
          <div className="mt-3.5 flex items-center justify-between gap-4">
            <span className="max-w-[130px] flex-1">
              <TrendSparkline
                close={row.close}
                dayChangePct={row.dayChangePct}
                sparkSeed={row.sparkSeed}
                sparkSeries={row.sparkSeries}
                candles={row.candles}
                isLiveData={isLiveData}
              />
            </span>
            <div className="text-right">
              <p className="font-[family-name:var(--font-landing-mono)] text-lg font-light text-[var(--landing-fg)]">
                {formatInr(row.close)}
              </p>
              <div className="mt-1">
                <ChangeCell value={row.dayChangePct} />
              </div>
            </div>
          </div>
          <div className="board-card-grid mt-3.5 grid grid-cols-3 gap-3 border-t border-[var(--landing-border-soft)] pt-3">
            <div className="text-xs text-[var(--landing-muted)]">
              Sector
              <strong className="mt-0.5 block font-[family-name:var(--font-landing-mono)] text-sm font-normal text-[var(--landing-fg)]">
                {row.sector}
              </strong>
            </div>
            <div className="text-xs text-[var(--landing-muted)]">
              ADTV
              <strong className="mt-0.5 block font-[family-name:var(--font-landing-mono)] text-sm font-normal text-[var(--landing-fg)]">
                {formatAdtv(row.adtvCrore)}
              </strong>
            </div>
            <div className="text-xs text-[var(--landing-muted)]">
              RS rating
              <strong className="mt-0.5 block font-[family-name:var(--font-landing-mono)] text-sm font-normal text-[var(--landing-fg)]">
                {row.rsRating} / 100
              </strong>
            </div>
          </div>
        </Link>
      ))}
    </div>
  )
}

function EmptyBoard({
  hasFilters,
  isLiveData,
  status,
}: {
  hasFilters: boolean
  isLiveData: boolean
  status?: string
}) {
  const pending =
    isLiveData &&
    !hasFilters &&
    (status === "missing" || status === "queued" || status === "running" || status === "error")
  const message = (
    <div className="border border-[var(--landing-border)] px-6 py-14 text-center">
      <p className="landing-kicker mb-3.5">{pending ? "Waiting on EOD" : "No match"}</p>
      <p className="mx-auto max-w-[40ch] text-sm leading-relaxed text-[var(--landing-fg-2)]">
        {pending
          ? status === "error"
            ? "Could not reach the scanner API. Check that FastAPI is running and API_URL is set, then refresh."
            : "Tonight’s Standard shortlist is not published yet. After the cash-market close (or Run EOD SCAN in the trading app), this board fills automatically."
          : "No setup in this scan matches the current search or filters. Try another symbol, sector, grade, or clear the filters."}
      </p>
    </div>
  )
  return (
    <>
      <div className="board-wrap hidden min-[901px]:block">{message}</div>
      <div className="board-cards mt-5 min-[901px]:hidden">{message}</div>
    </>
  )
}

function BoardSkeleton() {
  return <div className="mt-5 h-[420px] animate-pulse border border-[var(--landing-border)] bg-[var(--landing-surface)]" />
}

function SearchIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden className="shrink-0">
      <circle cx="6" cy="6" r="4.6" stroke="rgba(255,255,255,0.5)" strokeWidth="1.2" />
      <path d="M9.6 9.6l3 3" stroke="rgba(255,255,255,0.5)" strokeWidth="1.2" />
    </svg>
  )
}
