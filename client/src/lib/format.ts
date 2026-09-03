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
