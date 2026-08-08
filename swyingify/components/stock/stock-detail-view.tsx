"use client"

import dynamic from "next/dynamic"
import Link from "next/link"
import { useMemo, useState } from "react"

import { ChangeCell } from "@/components/scanner-board/board-formatters"
import { Reveal } from "@/components/landing/reveal"
import {
  CHART_RANGE_LABELS,
  CHART_RANGE_SLICES,
  generateStockCandles,
  type ChartRange,
} from "@/lib/scanner/generate-stock-candles"
import {
  buildStockChecks,
  deriveStockLevels,
  formatInrOneDecimal,
  formatInrWhole,
  formatStockDate,
} from "@/lib/scanner/stock-derive"
import type { ScannerResultPreview } from "@/lib/scanner/types"
import { cn } from "@/lib/utils"

const StockChartDark = dynamic(
  () => import("@/components/stock/stock-chart-dark").then((m) => m.StockChartDark),
  { ssr: false, loading: () => <div className="h-[300px] min-[901px]:h-[460px] w-full animate-pulse bg-white/5" /> },
)

export function StockDetailView({
  result,
  showChrome = true,
}: {
  result: ScannerResultPreview
  showChrome?: boolean
}) {
  const [range, setRange] = useState<ChartRange>("3m")
  const levels = useMemo(() => deriveStockLevels(result), [result])
  const checks = useMemo(() => buildStockChecks(result, levels), [result, levels])
  const passCount = checks.filter((c) => c.pass).length

  const allCandles = useMemo(
    () =>
      generateStockCandles({
        close: result.close,
        dayChangePct: result.dayChangePct,
        sparkSeed: result.sparkSeed,
        adtvCrore: result.adtvCrore,
        pivot: levels.pivot,
        baseLow: levels.baseLow,
        prevClose: levels.prevClose,
      }),
    [result, levels],
  )

  const sliceCandles = useMemo(
    () => allCandles.slice(-CHART_RANGE_SLICES[range]),
    [allCandles, range],
  )

  const asOfDate = formatStockDate()

  return (
    <>
      <header className={showChrome ? "pt-[clamp(40px,6vw,64px)]" : "pt-2"}>
        <div className={showChrome ? "mx-auto max-w-[1200px] px-6 max-sm:px-3" : ""}>
          {showChrome ? (
            <Reveal>
              <Link
                href="/scanner"
                className="-ml-2 inline-flex min-h-11 items-center px-2 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)] transition-colors hover:text-[var(--landing-fg)]"
              >
                ← Daily board
              </Link>
            </Reveal>
          ) : null}
          <Reveal>
            <p className={cn("landing-kicker", showChrome && "mt-[26px]")}>
              Minervini VCP · {result.sector} · End of day
            </p>
          </Reveal>
          <Reveal>
            <h1 className="stock-page-title mt-3.5">{result.symbol}.</h1>
          </Reveal>
          <Reveal>
            <p className="mt-[18px] text-base text-[var(--landing-fg-2)]">
              {result.companyName} · {result.sector}
            </p>
          </Reveal>
          <Reveal>
            <div className="mt-8 flex flex-wrap items-center justify-between gap-5 border border-[var(--landing-border)] bg-[var(--landing-surface)] px-[22px] py-5 max-sm:px-[18px]">
              <div className="flex flex-wrap items-baseline gap-4">
                <span className="font-[family-name:var(--font-landing-mono)] text-[clamp(30px,3.6vw,44px)] font-light text-[var(--landing-fg)]">
                  {formatInrOneDecimal(result.close)}
                </span>
                <ChangeCell value={result.dayChangePct} />
              </div>
              <div className="flex flex-wrap items-center gap-4">
                <span className="border border-[var(--landing-border)] px-2.5 py-1 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">
                  EOD snapshot
                </span>
                <span className="font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-wider text-[var(--landing-muted)]">
                  as of close · {asOfDate}
                </span>
              </div>
            </div>
          </Reveal>
        </div>
      </header>

      <section className="pt-14 min-[901px]:pt-24">
        <div className={showChrome ? "mx-auto max-w-[1200px] px-6 max-sm:px-3" : ""}>
          <Reveal>
            <div className="flex flex-wrap items-center justify-between gap-5">
              <h2 className="stock-section-title">Price & volume</h2>
              <div className="board-tabs" role="group" aria-label="Chart range">
                {(["3m", "6m", "1y"] as const).map((value) => (
                  <button
                    key={value}
                    type="button"
                    className={cn("board-tab", range === value && "active")}
                    aria-pressed={range === value}
                    onClick={() => setRange(value)}
                  >
                    {value.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
          </Reveal>
          <Reveal>
            <div className="mt-6 border border-[var(--landing-border)] bg-[var(--landing-surface)] p-3.5">
              <StockChartDark
                candles={sliceCandles}
                symbol={result.symbol}
                lastClose={result.close}
                dayChangePct={result.dayChangePct}
                rangeLabel={CHART_RANGE_LABELS[range]}
              />
            </div>
            <p className="mt-3.5 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-wider text-[var(--landing-muted)]">
              Daily candles · {CHART_RANGE_LABELS[range]} view
            </p>
          </Reveal>
        </div>
      </section>

      <section className="pt-14 min-[901px]:pt-24">
        <div className={showChrome ? "mx-auto max-w-[1200px] px-6 max-sm:px-3" : ""}>
          <Reveal>
            <div className="stock-stat-grid grid grid-cols-2 gap-x-8 gap-y-7 border-t border-[var(--landing-border)] pt-3.5 min-[901px]:grid-cols-4">
              <Stat label="52-week high" value={formatInrWhole(levels.high52)} />
              <Stat label="52-week low" value={formatInrWhole(levels.low52)} />
              <Stat label="% from high" value={`${result.pctFrom52WeekHigh.toFixed(1)}%`} />
              <Stat
                label="RS rating"
                value={
                  <>
                    {result.rsRating} <small className="text-xs text-[var(--landing-muted)]">/ 100</small>
                  </>
                }
              />
              <Stat label="ADTV · 20d" value={`${result.adtvCrore.toLocaleString("en-IN")} Cr`} />
              <Stat label="Volume dry-up" value={`${result.volumeDryUpRatio.toFixed(2)}×`} />
              <Stat label="ATR ratio" value={result.atrRatio.toFixed(2)} sub="ATR(10) vs ATR(50)" />
              <Stat
                label="Technical score"
                value={
                  <>
                    {result.technicalScore}{" "}
                    <small className="text-xs text-[var(--landing-muted)]">{result.grade}</small>
                  </>
                }
              />
            </div>
          </Reveal>
        </div>
      </section>

      <section className="pt-14 min-[901px]:pt-24">
        <div
          className={cn(
            "stock-detail-grid grid items-start gap-12 min-[901px]:grid-cols-[1.25fr_1fr] min-[901px]:gap-12",
            showChrome && "mx-auto max-w-[1200px] px-6 max-sm:px-3",
          )}
        >
          <Reveal>
            <div>
              <p className="landing-kicker">Why it was selected</p>
              <h2 className="stock-section-title mt-4">
                {passCount} of {checks.length} checks pass.
              </h2>
              <p className="landing-lead mt-3.5">
                The scanner checks five things against the daily close. A setup needs the trend, the proximity, and
                the tightening to line up before it earns a rank.
              </p>
              <div className="mt-7">
                {checks.map((check) => (
                  <div
                    key={check.key}
                    className={cn(
                      "stock-check border-t border-[var(--landing-border)] py-[18px] last:border-b",
                      check.pass ? "pass" : "weak",
                    )}
                  >
                    <div className="flex items-center gap-3">
                      <span
                        className={cn(
                          "stock-check-dot size-[7px] shrink-0 rounded-full bg-[var(--landing-fg)]",
                          !check.pass && "bg-[var(--landing-meta)]",
                        )}
                      />
                      <span
                        className={cn(
                          "stock-check-label flex-1 text-base text-[var(--landing-fg)]",
                          !check.pass && "text-[var(--landing-fg-2)]",
                        )}
                      >
                        {check.label}
                      </span>
                      <span
                        className={cn(
                          "font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]",
                          check.pass && "text-[var(--landing-fg)]",
                        )}
                      >
                        {check.pass ? "Pass" : "Watch"}
                      </span>
                    </div>
                    <p className="ml-[19px] mt-2 max-w-[58ch] text-sm leading-relaxed text-[var(--landing-fg-2)]">
                      {check.note}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          </Reveal>
          <Reveal>
            <aside className="border border-[var(--landing-border)] bg-[var(--landing-surface)] p-6">
              <p className="landing-kicker mb-[18px]">Key levels</p>
              <LevelRow prime name="Breakout pivot" value={formatInrOneDecimal(levels.pivot)} note="entry above this level" />
              <LevelRow name="Base low" value={formatInrOneDecimal(levels.baseLow)} note="base fails below this" />
              <LevelRow name="Base high" value={formatInrOneDecimal(levels.baseHigh)} note="resistance in the base" />
              <LevelRow name="52-week high" value={formatInrWhole(levels.high52)} note="the reference high" />
              <LevelRow name="52-week low" value={formatInrWhole(levels.low52)} note="the reference low" />
              <LevelRow
                name="ATR(10) / ATR(50)"
                value={result.atrRatio.toFixed(2)}
                note="contraction read"
                last
              />
              <p className="mt-[18px] max-w-[46ch] text-xs leading-relaxed text-[var(--landing-muted)]">
                Illustrative levels on the daily close — recomputed by the live scan after every close.
              </p>
            </aside>
          </Reveal>
        </div>
      </section>

      {showChrome ? (
        <>
          <section className="pt-14 min-[901px]:pt-24">
            <div className="mx-auto max-w-[1200px] px-6 max-sm:px-3">
              <p className="font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-wider text-[var(--landing-muted)]">
                Illustrative values on real Nifty 500 symbols · the live board replaces these numbers after every close
              </p>
            </div>
          </section>
          <footer className="mt-14 border-t border-[var(--landing-border)] min-[901px]:mt-24">
            <div className="mx-auto flex max-w-[1200px] flex-wrap items-center justify-between gap-5 px-6 py-8 max-sm:px-3">
              <span className="landing-brand text-[13px]">Swyingify</span>
              <p className="min-w-[240px] flex-1 text-xs leading-relaxed text-[var(--landing-muted)]">
                Educational purposes only. Swyingify scans markets — it does not place orders, hold positions, or give
                investment advice.
              </p>
              <Link
                href="/scanner"
                className="py-3 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)] transition-colors hover:text-[var(--landing-fg)]"
              >
                ← Daily board
              </Link>
            </div>
          </footer>
        </>
      ) : null}
    </>
  )
}

function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <div className="border-t border-[var(--landing-border)] pt-3.5">
      <p className="font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">
        {label}
      </p>
      <p className="mt-2 font-[family-name:var(--font-landing-mono)] text-[19px] leading-tight text-[var(--landing-fg)]">
        {value}
      </p>
      {sub ? <p className="mt-1 text-xs text-[var(--landing-fg-2)]">{sub}</p> : null}
    </div>
  )
}

function LevelRow({
  name,
  value,
  note,
  prime,
  last,
}: {
  name: string
  value: string
  note?: string
  prime?: boolean
  last?: boolean
}) {
  return (
    <div
      className={cn(
        "flex items-baseline justify-between gap-4 border-t border-[var(--landing-border)] py-3",
        prime && "border-t-0",
        last && "border-b border-[var(--landing-border)]",
      )}
    >
      <span className={cn("text-sm text-[var(--landing-fg-2)]", prime && "font-medium text-[var(--landing-fg)]")}>
        {name}
      </span>
      <div className="flex flex-col items-end gap-0.5">
        <span
          className={cn(
            "whitespace-nowrap text-right font-[family-name:var(--font-landing-mono)] text-[15px] tracking-wide text-[var(--landing-fg)]",
            prime && "text-[19px]",
          )}
        >
          {value}
        </span>
        {note ? <span className="max-w-[18ch] text-right text-xs text-[var(--landing-muted)]">{note}</span> : null}
      </div>
    </div>
  )
}
