import type { Time } from "lightweight-charts"

export interface ChartPoint {
  time: Time
  price: number
}

export type DrawingTool =
  | "cursor"
  | "trendline"
  | "rectangle"
  | "risk-reward"
  | "horizontal-line"
  | "price-range"

export interface DrawingStyle {
  color: string
  lineWidth: number
  lineStyle: number // 0: solid, 1: dotted, 2: dashed
  fillColor?: string
}

export type DrawingRecord =
  | {
      id: string
      type: "trendline"
      p1: ChartPoint
      p2: ChartPoint
      style?: Partial<DrawingStyle>
    }
  | {
      id: string
      type: "rectangle"
      p1: ChartPoint
      p2: ChartPoint
      style?: Partial<DrawingStyle>
    }
  | {
      id: string
      type: "risk-reward"
      entry: ChartPoint
      stop: ChartPoint
      target: ChartPoint
      style?: Partial<DrawingStyle>
    }
  | {
      id: string
      type: "horizontal-line"
      price: number
      time: Time
      style?: Partial<DrawingStyle>
    }
  | {
      id: string
      type: "price-range"
      p1: ChartPoint
      p2: ChartPoint
      style?: Partial<DrawingStyle>
    }

export interface ViewPoint {
  x: number | null
  y: number | null
}

export interface HitTestResult {
  drawingId: string
  type: "handle" | "body"
  handleIndex?: number // 0: p1/entry, 1: p2/stop, 2: target
}
