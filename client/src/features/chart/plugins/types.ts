import type { Time } from "lightweight-charts"

export interface ChartPoint {
  time: Time
  price: number
}

export type DrawingTool = "cursor" | "trendline" | "rectangle" | "risk-reward"

export type DrawingRecord =
  | { id: string; type: "trendline"; p1: ChartPoint; p2: ChartPoint }
  | { id: string; type: "rectangle"; p1: ChartPoint; p2: ChartPoint }
  | {
      id: string
      type: "risk-reward"
      entry: ChartPoint
      stop: ChartPoint
      target: ChartPoint
    }

export interface ViewPoint {
  x: number | null
  y: number | null
}
