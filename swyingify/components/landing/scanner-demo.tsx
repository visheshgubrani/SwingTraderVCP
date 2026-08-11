"use client"

import { useCallback, useEffect, useRef, useState } from "react"

import { VcpDemoChart } from "@/components/landing/vcp-demo-chart"
import { Reveal } from "@/components/landing/reveal"
import {
  LANDING_CHECKS,
  STATUS_LABELS,
  type ScanDemoItem,
} from "@/lib/landing/demo-data"
import { cn } from "@/lib/utils"

function padRank(n: number) {
  return (n < 10 ? "0" : "") + n
}

function formatScanDate(asOfDate?: string) {
  if (asOfDate) {
    const d = new Date(`${asOfDate}T00:00:00.000Z`)
    if (!Number.isNaN(d.getTime())) {
      const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
      const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
      return `${days[d.getUTCDay()]} ${d.getUTCDate()} ${months[d.getUTCMonth()]}`
    }
  }
  const d = new Date()
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
  return `${days[d.getDay()]} ${d.getDate()} ${months[d.getMonth()]}`
}

type ScannerDemoProps = {
  scans: ScanDemoItem[]
  asOfDate: string
  isLiveData: boolean
}

export function ScannerDemo({ scans, asOfDate, isLiveData }: ScannerDemoProps) {
  const [idx, setIdx] = useState(0)
  const [manual, setManual] = useState(false)
  const [fpKey, setFpKey] = useState(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const safeScans = scans.length > 0 ? scans : []
  const item = safeScans[Math.min(idx, Math.max(safeScans.length - 1, 0))]

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const resetTimer = useCallback(
    (ms: number) => {
      const reduced =
        typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches
      if (reduced) return
      clearTimer()
      timerRef.current = setInterval(() => {
        if (document.hidden) return
        setManual(false)
        setIdx((i) => (i + 1) % Math.max(safeScans.length, 1))
        setFpKey((k) => k + 1)
      }, ms)
    },
    [clearTimer, safeScans.length],
  )

  const select = useCallback(
    (i: number, isManual: boolean) => {
      setIdx(i)
      setManual(isManual)
      setFpKey((k) => k + 1)
      resetTimer(isManual ? 9000 : 3400)
    },
    [resetTimer],
  )

  useEffect(() => {
    resetTimer(3400)
    return clearTimer
  }, [resetTimer, clearTimer])

  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden) clearTimer()
      else resetTimer(3400)
    }
    document.addEventListener("visibilitychange", onVisibility)
    return () => document.removeEventListener("visibilitychange", onVisibility)
  }, [clearTimer, resetTimer])

  return (
    <Reveal>
      <div
        className="landing-demo mt-14"
        onMouseEnter={clearTimer}
        onMouseLeave={() => resetTimer(3400)}
      >
        {item ? <DemoHead manual={manual} item={item} asOfDate={asOfDate} isLiveData={isLiveData} /> : null}
        <div className="grid border-b border-[var(--landing-border)] lg:grid-cols-[1.55fr_1fr]">
          <div className="border-b border-[var(--landing-border)] p-5 lg:border-b-0 lg:border-r">
            {item ? (
              <>
                <VcpDemoChart item={item} isLiveData={isLiveData} />
                <ChartMeta stage={item.stage} isLiveData={isLiveData} />
              </>
            ) : (
              <p className="text-sm text-[var(--landing-muted)]">Waiting for tonight&apos;s Standard shortlist.</p>
            )}
          </div>
          {item ? <FingerprintPanel key={fpKey} item={item} /> : null}
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6">
          {safeScans.map((scan, i) => (
            <button
              key={scan.sym}
              type="button"
              className={cn(
                "landing-strip-cell flex flex-col gap-1.5 border-0 border-r border-[var(--landing-border-soft)] bg-transparent p-3.5 text-left font-[family-name:var(--font-landing-mono)] text-xs text-[var(--landing-fg)] transition-colors hover:bg-white/5 focus-visible:outline-none focus-visible:shadow-[var(--landing-focus)] last:border-r-0",
                i === idx && "active bg-[var(--landing-surface-warm)] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.2)]",
              )}
              aria-label={`Select ${scan.sym}`}
              aria-pressed={i === idx}
              onClick={() => select(i, true)}
            >
              <span className="flex items-baseline justify-between gap-1.5">
                <span className="tracking-wider text-[var(--landing-muted)]">{padRank(i + 1)}</span>
                <span className="tracking-wide">{scan.sym}</span>
              </span>
              <span className="text-[var(--landing-fg-2)]">{scan.score}</span>
              <span className="h-0.5 bg-[var(--landing-border-soft)]">
                <i className="landing-strip-bar-fill block h-full w-0 bg-[var(--landing-meta)]" />
              </span>
            </button>
          ))}
        </div>
        <div className="flex flex-wrap justify-between gap-4 border-t border-[var(--landing-border)] px-[18px] py-3 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-wider text-[var(--landing-muted)]">
          {isLiveData
            ? "Live Standard shortlist · Updates after every cash-market close"
            : "Illustrative preview on real Nifty 500 symbols · Live shortlist publishes after every close"}
        </div>
      </div>
    </Reveal>
  )
}

