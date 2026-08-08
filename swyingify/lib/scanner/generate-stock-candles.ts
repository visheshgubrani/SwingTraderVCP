export type GeneratedCandle = {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

function rnd(seed: number) {
  return function random() {
    seed |= 0
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function timeStr(dt: Date) {
  const mm = String(dt.getMonth() + 1).padStart(2, "0")
  const dd = String(dt.getDate()).padStart(2, "0")
  return `${dt.getFullYear()}-${mm}-${dd}`
}

export function generateStockCandles(input: {
  close: number
  dayChangePct: number
  sparkSeed: number
  adtvCrore: number
  pivot: number
  baseLow: number
  prevClose: number
  bars?: number
}): GeneratedCandle[] {
  const { close, sparkSeed, adtvCrore, pivot, baseLow, prevClose } = input
  const N = input.bars ?? 252
  const r = rnd(sparkSeed * 7919 + 17)
  const days: Date[] = []
  const dt = new Date()
  dt.setHours(12, 0, 0, 0)
  while (days.length < N) {
    if (dt.getDay() !== 0 && dt.getDay() !== 6) days.unshift(new Date(dt))
    dt.setDate(dt.getDate() - 1)
  }

  const start = baseLow * 0.72
  const advHigh = pivot * 1.03
  const aEnd = Math.floor(N * 0.58)
  let prevC = start
  const round2 = (x: number) => Math.round(x * 100) / 100
  const out: GeneratedCandle[] = []

  for (let i = 0; i < N; i++) {
    const day = days[i]
    let o: number
    let h: number
    let l: number
    let c: number
    let v: number

    if (i < aEnd) {
      const advProg = Math.min(1, i / aEnd)
      const ease = 1 - (1 - advProg) ** 3
      const base = start + (advHigh * 0.985 - start) * ease
      const spread = advHigh * 0.028 * (1 - advProg * 0.6)
      c = base + (r() - 0.5) * 2 * spread
      o = prevC + (r() - 0.5) * spread * 0.8
      h = Math.max(o, c) + spread * (0.3 + r() * 0.5)
      l = Math.min(o, c) - spread * (0.3 + r() * 0.5)
      v = (0.5 + r() * 0.6) * adtvCrore / 2
    } else if (i < N - 1) {
      const bProg = (i - aEnd) / (N - 1 - aEnd)
      const amp = advHigh * 0.014 * (1 - bProg * 0.8)
      const center = advHigh - (advHigh - prevClose) * bProg
      const w = r() - 0.5
      c = center + w * amp * 2
      o = prevC + (r() - 0.5) * amp * 1.2
      h = Math.max(o, c) + amp * (0.4 + r() * 0.6)
      l = Math.min(o, c) - amp * (0.4 + r() * 0.6)
      v = (0.5 + r() * 0.5) * (1 - bProg * 0.62) * (adtvCrore / 2)
    } else {
      o = pivot * 0.997
      c = close
      h = Math.max(o, c) + close * 0.006
      l = Math.min(o, c) - close * 0.004
      v = (adtvCrore * 2.3) / 2
    }

    out.push({
      time: timeStr(day),
      open: round2(o),
      high: round2(h),
      low: round2(l),
      close: round2(c),
      volume: Math.round(v),
    })
    prevC = c
  }

  return out
}

export const CHART_RANGE_SLICES = { "3m": 63, "6m": 126, "1y": 252 } as const
export type ChartRange = keyof typeof CHART_RANGE_SLICES

export const CHART_RANGE_LABELS: Record<ChartRange, string> = {
  "3m": "3-month",
  "6m": "6-month",
  "1y": "1-year",
}
