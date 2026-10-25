import type { ReactNode } from "react"
import type { LucideIcon } from "lucide-react"

import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

/** Uppercase eyebrow label used for panel titles and stat labels. */
export function SectionLabel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span className={cn("text-2xs font-semibold tracking-[0.1em] text-muted-text uppercase", className)}>
      {children}
    </span>
  )
}

/** Bordered surface with an optional title row. The one panel style for every page. */
export function Panel({
  title,
  description,
  actions,
  children,
  className,
  bodyClassName,
  flush = false,
}: {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
  /** Remove body padding (tables, lists that run edge to edge). */
  flush?: boolean
}) {
  return (
    <section className={cn("flex min-w-0 flex-col rounded-md border border-border bg-surface", className)}>
      {(title || actions) && (
        <header className="flex min-h-10 flex-wrap items-center gap-x-3 gap-y-1 border-b border-border-soft px-4 py-2">
          <div className="min-w-0 flex-1">
            {title && <h3 className="text-xs font-semibold tracking-[0.08em] text-fg2 uppercase">{title}</h3>}
            {description && <p className="mt-0.5 text-xs text-muted-text">{description}</p>}
          </div>
          {actions && <div className="flex flex-none items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cn("min-w-0", !flush && "p-4", bodyClassName)}>{children}</div>
    </section>
  )
}

export type StatTone = "ok" | "warn" | "bad" | "info" | "neutral"

const STAT_TONE: Record<StatTone, string> = {
  ok: "text-ok",
  warn: "text-wa",
  bad: "text-ko",
  info: "text-accent",
  neutral: "text-foreground",
}

const STAT_DOT: Record<StatTone, string> = {
  ok: "bg-ok",
  warn: "bg-wa",
  bad: "bg-ko",
  info: "bg-accent",
  neutral: "bg-muted-text",
}

/** Compact label / value / sub-line tile. */
export function Stat({
  label,
  value,
  sub,
  tone = "neutral",
  dot = false,
  loading = false,
  action,
  className,
}: {
  label: ReactNode
  value: ReactNode
  sub?: ReactNode
  tone?: StatTone
  /** Show a status dot before the value (health tiles). */
  dot?: boolean
  loading?: boolean
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1 rounded-md border border-border bg-surface px-3.5 py-3", className)}>
      <SectionLabel>{label}</SectionLabel>
      {loading ? (
        <Skeleton className="h-5 w-24" />
      ) : (
        <div className={cn("flex min-w-0 items-center gap-2 font-mono text-base font-semibold tabular-nums", STAT_TONE[tone])}>
          {dot && <span aria-hidden="true" className={cn("size-2 flex-none rounded-full", STAT_DOT[tone])} />}
          <span className="truncate">{value}</span>
        </div>
      )}
      {sub && !loading && <div className="truncate text-xs text-muted-text">{sub}</div>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  )
}

/** Centered muted message for empty, error and "pick something" states. */
export function EmptyState({
  icon: Icon,
  title,
  children,
  action,
  tone = "neutral",
  className,
}: {
  icon?: LucideIcon
  title: ReactNode
  children?: ReactNode
  action?: ReactNode
  tone?: "neutral" | "bad"
  className?: string
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-2 px-6 py-10 text-center", className)}>
      {Icon && <Icon aria-hidden="true" className={cn("size-5", tone === "bad" ? "text-ko" : "text-muted-text")} />}
      <p className={cn("text-sm font-medium", tone === "bad" ? "text-ko" : "text-fg2")}>{title}</p>
      {children && <div className="max-w-md text-xs text-muted-text">{children}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}
