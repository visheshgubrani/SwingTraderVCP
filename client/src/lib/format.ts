/** Terminal formatting helpers (en-IN locale, design conventions). */

export function fmtNum(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—"
  return Number(value).toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function fmtPrice(value: number | null | undefined): string {
  const n = fmtNum(value)
  return n === "—" ? n : `₹${n}`
}

export function fmtAmount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—"
  const sign = value < 0 ? "-" : value > 0 ? "+" : ""
  return `${sign}₹${fmtNum(Math.abs(value))}`
}

export function fmtPct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—"
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`
}

/** 1,20,000 → 1.2 L · 5,00,00,000 → 5 cr · 9,500 → 9.5k */
export function compactAmount(value: number): string {
  const x = Math.abs(value)
  if (x >= 1e7) return `${trimZeros(x / 1e7)} cr`
  if (x >= 1e5) return `${trimZeros(x / 1e5)} L`
  if (x >= 1e3) return `${trimZeros(x / 1e3)}k`
  return String(Math.round(x))
}

function trimZeros(n: number): string {
  return n.toFixed(2).replace(/\.?0+$/, "")
}

/** Design tone classes for signed values: up / down / flat. */
export function toneCls(value: number | null | undefined): "up" | "down" | "flat" {
  if (value === null || value === undefined || !Number.isFinite(value)) return "flat"
  return value > 0 ? "up" : value < 0 ? "down" : "flat"
}

/** Compact NSE session clock, e.g. "14:05 IST". */
export function nseNow(): string {
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date())
}

export function nseDate(d = new Date()): string {
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(d)
}

export function isNseOpen(now = new Date()): boolean {
  const parts = new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(now)
  const hour = Number(parts.find((p) => p.type === "hour")?.value ?? 0)
  const min = Number(parts.find((p) => p.type === "minute")?.value ?? 0)
  const dow = now.getDay()
  if (dow === 0 || dow === 6) return false
  const mins = hour * 60 + min
  return mins >= 555 && mins <= 930 // 09:15–15:30 IST
}

/** Coerce an API number-or-decimal-string into a finite number (else null). */
export function toNum(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null
  const n = typeof value === "number" ? value : Number(value)
  return Number.isFinite(n) ? n : null
}

/**
 * Rupee amount with en-IN grouping: ₹5,08,080.00 · -₹2,000.00.
 * `sign` prefixes "+" on positive values (P&L columns).
 */
export function fmtMoney(
  value: number | string | null | undefined,
  { sign = false, decimals = 2 }: { sign?: boolean; decimals?: number } = {},
): string {
  const n = toNum(value)
  if (n === null) return "—"
  const prefix = n < 0 ? "-" : sign && n > 0 ? "+" : ""
  return `${prefix}₹${fmtNum(Math.abs(n), decimals)}`
}

/** R-multiple, e.g. 2.50R · +3.00R with `sign`. */
export function fmtR(value: number | string | null | undefined, { sign = false }: { sign?: boolean } = {}): string {
  const n = toNum(value)
  if (n === null) return "—"
  return `${sign && n > 0 ? "+" : ""}${n.toFixed(2)}R`
}

const IST = "Asia/Kolkata"

function toDate(value: string | Date | null | undefined): Date | null {
  if (!value) return null
  // Bare ISO dates ("2026-10-24") are session dates — pin them to IST midnight.
  const date = typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? new Date(`${value}T00:00:00+05:30`)
    : new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/** 24 Oct 2026 (IST). */
export function fmtDate(value: string | Date | null | undefined): string {
  const date = toDate(value)
  if (!date) return "—"
  return new Intl.DateTimeFormat("en-IN", { timeZone: IST, day: "2-digit", month: "short", year: "numeric" }).format(date)
}

/** 24 Oct, 14:05 (IST); the year is shown only when it is not the current one. */
export function fmtDateTime(value: string | Date | null | undefined): string {
  const date = toDate(value)
  if (!date) return "—"
  const sameYear = date.getFullYear() === new Date().getFullYear()
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: IST,
    day: "2-digit",
    month: "short",
    ...(sameYear ? {} : { year: "numeric" }),
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date)
}

/** 14:05 (IST). */
export function fmtTime(value: string | Date | null | undefined): string {
  const date = toDate(value)
  if (!date) return "—"
  return new Intl.DateTimeFormat("en-IN", { timeZone: IST, hour: "2-digit", minute: "2-digit", hour12: false }).format(date)
}

/** "in 2h 14m" · "3m ago" · "just now". */
export function fmtRelative(value: string | Date | null | undefined, now: Date = new Date()): string {
  const date = toDate(value)
  if (!date) return "—"
  const diffMs = date.getTime() - now.getTime()
  const abs = Math.abs(diffMs)
  if (abs < 45_000) return "just now"
  const minutes = Math.round(abs / 60_000)
  const days = Math.floor(minutes / 1440)
  const hours = Math.floor((minutes % 1440) / 60)
  const mins = minutes % 60
  const span = days > 0 ? `${days}d ${hours}h` : hours > 0 ? `${hours}h ${mins}m` : `${mins}m`
  return diffMs > 0 ? `in ${span}` : `${span} ago`
}
