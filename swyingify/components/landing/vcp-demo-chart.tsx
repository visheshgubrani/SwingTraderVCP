"use client"

import { useMemo } from "react"

import { VCP_CANDLES, VCP_PIVOT, type ScanDemoItem } from "@/lib/landing/demo-data"

function ema20(vals: number[]) {
  const k = 2 / 21
  let e: number | null = null
  const out: number[] = []
  vals.forEach((v) => {
    e = e === null ? v : v * k + e * (1 - k)
    out.push(e)
  })
  return out
}

export function VcpDemoChart({ item }: { item: ScanDemoItem }) {
  const svg = useMemo(() => {
    const s = 1
    const candles =
      item.cut != null && item.cut < VCP_CANDLES.length ? VCP_CANDLES.slice(0, item.cut + 1) : VCP_CANDLES

    const W = 620
    const H = 336
    const padL = 12
    const padR = 12
    const volTop = 292
    const volH = 22
    const plotTop = 16
    const plotH = volTop - 16 - 8

    const highs = candles.map((c) => c[1] * s)
    const lows = candles.map((c) => c[2] * s)
    const maxP = Math.max(...highs) * 1.02
    const minP = Math.min(...lows) * 0.985
    const y = (p: number) => plotTop + ((maxP - p) / (maxP - minP)) * plotH

    const plotW = W - padL - padR
    const slot = plotW / candles.length
    const bodyW = Math.max(4, Math.min(10, slot * 0.4))
    const cx = (i: number) => padL + slot * i + slot / 2

    const maxVol = 1260000
    const trend = ema20(candles.map((c) => c[3] * s))
    const pivY = y(VCP_PIVOT * s)
    const baseX1 = cx(10) - slot / 2
    const baseX2 = cx(candles.length - 1) + slot / 2

    return (
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full"
        role="img"
        aria-label={`Representative VCP base for ${item.sym}`}
      >
        <polyline
          points={trend.map((v, i) => `${cx(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}
          fill="none"
          stroke="rgba(255,255,255,0.6)"
          strokeWidth="1.5"
        />
        <line
          x1={padL}
          y1={pivY}
          x2={W - padR}
          y2={pivY}
          stroke="rgba(255,255,255,0.35)"
          strokeDasharray="4 4"
        />
        <text
          x={W - padR - 4}
          y={pivY - 6}
          textAnchor="end"
          fontFamily="var(--font-landing-mono)"
          fontSize="10"
          letterSpacing="1"
          fill="rgba(255,255,255,0.6)"
        >
          PIVOT {(VCP_PIVOT * s).toFixed(2)}
        </text>
        <rect
          x={baseX1}
          y={plotTop}
          width={baseX2 - baseX1}
          height={plotH}
          fill="rgba(255,255,255,0.035)"
          stroke="rgba(255,255,255,0.16)"
          strokeDasharray="3 3"
        />
        <text
          x={baseX1 + 6}
          y={plotTop + 12}
          fontFamily="var(--font-landing-mono)"
          fontSize="9"
          letterSpacing="1.5"
          fill="rgba(255,255,255,0.45)"
        >
          BASE
        </text>
        {candles.map((c, i) => {
          const o = c[0] * s
          const h = c[1] * s
          const l = c[2] * s
          const cl = c[3] * s
          const v = c[4]
          const x = cx(i)
          const up = cl >= o
          const bodyCol = up ? "rgba(255,255,255,0.85)" : "rgba(255,255,255,0.16)"
          const yO = y(o)
          const yC = y(cl)
          const yH = y(h)
          const yL = y(l)
          const top = Math.min(yO, yC)
          const bh = Math.max(1.5, Math.abs(yC - yO))
          const vh = Math.max(1.5, (v / maxVol) * volH)
          return (
            <g key={i}>
              <line
                x1={x}
                y1={yH}
                x2={x}
                y2={yL}
                stroke="rgba(255,255,255,0.4)"
                strokeWidth="1"
              />
              <rect x={x - bodyW / 2} y={top} width={bodyW} height={bh} fill={bodyCol} />
              <rect
                x={x - bodyW / 2}
                y={volTop + volH - vh}
                width={bodyW}
                height={vh}
                fill={i >= 20 ? "rgba(255,255,255,0.55)" : "rgba(255,255,255,0.25)"}
              />
            </g>
          )
        })}
        <text
          x={cx(candles.length - 1)}
          y={volTop + volH + 14}
          textAnchor="middle"
          fontFamily="var(--font-landing-mono)"
          fontSize="9"
          letterSpacing="1.5"
          fill="rgba(255,255,255,0.45)"
        >
          TODAY
        </text>
      </svg>
    )
  }, [item])

  return <div className="transition-opacity duration-300">{svg}</div>
}
