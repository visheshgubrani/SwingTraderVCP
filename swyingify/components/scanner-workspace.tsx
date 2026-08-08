"use client"

import Link from "next/link"
import { useMemo, useTransition } from "react"
import { SearchIcon, SlidersHorizontalIcon, ArrowUpDownIcon } from "lucide-react"
import { useQuery } from "@tanstack/react-query"
import { usePathname, useRouter, useSearchParams } from "next/navigation"

import { PreviewNotice } from "@/components/preview-notice"
import { SetupFingerprint } from "@/components/setup-fingerprint"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { scannerResultsQuery } from "@/lib/scanner/queries"
import type { ScannerPreset, ScannerResultPreview } from "@/lib/scanner/types"

const presetCopy: Record<ScannerPreset, { label: string; description: string }> = {
  standard: { label: "Standard", description: "A focused shortlist with stronger contraction and trend alignment." },
  wide: { label: "Wide", description: "A broader Stage 2 pool for early research and learning." },
}

const sortOptions = ["score", "rs", "nearHigh", "price"] as const
type SortKey = (typeof sortOptions)[number]
const sortLabels: Record<SortKey, string> = {
  score: "Score",
  rs: "RS rating",
  nearHigh: "Near high",
  price: "Price",
}

function formatPrice(value: number) {
  return `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`
}

function scoreTone(score: number) {
  if (score >= 90) return "bg-positive/10 text-positive border-positive/20"
  if (score >= 80) return "bg-primary/10 text-primary border-primary/20"
  return "bg-muted text-muted-foreground border-border"
}

