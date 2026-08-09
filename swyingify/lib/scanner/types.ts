export type ScannerPreset = "standard"

export type QualificationKey =
  | "stage2"
  | "relativeStrength"
  | "nearHigh"
  | "atrContraction"
  | "bollingerContraction"
  | "volumeDryUp"

export type QualificationStatus = "strong" | "supporting" | "watch"

export interface QualificationComponent {
  key: QualificationKey
  label: string
  shortLabel: string
  score: number
  maxScore: number
  status: QualificationStatus
  summary: string
}

export interface QualificationFingerprint {
  strongCount: number
  totalCount: number
  components: QualificationComponent[]
}

export interface DailyCandle {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  sma50?: number | null
  sma150?: number | null
  sma200?: number | null
}

export interface ScannerResultPreview {
  id: string
  symbol: string
  companyName: string
  sector: string
  preset: ScannerPreset
  rank: number
  asOfDate: string
  close: number
  technicalScore: number
  grade: "A" | "B" | "C" | string
  rsRating: number
  pctFrom52WeekHigh: number
  adtvCrore: number
  dayChangePct: number
  /** Synthetic fallback seed when sparkSeries is empty. */
  sparkSeed?: number
  /** Recent daily closes for the trend sparkline. */
  sparkSeries?: number[]
  atrRatio: number
  volumeDryUpRatio: number
  fingerprint: QualificationFingerprint
  candles: DailyCandle[]
}

export interface ScannerLatestMeta {
  family: string
  code: string
  asOfDate: string | null
  status: string
  completedAt: string | null
  resultCount: number
  scanRunId: string | null
  message?: string | null
}

export interface ScannerQuery {
  preset?: ScannerPreset
  search?: string
  sort?: "score" | "rs" | "nearHigh" | "price"
}
