import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

export type StatusTone = "wait" | "fill" | "work" | "rej" | "off"

/** Pill chip with a status dot (design .sc-chip). */
export function StatusChip({
  tone,
  children,
  className,
}: {
  tone: StatusTone
  children: ReactNode
  className?: string
}) {
  return (
    <span className={cn("sc-chip", tone, className)}>
      <i aria-hidden="true" />
      {children}
    </span>
  )
}

export const toneOf = (tone: StatusTone) => tone

export function gradeChipCls(grade: string): string {
  const g = grade.toUpperCase()
  return g === "A+" || g === "A" ? "g-A" : g === "B" ? "g-B" : g === "C" ? "g-C" : "g-D"
}

/** Compact grade badge (design .gchip). Unknown grades render muted. */
export function GradeChip({ grade }: { grade: string | null | undefined }) {
  const g = grade?.trim().toUpperCase()
  if (!g) return null
  return <span className={cn("gchip", gradeChipCls(g))}>{g}</span>
}

export interface SegOption<T extends string> {
  value: T
  label: ReactNode
  disabled?: boolean
  dim?: boolean
  title?: string
}

interface SegProps<T extends string> {
  options: SegOption<T>[]
  value: T
  onValueChange: (value: T) => void
  /** Apply buy/sell color treatment to the selected button. */
  side?: "buy" | "sell" | null
  className?: string
  "aria-label"?: string
  size?: "sm" | "md"
}

/** Segmented control (design .seg). */
export function Seg<T extends string>({
  options,
  value,
  onValueChange,
  side = null,
  className,
  size = "md",
  ...rest
}: SegProps<T> & Record<string, unknown>) {
  return (
    <div className={cn("seg", side && "seg-side", className)} role="group" {...rest}>
      {options.map((option) => {
        const selected = option.value === value
        return (
          <button
            aria-pressed={selected}
            className={cn(
              selected && "on",
              option.dim && "dim",
              side && selected && side === "buy" && "on-buy",
              side && selected && side === "sell" && "on-sell",
              size === "sm" && "!h-7 !px-2.5 !text-[10.5px]",
            )}
            disabled={option.disabled}
            key={option.value}
            onClick={() => onValueChange(option.value)}
            title={option.title}
            type="button"
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