export function ScannerWorkspace() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const [isPending, startTransition] = useTransition()
  const preset = searchParams.get("preset") === "wide" ? "wide" : "standard"
  const search = searchParams.get("q") || ""
  const sortParam = searchParams.get("sort")
  const sort: SortKey = sortOptions.includes(sortParam as SortKey) ? (sortParam as SortKey) : "score"
  const { data, isLoading, isError } = useQuery(scannerResultsQuery(preset))

  const results = useMemo(() => {
    const filtered = (data ?? []).filter((result) => `${result.symbol} ${result.companyName} ${result.sector}`.toLowerCase().includes(search.toLowerCase()))
    return [...filtered].sort((a, b) => {
      if (sort === "rs") return b.rsRating - a.rsRating
      if (sort === "nearHigh") return a.pctFrom52WeekHigh - b.pctFrom52WeekHigh
      if (sort === "price") return b.close - a.close
      return a.rank - b.rank
    })
  }, [data, search, sort])

  function updateParams(next: Record<string, string | null>) {
    const params = new URLSearchParams(searchParams.toString())
    Object.entries(next).forEach(([key, value]) => value ? params.set(key, value) : params.delete(key))
    startTransition(() => router.replace(`${pathname}?${params.toString()}`, { scroll: false }))
  }

  return (
    <div className="flex flex-col gap-7">
      <PreviewNotice />
      <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">India · Nifty 500</Badge>
            <Badge variant="secondary">Mark Minervini-inspired</Badge>
          </div>
          <h1 className="mt-4 font-display text-4xl font-semibold tracking-tight sm:text-5xl">The daily setup board.</h1>
          <p className="mt-3 max-w-2xl text-base leading-7 text-muted-foreground">A clear shortlist of Indian equities that line up with a rule-based Stage 2 and VCP-style scan. Learn what matched before you decide what deserves more study.</p>
        </div>
        <div className="rounded-2xl border border-border/70 bg-card px-4 py-3 text-left shadow-sm lg:min-w-48">
          <p className="font-mono text-[0.68rem] uppercase tracking-[0.16em] text-muted-foreground">EOD snapshot</p>
          <p className="mt-1 font-mono text-lg font-semibold text-foreground">07 Aug 2026</p>
          <p className="mt-1 text-xs text-muted-foreground">Preview date · not live data</p>
        </div>
      </div>

      <Card className="overflow-visible">
        <CardHeader className="gap-4 border-b border-border/70 pb-5">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <CardTitle className="text-xl">Minervini scanner</CardTitle>
              <p className="mt-1 max-w-xl text-sm text-muted-foreground">{presetCopy[preset].description}</p>
            </div>
            <Tabs value={preset} onValueChange={(value) => updateParams({ preset: value })}>
              <TabsList aria-label="Scanner preset">
                <TabsTrigger value="standard">Standard</TabsTrigger>
                <TabsTrigger value="wide">Wide</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="relative w-full md:max-w-sm">
              <SearchIcon className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted-foreground" />
              <Input value={search} onChange={(event) => updateParams({ q: event.target.value || null })} placeholder="Search a stock or sector" className="h-10 pl-10" aria-label="Search stocks" />
            </div>
            <div className="flex items-center gap-2">
              <span className="hidden text-sm text-muted-foreground sm:inline">{results.length} preview matches</span>
              <DropdownMenu>
                <DropdownMenuTrigger render={<Button variant="outline" size="sm" aria-label={`Sort results, currently ${sortLabels[sort]}`} />}>
                  <ArrowUpDownIcon data-icon="inline-start" />
                  Sort: {sortLabels[sort]}
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {sortOptions.map((option) => (
                    <DropdownMenuItem key={option} onClick={() => updateParams({ sort: option })}>
                      {sortLabels[option]}{sort === option ? " · selected" : ""}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
              <Button variant="ghost" size="icon-sm" aria-label="More filters coming later" title="More filters coming later" disabled>
                <SlidersHorizontalIcon />
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? <ScannerSkeleton /> : isError ? <ScannerError /> : results.length === 0 ? <ScannerEmpty search={search} onClear={() => updateParams({ q: null })} /> : (
            <>
              <div className="hidden md:block">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="pl-6">Stock</TableHead>
                      <TableHead>Score</TableHead>
                      <TableHead>Setup fingerprint</TableHead>
                      <TableHead>RS rating</TableHead>
                      <TableHead>From 52W high</TableHead>
                      <TableHead>Volume behavior</TableHead>
                      <TableHead className="pr-6 text-right">Price</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {results.map((result) => <DesktopResultRow key={result.id} result={result} />)}
                  </TableBody>
                </Table>
              </div>
              <div className={`flex flex-col gap-3 p-4 md:hidden ${isPending ? "opacity-60" : ""}`}>
                {results.map((result) => <MobileResultCard key={result.id} result={result} />)}
              </div>
            </>
          )}
        </CardContent>
      </Card>
      <p className="text-center text-xs leading-5 text-muted-foreground">Swyingify is an educational scanner, not SEBI-registered and not investment advice. Minervini-style rules are independent approximations and are not endorsed by the trader.</p>
    </div>
  )
}

function DesktopResultRow({ result }: { result: ScannerResultPreview }) {
  return (
    <TableRow>
      <TableCell className="pl-6">
        <Link href={`/stocks/${result.symbol}`} className="group flex min-w-44 items-center gap-3 rounded-lg py-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <span className="grid size-9 place-items-center rounded-xl bg-primary/10 font-mono text-[0.62rem] font-semibold text-primary">{result.symbol.slice(0, 2)}</span>
          <span className="flex flex-col gap-0.5">
            <span className="font-mono text-sm font-semibold group-hover:text-primary">{result.symbol}</span>
            <span className="max-w-36 truncate text-xs text-muted-foreground">{result.companyName}</span>
          </span>
        </Link>
      </TableCell>
      <TableCell><Badge variant="outline" className={scoreTone(result.technicalScore)}>{result.technicalScore} · {result.grade}</Badge></TableCell>
      <TableCell><SetupFingerprint fingerprint={result.fingerprint} /></TableCell>
      <TableCell><span className="font-mono text-sm">{result.rsRating}</span><span className="ml-1 text-xs text-muted-foreground">/100</span></TableCell>
      <TableCell><span className="font-mono text-sm">{result.pctFrom52WeekHigh.toFixed(1)}%</span><span className="ml-1 text-xs text-muted-foreground">away</span></TableCell>
      <TableCell><span className="font-mono text-sm">{result.volumeDryUpRatio.toFixed(2)}×</span><span className="ml-1 text-xs text-muted-foreground">dry-up</span></TableCell>
      <TableCell className="pr-6 text-right"><span className="font-mono text-sm font-semibold">{formatPrice(result.close)}</span></TableCell>
    </TableRow>
  )
}

function MobileResultCard({ result }: { result: ScannerResultPreview }) {
  return (
    <Link href={`/stocks/${result.symbol}`} className="rounded-2xl border border-border/70 bg-background p-4 transition-colors hover:border-primary/30 hover:bg-muted/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="grid size-9 place-items-center rounded-xl bg-primary/10 font-mono text-[0.62rem] font-semibold text-primary">{result.symbol.slice(0, 2)}</span>
          <span className="flex flex-col gap-0.5"><span className="font-mono text-sm font-semibold">{result.symbol}</span><span className="text-xs text-muted-foreground">{result.companyName}</span></span>
        </div>
        <Badge variant="outline" className={scoreTone(result.technicalScore)}>{result.technicalScore}</Badge>
      </div>
      <div className="mt-4 flex items-center justify-between gap-3"><SetupFingerprint fingerprint={result.fingerprint} /><span className="font-mono text-sm font-semibold">{formatPrice(result.close)}</span></div>
      <div className="mt-4 grid grid-cols-3 gap-3 border-t border-border/70 pt-3 text-xs"><span className="text-muted-foreground">RS <strong className="ml-1 font-mono font-medium text-foreground">{result.rsRating}</strong></span><span className="text-center text-muted-foreground">From high <strong className="ml-1 font-mono font-medium text-foreground">{result.pctFrom52WeekHigh.toFixed(1)}%</strong></span><span className="text-right text-muted-foreground">Dry-up <strong className="ml-1 font-mono font-medium text-foreground">{result.volumeDryUpRatio.toFixed(2)}×</strong></span></div>
    </Link>
  )
}

function ScannerSkeleton() {
  return <div className="flex flex-col gap-4 p-6">{Array.from({ length: 5 }).map((_, index) => <div key={index} className="flex items-center gap-4"><Skeleton className="size-9 rounded-xl" /><Skeleton className="h-4 flex-1" /><Skeleton className="h-4 w-16" /><Skeleton className="h-4 w-28" /></div>)}</div>
}

function ScannerEmpty({ search, onClear }: { search: string; onClear: () => void }) {
  return <Empty className="min-h-64 border-0"><EmptyHeader><EmptyMedia variant="icon"><SearchIcon /></EmptyMedia><EmptyTitle>No stocks match “{search}”</EmptyTitle><EmptyDescription>Try a symbol, company name, or sector.</EmptyDescription></EmptyHeader><Button variant="outline" onClick={onClear}>Clear search</Button></Empty>
}

function ScannerError() {
  return <Empty className="min-h-64 border-0"><EmptyHeader><EmptyTitle>Preview results are unavailable</EmptyTitle><EmptyDescription>Refresh the page to load the deterministic scanner examples again.</EmptyDescription></EmptyHeader></Empty>
}
