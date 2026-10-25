import type { ReactNode } from "react"
import {
  ChevronDownIcon,
  ChevronUpIcon,
  FlaskConicalIcon,
  HeartIcon,
  PanelLeftOpenIcon,
  SparklesIcon,
} from "lucide-react"

import { GradeChip } from "@/components/terminal/bits"
import { useToast } from "@/components/terminal/toast"
import { Spinner } from "@/components/ui/spinner"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useTriggerSingleProposal } from "@/features/proposals/api"
import type { ScanResult } from "@/features/screener/api"
import {
  useAddWatchlistItem,
  useRemoveWatchlistItem,
  useWatchlistItems,
  useWatchlists,
} from "@/features/watchlist/api"
import { fmtNum, fmtPct, toneCls } from "@/lib/format"
import { shortSymbol } from "@/lib/marketSymbols"
import { cn } from "@/lib/utils"

function HeaderButton({
  label,
  tooltip,
  onClick,
  disabled,
  active,
  children,
}: {
  label: string
  tooltip: ReactNode
  onClick: () => void
  disabled?: boolean
  active?: boolean
  children: ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <button
            aria-label={label}
            className={cn("btn btn-ghost !h-7 !gap-1.5 !px-2.5 !text-xs", active && "!border-accent !text-accent")}
            disabled={disabled}
            onClick={onClick}
            type="button"
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent side="bottom">{tooltip}</TooltipContent>
    </Tooltip>
  )
}

function Metric({ label, children, title }: { label: string; children: ReactNode; title?: string }) {
  return (
    <span className="mcell" title={title}>
      <span className="l">{label}</span>
      <span className="v">{children}</span>
    </span>
  )
}

const SECTOR_TONE: Record<string, string> = {
  leading: "text-ok",
  neutral: "text-fg2",
  lagging: "text-ko",
  unavailable: "text-muted-text",
}

/** Toggle the symbol in the server-active watchlist (same list the WS subscribes). */
function WatchToggle({ symbol }: { symbol: string }) {
  const { toast } = useToast()
  const lists = useWatchlists()
  const active = lists.data?.find((list) => list.is_active) ?? lists.data?.[0] ?? null
  const items = useWatchlistItems(active?.id ?? null)
  const add = useAddWatchlistItem(active?.id ?? null)
  const remove = useRemoveWatchlistItem(active?.id ?? null)
  const existing = items.data?.find((item) => item.fyers_symbol === symbol)
  const name = active?.name ?? "watchlist"

  const toggle = async () => {
    try {
      if (existing) {
        await remove.mutateAsync(existing.instrument_id)
        toast("info", { title: `${shortSymbol(symbol)} removed from ${name}` })
      } else {
        await add.mutateAsync(symbol)
        toast("ok", { title: `${shortSymbol(symbol)} added to ${name}` })
      }
    } catch {
      toast("bad", { title: `Could not update ${name}` })
    }
  }

  return (
    <HeaderButton
      active={Boolean(existing)}
      disabled={!active || add.isPending || remove.isPending}
      label={existing ? `Remove from ${name}` : `Add to ${name}`}
      onClick={() => void toggle()}
      tooltip={existing ? `In ${name} — click to remove` : `Add to ${name}`}
    >
      <HeartIcon aria-hidden="true" className={cn("size-3.5", existing && "fill-current")} />
    </HeaderButton>
  )
}

export interface ChartHeaderProps {
  symbol: string
  name: string | null
  isIndex: boolean
  ltp: number | null
  changePct: number | null
  meta: ScanResult | undefined
  listLabel: string
  index: number
  total: number
  canPrev: boolean
  canNext: boolean
  onPrev: () => void
  onNext: () => void
  sidebarCollapsed: boolean
  onExpandSidebar: () => void
  onAnalyzeVcp: () => void
}

