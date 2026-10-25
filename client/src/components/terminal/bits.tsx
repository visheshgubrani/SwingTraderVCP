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
