import type {
  IChartApi,
  ISeriesApi,
  ISeriesPrimitive,
  MouseEventParams,
  SeriesType,
  Time,
} from "lightweight-charts"

import { PreviewRectanglePrimitive, RectanglePrimitive } from "./rectangle"
import { RiskRewardPrimitive } from "./risk-reward"
import { TrendLinePrimitive } from "./trend-line"
import type { ChartPoint, DrawingRecord, DrawingTool } from "./types"

const symbolDrawingsCache = new Map<string, DrawingRecord[]>()

let drawingIdCounter = 0

function nextDrawingId(): string {
  drawingIdCounter += 1
  return `drawing-${drawingIdCounter}`
}

function anchorsForTool(tool: DrawingTool): number {
  switch (tool) {
    case "trendline":
    case "rectangle":
      return 2
    case "risk-reward":
      return 3
    default:
      return 0
  }
}

export class ChartDrawingController {
  private chart: IChartApi | null = null
  private series: ISeriesApi<SeriesType> | null = null
  private symbol = ""
  private activeTool: DrawingTool = "cursor"
  private pendingPoints: ChartPoint[] = []
  private previewPrimitive:
    | TrendLinePrimitive
    | PreviewRectanglePrimitive
    | RiskRewardPrimitive
    | null = null
  private attachedPrimitives: ISeriesPrimitive<Time>[] = []
  private records: DrawingRecord[] = []
  private onToolChange: ((tool: DrawingTool) => void) | null = null

  private readonly clickHandler = (param: MouseEventParams<Time>) =>
    this.onClick(param)
  private readonly moveHandler = (param: MouseEventParams<Time>) =>
    this.onMouseMove(param)
  private readonly keyHandler = (event: KeyboardEvent) => this.onKeyDown(event)

  bind(
    chart: IChartApi,
    series: ISeriesApi<SeriesType>,
    symbol: string,
    onToolChange?: (tool: DrawingTool) => void,
  ): void {
    this.unbind()
    this.chart = chart
    this.series = series
    this.symbol = symbol
    this.onToolChange = onToolChange ?? null
    this.records = [...(symbolDrawingsCache.get(symbol) ?? [])]
    this.restoreDrawings()

    chart.subscribeClick(this.clickHandler)
    chart.subscribeCrosshairMove(this.moveHandler)
    window.addEventListener("keydown", this.keyHandler)
  }

  unbind(): void {
    if (this.chart) {
      this.chart.unsubscribeClick(this.clickHandler)
      this.chart.unsubscribeCrosshairMove(this.moveHandler)
    }
    window.removeEventListener("keydown", this.keyHandler)
    this.persistCurrentSymbol()
    this.detachAll()
    this.chart = null
    this.series = null
    this.onToolChange = null
  }

  switchSymbol(symbol: string): void {
    if (!this.chart || !this.series) return
    if (symbol === this.symbol) return

    this.cancelPlacement()
    this.persistCurrentSymbol()
    this.detachAll()

    this.symbol = symbol
    this.records = [...(symbolDrawingsCache.get(symbol) ?? [])]
    this.restoreDrawings()
  }

  setActiveTool(tool: DrawingTool): void {
    if (tool === this.activeTool) {
      if (tool !== "cursor") {
        this.cancelPlacement()
        this.activeTool = "cursor"
        this.onToolChange?.("cursor")
      }
      return
    }

    this.cancelPlacement()
    this.activeTool = tool
  }

  getActiveTool(): DrawingTool {
    return this.activeTool
  }

  clearDrawings(): void {
    this.cancelPlacement()
    this.records = []
    symbolDrawingsCache.delete(this.symbol)
    this.detachAll()
  }

  private persistCurrentSymbol(): void {
    if (!this.symbol) return
    if (this.records.length === 0) {
      symbolDrawingsCache.delete(this.symbol)
      return
    }
    symbolDrawingsCache.set(this.symbol, [...this.records])
  }

  private detachAll(): void {
    this.removePreview()
    if (!this.series) {
      this.attachedPrimitives = []
      return
    }
    for (const primitive of this.attachedPrimitives) {
      try {
        this.series.detachPrimitive(primitive)
      } catch {
        // series may already be disposed
      }
    }
    this.attachedPrimitives = []
  }

  private attachPrimitive(primitive: ISeriesPrimitive<Time>): void {
    if (!this.series) return
    this.series.attachPrimitive(primitive)
    this.attachedPrimitives.push(primitive)
  }

  private restoreDrawings(): void {
    if (!this.chart || !this.series) return
    for (const record of this.records) {
      const primitive = this.createPrimitiveFromRecord(record)
      if (primitive) this.attachPrimitive(primitive)
    }
  }

  private createPrimitiveFromRecord(
    record: DrawingRecord,
  ): ISeriesPrimitive<Time> | null {
    if (!this.chart || !this.series) return null

    switch (record.type) {
      case "trendline":
        return new TrendLinePrimitive(
          this.chart,
          this.series,
          record.p1,
          record.p2,
        )
      case "rectangle":
        return new RectanglePrimitive(
          this.chart,
          this.series,
          record.p1,
          record.p2,
        )
      case "risk-reward":
        return new RiskRewardPrimitive(
          this.chart,
          this.series,
          record.entry,
          record.stop,
          record.target,
        )
      default:
        return null
    }
  }

