import { useEffect, useState } from "react"
import {
  BookOpen,
  FilterIcon,
  Loader2Icon,
  Sparkles,
  Star,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
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
  const prefix = value >= 0 ? "+" : ""
  return `${prefix}₹${value.toFixed(2)}`
}

function formatR(value: number | null | undefined): string {
  if (value == null) return "—"
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}R`
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
      setup_tags: setupTags
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean),
      mistake_tags: mistakeTags
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean),
      emotion_tags: emotionTags
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean),
      lessons,
    })
  }

  const handleScanWithAi = async () => {
    const run = await createAiRun.mutateAsync({})
    setAiRunId(run.id)
  }

  const summaryMetrics = (summary?.summary ?? {}) as Record<string, unknown>

  return (
    <div className="flex h-full flex-col bg-[#080a0e] font-mono text-xs select-none">
      <div className="grid shrink-0 grid-cols-2 gap-2 border-b border-[#252932] bg-[#0d1117] p-3 md:grid-cols-4 lg:grid-cols-6">
        <StatCard label="TRADES" value={String(summaryMetrics.trade_count ?? 0)} />
        <StatCard
          label="WIN RATE"
          value={
            summaryMetrics.win_rate != null
              ? `${Number(summaryMetrics.win_rate).toFixed(1)}%`
              : "—"
          }
        />
        <StatCard
          label="NET P&L"
          value={formatMoney(Number(summaryMetrics.net_pnl ?? 0))}
          positive={Number(summaryMetrics.net_pnl ?? 0) >= 0}
        />
        <StatCard
          label="PROFIT FACTOR"
          value={
            summaryMetrics.profit_factor != null
              ? Number(summaryMetrics.profit_factor).toFixed(2)
              : "—"
          }
        />
        <StatCard
          label="AVG R"
          value={
            summaryMetrics.avg_r != null
              ? formatR(Number(summaryMetrics.avg_r))
              : "—"
          }
        />
        <StatCard
          label="MAX DD"
          value={
            summaryMetrics.max_drawdown != null
              ? formatMoney(Number(summaryMetrics.max_drawdown))
              : "—"
          }
        />
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-[#252932] bg-[#0d1117] px-3 py-2">
        <FilterIcon className="h-3.5 w-3.5 text-[#8b949e]" aria-hidden="true" />
        <Input
          className="h-7 w-36 bg-[#080a0e] text-xs"
          placeholder="Symbol filter"
          onChange={(e) =>
            setFilters((prev) => ({ ...prev, symbol: e.target.value || undefined }))
          }
        />
        <select
          className="h-7 rounded border border-[#252932] bg-[#080a0e] px-2 text-xs"
          value={filters.execution_mode ?? ""}
          onChange={(e) =>
            setFilters((prev) => ({
              ...prev,
              execution_mode: (e.target.value || undefined) as "paper" | "live" | undefined,
            }))
          }
        >
          <option value="">Paper + Live</option>
          <option value="paper">Paper</option>
          <option value="live">Live</option>
        </select>
        <select
          className="h-7 rounded border border-[#252932] bg-[#080a0e] px-2 text-xs"
          value={bucket}
          onChange={(e) => setBucket(e.target.value as PeriodBucket)}
        >
          <option value="day">Daily</option>
          <option value="week">Weekly</option>
          <option value="month">Monthly</option>
          <option value="year">Yearly</option>
        </select>
        <Button
          size="sm"
          variant="outline"
          className="h-7 gap-1 text-xs"
          onClick={() => void handleScanWithAi()}
          disabled={createAiRun.isPending}
        >
          {createAiRun.isPending ? (
            <Loader2Icon className="h-3 w-3 animate-spin" aria-hidden="true" />
          ) : (
            <Sparkles className="h-3 w-3" aria-hidden="true" />
          )}
          Scan with AI
        </Button>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="flex w-80 shrink-0 flex-col border-r border-[#252932] bg-[#0d1117]">
          <div className="flex h-9 items-center gap-1.5 border-b border-[#252932] px-3 font-bold text-[#e6edf3]">
            <BookOpen className="h-4 w-4 text-[#3b82f6]" aria-hidden="true" />
            <span>TRADE JOURNAL</span>
          </div>
          <div className="flex-1 divide-y divide-[#161b22] overflow-y-auto">
            {listLoading && (
              <div className="p-3 text-[#8b949e]">Loading journal entries…</div>
            )}
            {!listLoading && items.length === 0 && (
              <div className="p-3 text-[#8b949e]">
                No journal entries yet. Future fills will appear here automatically.
              </div>
            )}
            {items.map((entry) => {
              const pnl = entry.net_pnl ?? entry.gross_pnl
              return (
                <button
                  key={entry.id}
                  type="button"
                  onClick={() => setSelectedId(entry.id)}
                  className={`w-full p-3 text-left transition-colors ${
                    selectedId === entry.id
                      ? "border-l-2 border-[#3b82f6] bg-[#1c2128]"
                      : "hover:bg-[#161b22]"
                  }`}
                >
                  <div className="mb-1 flex items-center justify-between">
                    <span className="font-bold text-[#3b82f6]">{entry.symbol}</span>
                    <span
                      className={`font-bold ${
                        (pnl ?? 0) >= 0 ? "text-[#22c55e]" : "text-[#ef4444]"
                      }`}
                    >
                      {formatMoney(pnl)}
                    </span>
                  </div>
                  <div className="flex justify-between text-[11px] text-[#8b949e]">
                    <span>{entry.setup_tags?.[0] ?? entry.regime ?? entry.execution_mode}</span>
                    <span>
                      {entry.closed_at
                        ? new Date(entry.closed_at).toLocaleDateString("en-IN")
                        : "Open"}
                    </span>
                  </div>
                </button>
              )
            })}
          </div>
        </div>

        <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-4">
          {!selectedId && (
            <div className="text-[#8b949e]">Select a trade to review entry context and fills.</div>
          )}
          {selectedId && detailLoading && (
            <div className="text-[#8b949e]">Loading trade detail…</div>
          )}
          {detail && (
            <>
              <div className="flex items-center justify-between rounded border border-[#252932] bg-[#0d1117] p-3">
                <div>
                  <h3 className="text-sm font-bold text-[#3b82f6]">
                    {detail.symbol} — {detail.status.toUpperCase()}
                  </h3>
                  <p className="text-[11px] text-[#8b949e]">
                    Closed {detail.closed_at ? new Date(detail.closed_at).toLocaleString("en-IN") : "—"}
                    {" · "}
                    {formatR(detail.net_r_multiple)} · Regime: {detail.regime ?? "—"}
                  </p>
                </div>
                <div className="text-right">
                  <div
                    className={`text-lg font-bold ${
                      (detail.net_pnl ?? 0) >= 0 ? "text-[#22c55e]" : "text-[#ef4444]"
                    }`}
                  >
                    {formatMoney(detail.net_pnl)}
                  </div>
                  <div className="text-[10px] text-[#8b949e]">
                    Gross {formatMoney(detail.gross_pnl)} · Charges{" "}
                    {detail.charge_quality}
                  </div>
                </div>
              </div>

              {detail.artifact_status === "captured" && (
                <img
                  src={journalChartUrl(detail.id)}
                  alt={`Entry chart for ${detail.symbol}`}
                  className="max-h-80 rounded border border-[#252932]"
                />
              )}

              <div className="grid gap-3 md:grid-cols-2">
                <Panel title="Plan vs execution">
                  <Row label="Planned entry" value={String(detail.entry_snapshot?.planned_entry_price ?? "—")} />
                  <Row label="First fill" value={String(detail.first_entry_price ?? "—")} />
                  <Row label="Weighted entry" value={String(detail.weighted_entry_price ?? "—")} />
                  <Row label="Weighted exit" value={String(detail.weighted_exit_price ?? "—")} />
                  <Row label="Exit outcome" value={detail.exit_outcome ?? "—"} />
                </Panel>
                <Panel title="Market evidence">
                  <Row label="Regime" value={detail.regime ?? "—"} />
                  <Row label="EOD date" value={detail.reference_eod_date ?? "—"} />
                  <Row label="Breadth 50" value={String(detail.regime_evidence?.breadth_above_sma_50_pct ?? "—")} />
                </Panel>
              </div>

              <Panel title="Fill timeline">
                <div className="space-y-1">
                  {(detail.exit_fills ?? []).map((fill) => (
                    <div key={String(fill.order_fill_id)} className="text-[11px] text-[#8b949e]">
                      Exit {String(fill.quantity)} @ {String(fill.price)} ({String(fill.exit_reason)})
                    </div>
                  ))}
                  {detail.exit_fills?.length === 0 && (
                    <div className="text-[11px] text-[#8b949e]">No exit fills recorded yet.</div>
                  )}
                </div>
              </Panel>

              <Panel title="Review editor">
                <div className="mb-2 flex gap-1">
                  {[1, 2, 3, 4, 5].map((star) => (
                    <button
                      key={star}
                      type="button"
                      onClick={() => setRating(star)}
                      aria-label={`Rate ${star} stars`}
                    >
                      <Star
                        className={`h-4 w-4 ${
                          rating != null && star <= rating
                            ? "fill-[#eab308] text-[#eab308]"
                            : "text-[#8b949e]"
                        }`}
                      />
                    </button>
                  ))}
                </div>
                <Textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  className="mb-2 min-h-24 bg-[#080a0e] text-xs"
                  placeholder="Trade notes…"
                />
                <Input
                  value={setupTags}
                  onChange={(e) => setSetupTags(e.target.value)}
                  className="mb-2 h-7 bg-[#080a0e] text-xs"
                  placeholder="Setup tags (comma separated)"
                />
                <Input
                  value={mistakeTags}
                  onChange={(e) => setMistakeTags(e.target.value)}
                  className="mb-2 h-7 bg-[#080a0e] text-xs"
                  placeholder="Mistake tags"
                />
                <Input
                  value={emotionTags}
                  onChange={(e) => setEmotionTags(e.target.value)}
                  className="mb-2 h-7 bg-[#080a0e] text-xs"
                  placeholder="Emotion / discipline tags"
                />
                <Textarea
                  value={lessons}
                  onChange={(e) => setLessons(e.target.value)}
                  className="mb-2 min-h-16 bg-[#080a0e] text-xs"
                  placeholder="Lessons learned"
                />
                <Button
                  size="sm"
                  onClick={handleSaveReview}
                  disabled={updateReview.isPending}
                >
                  Save review
                </Button>
              </Panel>
            </>
          )}

          {aiRun && (
            <Panel title="AI Coach report">
              <div className="mb-2 flex items-center gap-2">
                <Badge variant="outline">{aiRun.status}</Badge>
                {aiRun.status === "running" || aiRun.status === "queued" ? (
                  <Loader2Icon className="h-3 w-3 animate-spin" aria-hidden="true" />
                ) : null}
              </div>
              {aiRun.status === "succeeded" && aiRun.result && (
                <div className="space-y-2 text-[11px] text-[#e6edf3]">
                  <SectionList title="Strengths" items={aiRun.result.strengths as string[]} />
                  <SectionList title="Weaknesses" items={aiRun.result.weaknesses as string[]} />
                  <SectionList
                    title="Recurring mistakes"
                    items={aiRun.result.recurring_mistakes as string[]}
                  />
                </div>
              )}
              {aiRun.error_message && (
                <p className="text-[11px] text-[#ef4444]">{aiRun.error_message}</p>
              )}
            </Panel>
          )}
        </div>
      </div>
    </div>
  )
}

function StatCard({
  label,
  value,
  positive,
}: {
  label: string
  value: string
  positive?: boolean
}) {
  return (
    <div className="rounded border border-[#252932] bg-[#080a0e] p-2">
      <span className="block text-[10px] text-[#8b949e]">{label}</span>
      <span
        className={`text-sm font-bold ${
          positive === undefined
            ? "text-[#e6edf3]"
            : positive
              ? "text-[#22c55e]"
              : "text-[#ef4444]"
        }`}
      >
        {value}
      </span>
    </div>
  )
}

function Panel({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded border border-[#252932] bg-[#0d1117] p-3">
      <h4 className="mb-2 text-[11px] font-bold uppercase text-[#8b949e]">{title}</h4>
      {children}
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between text-[11px]">
      <span className="text-[#8b949e]">{label}</span>
      <span className="text-[#e6edf3]">{value}</span>
    </div>
  )
}

function SectionList({ title, items }: { title: string; items?: string[] }) {
  if (!items?.length) return null
  return (
    <div>
      <div className="mb-1 font-bold text-[#3b82f6]">{title}</div>
      <ul className="list-disc pl-4 text-[#8b949e]">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  )
}
