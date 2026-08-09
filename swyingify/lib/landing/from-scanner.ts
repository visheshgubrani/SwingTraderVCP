import type { CheckKey, ScanDemoItem } from "@/lib/landing/demo-data"
import type { ScannerResultPreview } from "@/lib/scanner/types"

const STATUS_TO_TIER: Record<string, 1 | 2 | 3> = {
  strong: 1,
  supporting: 2,
  watch: 3,
}

const KEY_TO_CHECK: Record<string, CheckKey> = {
  stage2: "stage2",
  relativeStrength: "rs",
  nearHigh: "high",
  atrContraction: "atr",
  bollingerContraction: "bands",
  volumeDryUp: "vol",
}

function stageLabel(row: ScannerResultPreview): string {
  if (row.technicalScore >= 90) return "Breakout confirmed"
  if (row.technicalScore >= 85) return "Breakout day"
  if (row.dayChangePct >= 2) return "At the pivot"
  if (row.volumeDryUpRatio <= 0.6) return "Dry-up forming"
  if (row.atrRatio <= 1.05) return "Base tightening"
  return "Holding the pivot"
}

function formatAdtv(adtvCrore: number): string {
  if (!Number.isFinite(adtvCrore) || adtvCrore <= 0) return "—"
  return `${Math.round(adtvCrore)} Cr`
}

export function toLandingScanItem(row: ScannerResultPreview): ScanDemoItem {
  const checks: Record<CheckKey, 1 | 2 | 3> = {
    stage2: 2,
    rs: 2,
    high: 2,
    atr: 2,
    bands: 2,
    vol: 2,
  }
  for (const component of row.fingerprint.components) {
    const key = KEY_TO_CHECK[component.key]
    if (!key) continue
    checks[key] = STATUS_TO_TIER[component.status] ?? 2
  }

  return {
    sym: row.symbol,
    name: row.companyName,
    sector: row.sector,
    score: Math.round(row.technicalScore),
    rs: row.rsRating,
    high: row.pctFrom52WeekHigh,
    adtv: formatAdtv(row.adtvCrore),
    cut: Math.round(row.volumeDryUpRatio * 100),
    stage: stageLabel(row),
    c: checks,
  }
}

export function toHeroPreview(rows: ScannerResultPreview[]) {
  return rows.slice(0, 6).map((row) => ({
    sym: row.symbol,
    stage: stageLabel(row),
    score: Math.round(row.technicalScore),
  }))
}