  private cancelPlacement(): void {
    this.pendingPoints = []
    this.removePreview()
  }

  private removePreview(): void {
    if (this.previewPrimitive && this.series) {
      try {
        this.series.detachPrimitive(this.previewPrimitive)
      } catch {
        // ignore
      }
    }
    this.previewPrimitive = null
  }

  private onKeyDown(event: KeyboardEvent): void {
    if (event.key === "Escape") {
      this.cancelPlacement()
      if (this.activeTool !== "cursor") {
        this.activeTool = "cursor"
        this.onToolChange?.("cursor")
      }
    }
  }

  private onClick(param: MouseEventParams<Time>): void {
    if (this.activeTool === "cursor") return
    if (!param.point || param.time === undefined || !this.series) return

    const price = this.series.coordinateToPrice(param.point.y)
    if (price === null) return

    const point: ChartPoint = { time: param.time, price }
    this.pendingPoints.push(point)

    const required = anchorsForTool(this.activeTool)
    if (this.pendingPoints.length < required) {
      this.ensurePreview()
      return
    }

    this.finalizeDrawing()
  }

  private onMouseMove(param: MouseEventParams<Time>): void {
    if (this.activeTool === "cursor" || this.pendingPoints.length === 0) return
    if (!param.point || param.time === undefined || !this.series || !this.chart)
      return

    const price = this.series.coordinateToPrice(param.point.y)
    if (price === null) return

    const cursor: ChartPoint = { time: param.time, price }
    this.updatePreview(cursor)
  }

  private ensurePreview(): void {
    if (!this.chart || !this.series || this.pendingPoints.length === 0) return

    const anchor = this.pendingPoints[0]!
    this.removePreview()

    if (this.activeTool === "trendline") {
      this.previewPrimitive = new TrendLinePrimitive(
        this.chart,
        this.series,
        anchor,
        anchor,
      )
    } else if (this.activeTool === "rectangle") {
      this.previewPrimitive = new PreviewRectanglePrimitive(
        this.chart,
        this.series,
        anchor,
        anchor,
      )
    } else if (this.activeTool === "risk-reward") {
      const entry = this.pendingPoints[0]!
      const stop = this.pendingPoints[1] ?? entry
      this.previewPrimitive = new RiskRewardPrimitive(
        this.chart,
        this.series,
        entry,
        stop,
        entry,
      )
    }

    if (this.previewPrimitive) {
      this.series.attachPrimitive(this.previewPrimitive)
    }
  }

  private updatePreview(cursor: ChartPoint): void {
    if (!this.previewPrimitive) {
      this.ensurePreview()
      return
    }

    if (this.activeTool === "trendline" && this.previewPrimitive instanceof TrendLinePrimitive) {
      this.previewPrimitive.updateEndPoint(cursor)
    } else if (
      this.activeTool === "rectangle" &&
      this.previewPrimitive instanceof PreviewRectanglePrimitive
    ) {
      this.previewPrimitive.updateEndPoint(cursor)
    } else if (
      this.activeTool === "risk-reward" &&
      this.previewPrimitive instanceof RiskRewardPrimitive
    ) {
      if (this.pendingPoints.length === 1) {
        this.previewPrimitive.updateStop(cursor)
        this.previewPrimitive.updateTarget(cursor)
      } else {
        this.previewPrimitive.updateTarget(cursor)
      }
    }
  }

  private finalizeDrawing(): void {
    if (!this.chart || !this.series) return

    const id = nextDrawingId()
    let record: DrawingRecord | null = null
    let primitive: ISeriesPrimitive<Time> | null = null

    if (this.activeTool === "trendline" && this.pendingPoints.length >= 2) {
      const p1 = this.pendingPoints[0]!
      const p2 = this.pendingPoints[1]!
      record = { id, type: "trendline", p1, p2 }
      primitive = new TrendLinePrimitive(this.chart, this.series, p1, p2)
    } else if (
      this.activeTool === "rectangle" &&
      this.pendingPoints.length >= 2
    ) {
      const p1 = this.pendingPoints[0]!
      const p2 = this.pendingPoints[1]!
      record = { id, type: "rectangle", p1, p2 }
      primitive = new RectanglePrimitive(this.chart, this.series, p1, p2)
    } else if (
      this.activeTool === "risk-reward" &&
      this.pendingPoints.length >= 3
    ) {
      const entry = this.pendingPoints[0]!
      const stop = this.pendingPoints[1]!
      const target = this.pendingPoints[2]!
      record = { id, type: "risk-reward", entry, stop, target }
      primitive = new RiskRewardPrimitive(
        this.chart,
        this.series,
        entry,
        stop,
        target,
      )
    }

    this.removePreview()
    this.pendingPoints = []

    if (record && primitive) {
      this.records.push(record)
      this.attachPrimitive(primitive)
      this.persistCurrentSymbol()
    }

    this.activeTool = "cursor"
    this.onToolChange?.("cursor")
  }
}

export function clearAllChartDrawings(): void {
  symbolDrawingsCache.clear()
}