function DemoHead({
  manual,
  item,
  asOfDate,
  isLiveData,
}: {
  manual: boolean
  item: ScanDemoItem
  asOfDate: string
  isLiveData: boolean
}) {
  const scanDate = formatScanDate(asOfDate)

  return (
    <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[var(--landing-border)] px-[18px] py-3.5">
      <div className="flex flex-wrap items-center gap-3.5 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-fg)]">
        <span>Swyingify scanner</span>
        <span className="text-[var(--landing-meta)]">/</span>
        <span>Minervini VCP · Standard</span>
        <span className="text-[var(--landing-meta)]">/</span>
        <span>{scanDate}</span>
      </div>
      <div className="flex items-center gap-2 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest text-[var(--landing-muted)]">
        <span className="landing-dot" aria-hidden />
        <span>
          {manual
            ? `Paused — ${item.sym}`
            : isLiveData
              ? "Tonight's shortlist"
              : "Preview shortlist"}
        </span>
      </div>
    </div>
  )
}

function ChartMeta({ stage, isLiveData }: { stage: string; isLiveData?: boolean }) {
  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap gap-4 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-wider text-[var(--landing-muted)]">
        <span>Daily · EOD</span>
        {!isLiveData ? (
          <>
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block h-0.5 w-[18px] bg-white/60" />
              Stage 2 trend
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block w-[18px] border-t border-dashed border-white/40" />
              Pivot
            </span>
          </>
        ) : null}
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block size-2 bg-white/30" />
          Volume
        </span>
      </div>
      <span className="font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-wider text-[var(--landing-muted)]">
        {stage}
      </span>
    </div>
  )
}

function FingerprintPanel({ item }: { item: ScanDemoItem }) {
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const root = listRef.current
    if (!root) return

    const rows = [...root.querySelectorAll<HTMLElement>("[data-fp-row]")]
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches

    rows.forEach((row) => row.classList.remove("on"))

    if (reduced) {
      rows.forEach((row) => row.classList.add("on"))
      return
    }

    const timers = rows.map((row, i) => window.setTimeout(() => row.classList.add("on"), 140 * i))
    return () => timers.forEach(clearTimeout)
  }, [item.sym])

  return (
    <aside className="flex flex-col p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <strong className="font-[family-name:var(--font-landing-mono)] text-[26px] font-light leading-tight text-[var(--landing-fg)]">
          {item.sym}
        </strong>
        <span className="font-[family-name:var(--font-landing-mono)] text-[30px] font-light leading-none text-[var(--landing-fg)]">
          <small className="mr-1.5 text-xs uppercase tracking-widest text-[var(--landing-muted)]">Score</small>
          {item.score}
        </span>
      </div>
      <div className="mt-1.5 flex gap-3.5 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-wide text-[var(--landing-muted)]">
        <span>{item.name}</span>
        <span>{item.sector}</span>
      </div>
      <div ref={listRef} className="mt-5 flex-1 border-t border-[var(--landing-border)]">
        {LANDING_CHECKS.map((ch) => {
          const st = STATUS_LABELS[item.c[ch.k]] ?? STATUS_LABELS[3]
          const statusClass =
            st[1] === "s-strong"
              ? "landing-status-strong"
              : st[1] === "s-support"
                ? "landing-status-support"
                : "landing-status-watch"
          return (
            <div
              key={ch.k}
              data-fp-row
              className="landing-fp-row flex items-center justify-between gap-3.5 border-b border-[var(--landing-border-soft)] py-2.5"
            >
              <span className="text-sm text-[var(--landing-fg-2)]">{ch.l}</span>
              <span
                className={cn(
                  "font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-widest",
                  statusClass,
                )}
              >
                {st[0]}
              </span>
            </div>
          )
        })}
      </div>
      <div className="mt-4 flex flex-wrap justify-between gap-3 landing-kicker">
        <span>
          RS <span className="text-[var(--landing-fg)]">{item.rs}</span>
        </span>
        <span>
          % from high <span className="text-[var(--landing-fg)]">{item.high.toFixed(1)}</span>
        </span>
        <span>
          ADTV <span className="text-[var(--landing-fg)]">{item.adtv}</span>
        </span>
      </div>
    </aside>
  )
}
