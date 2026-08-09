"use client"

import Link from "next/link"
import { useEffect, useMemo, useRef, useTransition } from "react"
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

function formatBoardDate(isoDate: string) {
  const d = new Date(`${isoDate}T00:00:00.000Z`)
  if (Number.isNaN(d.getTime())) return isoDate
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
  const day = d.getUTCDate() < 10 ? `0${d.getUTCDate()}` : String(d.getUTCDate())
  return `${day} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()}`
}

type ScannerBoardProps = {
  initialPreset: ScannerPreset
  initialResults: ScannerResultPreview[]
  asOfDate: string
  isLiveData: boolean
}

export function ScannerBoard({
  initialPreset,
  initialResults,
  asOfDate: initialAsOfDate,
  isLiveData,
}: ScannerBoardProps) {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const queryClient = useQueryClient()
  const [isPending, startTransition] = useTransition()
  const lastCompletedAt = useRef<string | null>(null)

  const search = searchParams.get("q") || ""
  const sortParam = searchParams.get("sort")
  const sort: SortKey = sortOptions.includes(sortParam as SortKey) ? (sortParam as SortKey) : "score"
  const sector = searchParams.get("sector") || ""
  const grade = (searchParams.get("grade") || "") as GradeFilter
  const move = (searchParams.get("move") || "") as MoveFilter

  const { data, isLoading, isFetching } = useQuery({
    ...scannerResultsQuery(initialPreset),
    initialData: initialResults,
  })

  const { data: latest } = useQuery({
    ...scannerLatestQuery(),
    enabled: isLiveData,
  })

  useEffect(() => {
    if (!latest?.completedAt) return
    if (lastCompletedAt.current === null) {
      lastCompletedAt.current = latest.completedAt
      return
    }
    if (lastCompletedAt.current !== latest.completedAt) {
      lastCompletedAt.current = latest.completedAt
      void queryClient.invalidateQueries({ queryKey: scannerKeys.results("standard") })
    }
  }, [latest?.completedAt, queryClient])

  const asOfDate = latest?.asOfDate || data?.[0]?.asOfDate || initialAsOfDate

  const sectors = useMemo(() => {
    const seen = new Set<string>()
    ;(data ?? []).forEach((row) => seen.add(row.sector))
    return [...seen].sort()
  }, [data])

  const results = useMemo(() => {
    const filtered = (data ?? []).filter((row) => {
      const haystack = `${row.symbol} ${row.companyName} ${row.sector}`.toLowerCase()
      if (search && !haystack.includes(search.toLowerCase())) return false
      if (sector && row.sector !== sector) return false
      if (grade && row.grade !== grade) return false
      if (move === "up" && row.dayChangePct < 0) return false
      if (move === "dn" && row.dayChangePct >= 0) return false
      return true
    })

    return [...filtered].sort((a, b) => {
      if (sort === "rs") return b.rsRating - a.rsRating
      if (sort === "nearHigh") return a.pctFrom52WeekHigh - b.pctFrom52WeekHigh
      if (sort === "price") return b.close - a.close
      return b.technicalScore - a.technicalScore
    })
  }, [data, search, sector, grade, move, sort])

  const hasFilters = !!(search || sector || grade || move)

  function updateParams(next: Record<string, string | null>) {
    const params = new URLSearchParams(searchParams.toString())
    Object.entries(next).forEach(([key, value]) => (value ? params.set(key, value) : params.delete(key)))
    params.delete("preset")
    startTransition(() => router.replace(`${pathname}?${params.toString()}`, { scroll: false }))
  }

  function clearFilters() {
    updateParams({ q: null, sector: null, grade: null, move: null })
  }

  const statusLabel = !isLiveData
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
            <p className="landing-kicker">Minervini VCP · Standard · Nifty 500 · End of day</p>
          </Reveal>
          <Reveal>
            <h1 className="mt-[26px] font-[family-name:var(--font-landing-mono)] text-[clamp(34px,4.4vw,56px)] font-light leading-[1.1] text-[var(--landing-fg)]">
              Minervini VCP scanner for Nifty 500 stocks
            </h1>
          </Reveal>
          <Reveal>
            <p className="landing-lead mt-6">
              Tonight&apos;s Standard shortlist (top 25) after the cash-market close — an independent rule-based
              approximation of Stage 2 / volatility contraction conditions with trend, price, volume, and relative
              strength in one view.
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
                  Standard · top 25
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
          <Reveal>
            <div className="flex flex-wrap items-center justify-between gap-4">
              <p className="font-[family-name:var(--font-landing-mono)] text-sm uppercase tracking-widest text-[var(--landing-muted)]">
                Standard shortlist
              </p>
              <div className="flex flex-wrap items-center gap-3 max-sm:w-full max-sm:justify-between">
                <label className="board-search">
                  <SearchIcon />
                  <input
                    type="search"
                    value={search}
                    onChange={(e) => updateParams({ q: e.target.value || null })}
                    placeholder="Search a stock or sector"
                    autoComplete="off"
                    aria-label="Search stocks"
                  />
                </label>
                <label className="board-select">
                  <select
                    value={sort}
                    onChange={(e) => updateParams({ sort: e.target.value === "score" ? null : e.target.value })}
                    aria-label="Sort results"
                  >
                    {sortOptions.map((option) => (
                      <option key={option} value={option}>
                        Sort: {sortLabels[option]}
                      </option>
                    ))}
                  </select>
                </label>
                <span className="whitespace-nowrap font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]" aria-live="polite">
                  {padRank(results.length)} {results.length === 1 ? "setup" : "setups"}
                  {isPending ? " ·" : ""}
                </span>
              </div>
            </div>
          </Reveal>

          <Reveal>
            <div className="mt-3.5 flex flex-wrap items-center gap-3">
              <label className="board-select">
                <select
                  value={sector}
                  onChange={(e) => updateParams({ sector: e.target.value || null })}
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
              <label className="board-select">
                <select
                  value={grade}
                  onChange={(e) => updateParams({ grade: e.target.value || null })}
                  aria-label="Filter by grade"
                >
                  <option value="">All grades</option>
                  <option value="A">Grade A</option>
                  <option value="B">Grade B</option>
                  <option value="C">Grade C</option>
                </select>
              </label>
              <label className="board-select">
                <select
                  value={move}
                  onChange={(e) => updateParams({ move: e.target.value || null })}
                  aria-label="Filter by day move"
                >
                  <option value="">All moves</option>
                  <option value="up">Up day</option>
                  <option value="dn">Down day</option>
                </select>
              </label>
              {hasFilters ? (
                <button
                  type="button"
                  className="min-h-11 bg-transparent px-1 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)] transition-colors hover:text-[var(--landing-fg)]"
                  onClick={clearFilters}
                >
                  Clear filters
                </button>
              ) : null}
            </div>
          </Reveal>

          <Reveal>
            <div className="mt-5">
              {isLoading && !data?.length ? (
                <BoardSkeleton />
              ) : results.length > 0 ? (
                <>
                  <BoardTable results={results} />
                  <BoardCards results={results} />
                </>
              ) : (
                <EmptyBoard
                  hasFilters={hasFilters}
                  isLiveData={isLiveData}
                  status={latest?.status}
                />
              )}
            </div>
          </Reveal>

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

function BoardTable({ results }: { results: ScannerResultPreview[] }) {
  return (
    <div className="board-wrap">
      <div className="board-row head">
        <span className="font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">#</span>
        <span className="font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">Stock</span>
        <span className="font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">Sector</span>
        <span className="font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">Trend</span>
        <span className="text-right font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">Price</span>
        <span className="text-right font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">Change</span>
        <span className="text-right font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">ADTV</span>
        <span className="text-right font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">RS</span>
        <span className="text-right font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">Score</span>
      </div>
      {results.map((row, index) => (
        <Link
          key={row.id}
          href={stockPath(row.symbol)}
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

function BoardCards({ results }: { results: ScannerResultPreview[] }) {
  return (
    <div className="board-cards mt-5">
      {results.map((row) => (
        <Link
          key={`card-${row.id}`}
          href={stockPath(row.symbol)}
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
          : "No setup in the Standard scan matches the current search or filters. Try another symbol, sector, grade, or clear the filters."}
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
