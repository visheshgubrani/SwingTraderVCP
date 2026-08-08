export function sparklineSeries(close: number, dayChangePct: number, sparkSeed: number, points = 26) {
  const prev = close / (1 + dayChangePct / 100)
  const start = prev - (points - 2) * (close - prev) * 0.34
  const out: number[] = []
  for (let i = 0; i < points - 1; i++) {
    const t = i / (points - 2)
    out.push(start + (prev - start) * t + Math.sin(i * 0.7 + sparkSeed) * close * 0.006)
  }
  out.push(close)
  return out
}

export function sparklinePath(series: number[], width = 96, height = 30, padding = 3) {
  const min = Math.min(...series)
  const max = Math.max(...series)
  const span = max - min || 1
  const step = (width - 2 * padding) / (series.length - 1)
  const pts = series.map((v, i) => {
    const x = padding + i * step
    const y = height - padding - ((v - min) / span) * (height - 2 * padding)
    return `${x},${y}`
  })
  const line = pts.join(" ")
  const firstX = pts[0].split(",")[0]
  const lastX = pts[pts.length - 1].split(",")[0]
  const area = `M${pts[0]} L${pts.slice(1).join(" L")} L${lastX},${height - padding} L${firstX},${height - padding} Z`
  return { line, area }
}
