import { useEffect, useState, type ReactNode } from "react"
import { BookOpen, Loader2Icon, Sparkles, Star } from "lucide-react"

import { StatusChip, type StatusTone } from "@/components/terminal/bits"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"
import { toneCls } from "@/lib/format"
import {
  journalChartUrl,
  useAiCoachRun,
  useCreateAiCoachRun,
  useJournalEntries,
  useJournalEntry,
  useJournalSummary,
  useUpdateJournalReview,
  type JournalFilters,
  type PeriodBucket,
} from "@/features/journal/api"

function formatMoney(value: number | null | undefined): string {
  if (value == null) return "—"
  return value >= 0 ? `+₹${value.toFixed(2)}` : `₹${value.toFixed(2)}`
}

function formatR(value: number | null | undefined): string {
  if (value == null) return "—"
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}R`
}

function runTone(status: string | undefined): StatusTone {
  const s = status ?? ""
  if (s.includes("succeed") || s.includes("complete")) return "fill"
  if (s.includes("fail")) return "rej"
  return "work"
}

function PanelBox({
  title,
  children,
  className,
}: {
  title: string
  children: ReactNode
  className?: string
}) {
  return (
    <section className={cn("rounded-lg border border-border bg-surface", className)}>
      <header className="flex items-center justify-between border-b border-border-soft px-4 py-2">
        <h4 className="mono text-[10px] font-bold uppercase tracking-[0.12em] text-muted-text">
          {title}
        </h4>
      </header>
      <div className="space-y-2 p-4">{children}</div>
    </section>
  )
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-6 text-[12px]">
      <span className="shrink-0 text-muted-text">{label}</span>
      <span className="mono truncate text-fg2" title={value}>
        {value}
      </span>
    </div>
  )
}

function SectionList({ title, items }: { title: string; items?: string[] }) {
  if (!items?.length) return null
  return (
    <div>
      <div className="mb-1 text-[11px] font-bold text-accent">{title}</div>
      <ul className="list-disc space-y-0.5 pl-4 text-[11.5px] text-muted-foreground">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  )
}

export function JournalView() {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [bucket, setBucket] = useState<PeriodBucket>("month")
  const [filters, setFilters] = useState<JournalFilters>({
    status: "closed",
    limit: 100,
  })
  const [notes, setNotes] = useState("")
  const [rating, setRating] = useState<number | null>(null)
  const [setupTags, setSetupTags] = useState("")
  const [mistakeTags, setMistakeTags] = useState("")
  const [emotionTags, setEmotionTags] = useState("")
  const [lessons, setLessons] = useState("")
  const [aiRunId, setAiRunId] = useState<string | null>(null)

  const { data: listData, isLoading: listLoading } = useJournalEntries(filters)
  const { data: detail, isLoading: detailLoading } = useJournalEntry(selectedId)
  const { data: summary } = useJournalSummary({ bucket, ...filters })
  const updateReview = useUpdateJournalReview(selectedId ?? "")
  const createAiRun = useCreateAiCoachRun()
  const { data: aiRun } = useAiCoachRun(aiRunId)

  const items = listData?.items ?? []

  useEffect(() => {
    if (!detail) return
    setNotes(detail.notes ?? "")
    setRating(detail.execution_rating)
    setSetupTags((detail.setup_tags ?? []).join(", "))
    setMistakeTags((detail.mistake_tags ?? []).join(", "))
    setEmotionTags((detail.emotion_tags ?? []).join(", "))
    setLessons(detail.lessons ?? "")
  }, [detail])

  const handleSaveReview = () => {
    if (!selectedId) return
    updateReview.mutate({
      notes,
      execution_rating: rating,
      setup_tags: setupTags.split(",").map((t) => t.trim()).filter(Boolean),
      mistake_tags: mistakeTags.split(",").map((t) => t.trim()).filter(Boolean),
      emotion_tags: emotionTags.split(",").map((t) => t.trim()).filter(Boolean),
      lessons,
    })
  }

  const handleScanWithAi = async () => {
    const run = await createAiRun.mutateAsync({})
    setAiRunId(run.id)
  }

  const metrics = (summary?.summary ?? {}) as Record<string, unknown>
  const tradeCount = Number(metrics.trade_count ?? 0)
  const winRate = metrics.win_rate != null ? Number(metrics.win_rate) : null
  const netPnl = Number(metrics.net_pnl ?? 0)
  const profitFactor = metrics.profit_factor != null ? Number(metrics.profit_factor) : null
  const avgR = metrics.avg_r != null ? Number(metrics.avg_r) : null
  const maxDd = metrics.max_drawdown != null ? Number(metrics.max_drawdown) : null
  const netTone = toneCls(netPnl)

  return (
    <section className="view h-full">
      <div className="vhead">
        <div>
          <h2>
            Trade Journal <span className="sub">closed trades · review + AI coach</span>
          </h2>
          <p className="vmeta">
            <b>{tradeCount}</b> trades · win rate{" "}
            <b>{winRate != null ? winRate.toFixed(1) + "%" : "—"}</b> · profit factor{" "}
            <b>{profitFactor != null ? profitFactor.toFixed(2) : "—"}</b> · avg R{" "}
            <b>{avgR != null ? formatR(avgR) : "—"}</b> · max DD{" "}
            <b>{maxDd != null ? formatMoney(maxDd) : "—"}</b>
          </p>
        </div>
        <div className="vhead-right">
          <div className="netp">
            <span className="lbl">NET P&L</span>
            <span className={cn("val", netTone)}>{formatMoney(netPnl)}</span>
          </div>
          <span className="note-demo">JOURNAL</span>
        </div>
      </div>

      {/* Filters */}
      <div className="sfilter">
        <label className="fsearch">
          <BookOpen aria-hidden="true" className="ic" />
          <input
            onChange={(e) => setFilters((prev) => ({ ...prev, symbol: e.target.value || undefined }))}
            placeholder="Symbol filter"
            type="text"
            value={filters.symbol ?? ""}
          />
        </label>
        <span className="fsel">
          <select
            aria-label="Execution mode filter"
            onChange={(e) =>
              setFilters((prev) => ({
                ...prev,
                execution_mode: (e.target.value || undefined) as "paper" | "live" | undefined,
              }))
            }
            value={filters.execution_mode ?? ""}
          >
            <option value="">Mode: Paper + Live</option>
            <option value="paper">Mode: Paper</option>
            <option value="live">Mode: Live</option>
          </select>
        </span>
        <span className="fsel">
          <select
            aria-label="Summary bucket"
            onChange={(e) => setBucket(e.target.value as PeriodBucket)}
            value={bucket}
          >
            <option value="day">Bucket: Daily</option>
            <option value="week">Bucket: Weekly</option>
            <option value="month">Bucket: Monthly</option>
            <option value="year">Bucket: Yearly</option>
          </select>
        </span>
        <button
          className="btn btn-primary ml-auto"
          disabled={createAiRun.isPending}
          onClick={() => void handleScanWithAi()}
          type="button"
        >
          {createAiRun.isPending ? (
            <Loader2Icon aria-hidden="true" className="btn-ic animate-spin" />
          ) : (
            <Sparkles aria-hidden="true" className="btn-ic" />
          )}
          Scan with AI
        </button>
      </div>

      {/* Master / detail */}
      <div className="flex min-h-0 flex-1">
        <aside className="flex w-[300px] shrink-0 flex-col border-r border-border bg-surface max-[900px]:hidden">
          <div className="flex h-9 shrink-0 items-center gap-1.5 border-b border-border px-3">
            <span className="mono text-[10px] font-bold uppercase tracking-[0.13em] text-fg2">
              TRADE JOURNAL
            </span>
            <span className="ml-auto mono text-[10px] text-muted-text">{items.length}</span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {listLoading && <div className="p-3 text-[11.5px] text-muted-text">Loading journal entries…</div>}
            {!listLoading && items.length === 0 && (
              <div className="p-3 text-[11.5px] leading-relaxed text-muted-text">
                No journal entries yet. Future fills will appear here automatically.
              </div>
            )}
            {items.map((entry) => {
              const pnl = entry.net_pnl ?? entry.gross_pnl
              const tone = toneCls(pnl)
              const tag = entry.setup_tags?.[0] ?? entry.regime ?? entry.execution_mode
              return (
                <button
                  className={cn(
                    "block w-full border-l-2 px-3 py-2.5 text-left transition-colors",
                    selectedId === entry.id
                      ? "border-accent bg-accent-soft"
                      : "border-transparent hover:bg-hl",
                  )}
                  key={entry.id}
                  onClick={() => setSelectedId(entry.id)}
                  type="button"
                >
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-[12.5px] font-bold text-fg">{entry.symbol}</span>
                    <span className={cn("mono shrink-0 text-[12px] font-bold", tone)}>
                      {formatMoney(pnl)}
                    </span>
                  </span>
                  <span className="mt-0.5 flex items-baseline justify-between gap-2">
                    <span className="truncate text-[10.5px] text-muted-text">{tag}</span>
                    <span className="mono shrink-0 text-[10px] text-muted-text">
                      {entry.closed_at
                        ? new Date(entry.closed_at).toLocaleDateString("en-IN")
                        : "Open"}
                    </span>
                  </span>
                </button>
              )
            })}
          </div>
        </aside>

        <div className="min-w-0 flex-1 space-y-4 overflow-y-auto p-4">
          {!selectedId && (
            <p className="px-1 pt-2 text-[12px] text-muted-text">
              Select a trade to review entry context and fills.
            </p>
          )}
          {selectedId && detailLoading && (
            <p className="px-1 pt-2 text-[12px] text-muted-text">Loading trade detail…</p>
          )}
          {detail && (
            <>
              {/* Detail head */}
              <div className="flex items-center justify-between gap-4 rounded-lg border border-border bg-surface px-4 py-3">
                <div className="min-w-0">
                  <div className="flex items-baseline gap-3">
                    <h3 className="text-[15px] font-bold text-fg">{detail.symbol}</h3>
                    <StatusChip tone={runTone(detail.status)}>{detail.status.toUpperCase()}</StatusChip>
                  </div>
                  <p className="mono mt-1 text-[11px] text-muted-text">
                    Closed{" "}
                    {detail.closed_at ? new Date(detail.closed_at).toLocaleString("en-IN") : "—"}
                    {" · "}
                    {formatR(detail.net_r_multiple)} · Regime: {detail.regime ?? "—"}
                  </p>
                </div>
                <div className="shrink-0 text-right">
                  <div
                    className={cn(
                      "mono text-lg font-bold",
                      (detail.net_pnl ?? 0) >= 0 ? "text-ok" : "text-ko",
                    )}
                  >
                    {formatMoney(detail.net_pnl)}
                  </div>
                  <div className="mono text-[10px] text-muted-text">
                    Gross {formatMoney(detail.gross_pnl)} · Charges {detail.charge_quality}
                  </div>
                </div>
              </div>

              {detail.artifact_status === "captured" && (
                <img
                  alt={`Entry chart for ${detail.symbol}`}
                  className="max-h-80 w-full rounded-lg border border-border"
                  src={journalChartUrl(detail.id)}
                />
              )}

              <div className="grid gap-4 lg:grid-cols-2">
                <PanelBox title="Plan vs execution">
                  <KV label="Planned entry" value={String(detail.entry_snapshot?.planned_entry_price ?? "—")} />
                  <KV label="First fill" value={String(detail.first_entry_price ?? "—")} />
                  <KV label="Weighted entry" value={String(detail.weighted_entry_price ?? "—")} />
                  <KV label="Weighted exit" value={String(detail.weighted_exit_price ?? "—")} />
                  <KV label="Exit outcome" value={detail.exit_outcome ?? "—"} />
                </PanelBox>
                <PanelBox title="Market evidence">
                  <KV label="Regime" value={detail.regime ?? "—"} />
                  <KV label="EOD date" value={detail.reference_eod_date ?? "—"} />
                  <KV label="Breadth 50" value={String(detail.regime_evidence?.breadth_above_sma_50_pct ?? "—")} />
                </PanelBox>
              </div>

              <PanelBox title="Fill timeline">
                {(detail.exit_fills ?? []).length === 0 ? (
                  <p className="text-[11.5px] text-muted-text">No exit fills recorded yet.</p>
                ) : (
                  <ul className="mono space-y-1 text-[11.5px] text-muted-foreground">
                    {(detail.exit_fills ?? []).map((fill) => (
                      <li key={String(fill.order_fill_id)}>
                        Exit {String(fill.quantity)} @ {String(fill.price)} ({String(fill.exit_reason)})
                      </li>
                    ))}
                  </ul>
                )}
              </PanelBox>

              <PanelBox title="Review editor">
                <div className="flex items-center gap-1">
                  <span className="mono mr-1 text-[10px] uppercase tracking-[0.1em] text-muted-text">Rating</span>
                  {[1, 2, 3, 4, 5].map((star) => (
                    <button
                      aria-label={`Rate ${star} stars`}
                      key={star}
                      onClick={() => setRating(star)}
                      type="button"
                    >
                      <Star
                        className={cn(
                          "h-4 w-4",
                          rating != null && star <= rating
                            ? "fill-wa text-wa"
                            : "text-muted-text",
                        )}
                      />
                    </button>
                  ))}
                </div>
                <Textarea
                  className="min-h-24 bg-field text-xs"
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Trade notes…"
                  value={notes}
                />
                <Input
                  className="h-8 bg-field text-xs"
                  onChange={(e) => setSetupTags(e.target.value)}
                  placeholder="Setup tags (comma separated)"
                  value={setupTags}
                />
                <Input
                  className="h-8 bg-field text-xs"
                  onChange={(e) => setMistakeTags(e.target.value)}
                  placeholder="Mistake tags"
                  value={mistakeTags}
                />
                <Input
                  className="h-8 bg-field text-xs"
                  onChange={(e) => setEmotionTags(e.target.value)}
                  placeholder="Emotion / discipline tags"
                  value={emotionTags}
                />
                <Textarea
                  className="min-h-16 bg-field text-xs"
                  onChange={(e) => setLessons(e.target.value)}
                  placeholder="Lessons learned"
                  value={lessons}
                />
                <Button disabled={updateReview.isPending} onClick={handleSaveReview} size="sm" type="button">
                  Save review
                </Button>
              </PanelBox>
            </>
          )}

          {aiRun && (
            <PanelBox title="AI Coach report">
              <div className="flex items-center gap-2">
                <StatusChip tone={runTone(aiRun.status)}>{aiRun.status.toUpperCase()}</StatusChip>
                {(aiRun.status === "running" || aiRun.status === "queued") && (
                  <Loader2Icon aria-hidden="true" className="h-3 w-3 animate-spin text-accent" />
                )}
              </div>
              {aiRun.status === "succeeded" && aiRun.result && (
                <div className="space-y-3 text-[12px] text-fg">
                  <SectionList title="Strengths" items={aiRun.result.strengths as string[]} />
                  <SectionList title="Weaknesses" items={aiRun.result.weaknesses as string[]} />
                  <SectionList
                    title="Recurring mistakes"
                    items={aiRun.result.recurring_mistakes as string[]}
                  />
                </div>
              )}
              {aiRun.error_message && <p className="text-[11.5px] text-ko">{aiRun.error_message}</p>}
            </PanelBox>
          )}
        </div>
      </div>
    </section>
  )
}
