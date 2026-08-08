import type { ScannerResultPreview } from "@/lib/scanner/types"

export function formatInr(value: number) {
  return `₹${Math.round(value).toLocaleString("en-IN")}`
}

export function formatAdtv(crore: number) {
  return `${crore.toLocaleString("en-IN")} Cr`
}

export function padRank(n: number) {
  return (n < 10 ? "0" : "") + n
}

export function ChangeCell({ value }: { value: number }) {
  const up = value >= 0
  return (
    <span
      className={`inline-flex items-center justify-end gap-1 font-[family-name:var(--font-landing-mono)] tracking-wide ${
        up ? "text-[var(--landing-fg)]" : "text-[var(--landing-muted)]"
      }`}
    >
      <span className="text-[11px] leading-none">{up ? "↑" : "↓"}</span>
      {up ? "+" : "−"}
      {Math.abs(value).toFixed(1)}%
    </span>
  )
}

export function ScoreCell({ result }: { result: ScannerResultPreview }) {
  return (
    <span className="font-[family-name:var(--font-landing-mono)] text-sm tracking-wide text-[var(--landing-fg)]">
      {result.technicalScore}
      <small className="ml-1 text-[11px] text-[var(--landing-muted)]">{result.grade}</small>
    </span>
  )
}