/** Quote bar above the chart: list position, symbol/price, scan context, actions. */
export function ChartHeader({
  symbol,
  name,
  isIndex,
  ltp,
  changePct,
  meta,
  listLabel,
  index,
  total,
  canPrev,
  canNext,
  onPrev,
  onNext,
  sidebarCollapsed,
  onExpandSidebar,
  onAnalyzeVcp,
}: ChartHeaderProps) {
  const { toast } = useToast()
  const generateProposal = useTriggerSingleProposal()
  const off52w = meta && Number.isFinite(meta.pct_from_52w_high) ? Math.abs(meta.pct_from_52w_high * 100) : null
  const proposalPending = generateProposal.isPending && generateProposal.variables === meta?.id

  const triggerProposal = () => {
    if (!meta) return
    generateProposal.mutate(meta.id, {
      onSuccess: (result) => toast("ok", { title: "Proposal queued", text: result.message }),
      onError: (error) =>
        toast("bad", {
          title: "Could not queue proposal",
          text: error instanceof Error ? error.message : undefined,
        }),
    })
  }

  return (
    <div className="qbar !gap-4 !px-3 !py-2">
      {sidebarCollapsed && (
        <button aria-label="Show list" className="ibtn" onClick={onExpandSidebar} title="Show list" type="button">
          <PanelLeftOpenIcon className="size-4" />
        </button>
      )}

      {/* list position */}
      <div className="flex flex-none items-center gap-1">
        <button
          aria-label="Previous symbol"
          className="ibtn"
          disabled={!canPrev}
          onClick={onPrev}
          title="Previous (↑ / K)"
          type="button"
        >
          <ChevronUpIcon className={cn("size-4", !canPrev && "opacity-30")} />
        </button>
        <button
          aria-label="Next symbol"
          className="ibtn"
          disabled={!canNext}
          onClick={onNext}
          title="Next (↓ / J)"
          type="button"
        >
          <ChevronDownIcon className={cn("size-4", !canNext && "opacity-30")} />
        </button>
        <span className="min-w-[64px] font-mono text-2xs text-muted-text" title={`Position in ${listLabel}`}>
          {index >= 0 ? (
            <>
              <b className="text-fg2">{index + 1}</b> / {total}
            </>
          ) : total > 0 ? (
            `not in ${listLabel.toLowerCase()}`
          ) : (
            "—"
          )}
        </span>
      </div>

      {/* symbol + price */}
      <div className="qsym">
        <div className="st">
          <h2 className="!text-lg">{shortSymbol(symbol)}</h2>
          <span className="ex">NSE {isIndex ? "IDX" : "EQ"}</span>
        </div>
        <div className="nm">{name ?? symbol}</div>
      </div>
      <div className="qpx !gap-2">
        <span className="ltp !text-xl">{fmtNum(ltp)}</span>
        <span className={cn("chg", toneCls(changePct))}>{fmtPct(changePct)}</span>
      </div>

      {/* scan context */}
      {meta ? (
        <div className="qmini min-w-0 overflow-hidden">
          <Metric label="RANK">#{meta.rank}</Metric>
          <Metric label="SCORE">
            <span className="flex items-center gap-1.5">
              {meta.technical_score?.toFixed(1) ?? "—"}
              <GradeChip grade={meta.score_grade} />
            </span>
          </Metric>
          <Metric label="RS">{meta.rs_rating}</Metric>
          <Metric label="OFF HIGH" title="Distance below the 52-week high">
            {off52w === null ? "—" : `${off52w.toFixed(1)}%`}
          </Metric>
          <Metric label="SECTOR" title={meta.industry ?? undefined}>
            <span className={cn("capitalize", SECTOR_TONE[meta.sector_tier] ?? "text-fg2")}>{meta.sector_tier}</span>
          </Metric>
          <Metric label="SHORTLIST">{meta.fundamental_selected ? `Top 20 · #${meta.fundamental_selection_rank ?? "—"}` : "No"}</Metric>
        </div>
      ) : (
        <span className="hidden text-xs text-muted-text min-[1100px]:inline">Not in the selected scan run</span>
      )}

      {/* actions */}
      <div className="ml-auto flex flex-none items-center gap-1.5">
        {meta && (
          <>
            <HeaderButton
              label={meta.vcp_vision ? "Review VCP vision" : "Analyze VCP with vision"}
              onClick={onAnalyzeVcp}
              tooltip={meta.vcp_vision ? "Open the VCP vision analysis" : "Run the advisory VCP vision check"}
            >
              <FlaskConicalIcon aria-hidden="true" className="size-3.5" />
              VCP
            </HeaderButton>
            <HeaderButton
              disabled={!meta.fundamental_selected || proposalPending}
              label="Generate proposal"
              onClick={triggerProposal}
              tooltip={
                meta.fundamental_selected
                  ? "Queue proposal generation for this stock"
                  : "Only the fundamental shortlist (Top 20) can generate a proposal"
              }
            >
              {proposalPending ? <Spinner /> : <SparklesIcon aria-hidden="true" className="size-3.5" />}
              Proposal
            </HeaderButton>
          </>
        )}
        <WatchToggle symbol={symbol} />
      </div>
    </div>
  )
}
