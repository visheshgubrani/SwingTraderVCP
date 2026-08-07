import type {
  IChartApi,
  ISeriesApi,
  ISeriesPrimitive,
  MouseEventParams,
  SeriesType,
  Time,
} from "lightweight-charts"

import { HorizontalLinePrimitive } from "./horizontal-line"
import {
  pointInBox,
  pointToSegmentDistance,
} from "./math-utils"
import { PriceRangePrimitive } from "./price-range"
import { PreviewRectanglePrimitive, RectanglePrimitive } from "./rectangle"
import { RiskRewardPrimitive } from "./risk-reward"
import { TrendLinePrimitive } from "./trend-line"
import type {
  ChartPoint,
  DrawingRecord,
  DrawingStyle,
  DrawingTool,
  HitTestResult,
} from "./types"

const symbolDrawingsCache = new Map<string, DrawingRecord[]>()
const DRAWING_STORAGE_VERSION = 1
const DRAWING_STORAGE_PREFIX = "swingtrader.chart.drawings.v1."
const CHART_SCROLL_OPTIONS = {
  mouseWheel: true,
  pressedMouseMove: true,
  horzTouchDrag: true,
  vertTouchDrag: true,
} as const
const CHART_SCALE_OPTIONS = {
  axisPressedMouseMove: { time: true, price: true },
  mouseWheel: true,
  pinch: true,
} as const

function storageKey(symbol: string): string {
  return `${DRAWING_STORAGE_PREFIX}${encodeURIComponent(symbol)}`
}

function isChartPoint(value: unknown): value is ChartPoint {
  if (!value || typeof value !== "object") return false
  const point = value as Record<string, unknown>
  const time = point.time
  const validBusinessDay = typeof time === "object" && time !== null &&
    Number.isInteger((time as Record<string, unknown>).year) &&
    Number.isInteger((time as Record<string, unknown>).month) &&
    Number.isInteger((time as Record<string, unknown>).day)
  return typeof point.price === "number" && Number.isFinite(point.price) &&
    ((typeof time === "string" && time.length > 0) ||
      (typeof time === "number" && Number.isFinite(time)) || validBusinessDay)
}

function isDrawingStyle(value: unknown): boolean {
  if (value === undefined) return true
  if (!value || typeof value !== "object") return false
  const style = value as Record<string, unknown>
  return (style.color === undefined || typeof style.color === "string") &&
    (style.fillColor === undefined || typeof style.fillColor === "string") &&
    (style.lineWidth === undefined || (typeof style.lineWidth === "number" && Number.isFinite(style.lineWidth) && style.lineWidth > 0)) &&
    (style.lineStyle === undefined || (typeof style.lineStyle === "number" && Number.isInteger(style.lineStyle)))
}

function isDrawingRecord(value: unknown): value is DrawingRecord {
  if (!value || typeof value !== "object") return false
  const record = value as Record<string, unknown>
  if (typeof record.id !== "string" || typeof record.type !== "string") return false
  if (!isDrawingStyle(record.style)) return false
  if (record.type === "horizontal-line") return typeof record.price === "number" && Number.isFinite(record.price) && isChartPoint({ price: record.price, time: record.time })
  if (record.type === "risk-reward") return isChartPoint(record.entry) && isChartPoint(record.stop) && isChartPoint(record.target)
  if (record.type === "trendline" || record.type === "rectangle" || record.type === "price-range") return isChartPoint(record.p1) && isChartPoint(record.p2)
  return false
}

function loadStoredDrawings(symbol: string): DrawingRecord[] {
  const cached = symbolDrawingsCache.get(symbol)
  if (cached) return cached.map((record) => structuredClone(record))
  try {
    const raw = window.localStorage.getItem(storageKey(symbol))
    if (!raw) return []
    const parsed = JSON.parse(raw) as { version?: number; drawings?: unknown[] }
    if (parsed.version !== DRAWING_STORAGE_VERSION || !Array.isArray(parsed.drawings)) return []
    const records = parsed.drawings.filter(isDrawingRecord)
    symbolDrawingsCache.set(symbol, records)
    return records.map((record) => structuredClone(record))
  } catch {
    return []
  }
}

let drawingIdCounter = 0
function nextDrawingId(): string {
  drawingIdCounter += 1
  return `drawing-${Date.now().toString(36)}-${drawingIdCounter}`
}

function anchorsForTool(tool: DrawingTool): number {
  switch (tool) {
    case "horizontal-line":
      return 1
    case "trendline":
    case "rectangle":
    case "price-range":
      return 2
    case "risk-reward":
      return 3
    default:
      return 0
  }
}

function hitFromHoveredInfo(param: MouseEventParams<Time>): HitTestResult | null {
  if (param.hoveredInfo?.sourceKind !== "series-primitive") return null
  const objectId = param.hoveredInfo.objectId
  if (typeof objectId !== "string" || !objectId.startsWith("drawing:")) return null
  const [, drawingId, type, rawIndex] = objectId.split(":")
  if (!drawingId || (type !== "handle" && type !== "body")) return null
  const handleIndex = type === "handle" ? Number(rawIndex) : undefined
  return { drawingId, type, handleIndex: Number.isFinite(handleIndex) ? handleIndex : undefined }
}

type CustomPrimitive =
  | TrendLinePrimitive
  | RectanglePrimitive
  | RiskRewardPrimitive
  | HorizontalLinePrimitive
  | PriceRangePrimitive

export class ChartDrawingController {
  private chart: IChartApi | null = null
  private series: ISeriesApi<SeriesType> | null = null
  private container: HTMLElement | null = null
  private symbol = ""
  private activeTool: DrawingTool = "cursor"
  private pendingPoints: ChartPoint[] = []

  private previewPrimitive:
    | TrendLinePrimitive
    | PreviewRectanglePrimitive
    | RiskRewardPrimitive
    | HorizontalLinePrimitive
    | PriceRangePrimitive
    | null = null

  private attachedPrimitivesMap = new Map<string, CustomPrimitive>()
  private records: DrawingRecord[] = []

  private selectedDrawingId: string | null = null
  private hoveredDrawingId: string | null = null
  private hoverFrame: number | null = null
  private pendingHoverHit: HitTestResult | null = null

  // Dragging state
  private isDragging = false
  private dragType: "handle" | "body" | null = null
  private dragDrawingId: string | null = null
  private dragHandleIndex: number | null = null
  private dragStartMousePos: { x: number; y: number } | null = null
  private dragStartPoints: ChartPoint[] = []
  private dragFrame: number | null = null
  private pendingDragPoint: { x: number; y: number } | null = null

  // Callbacks
  private onToolChange: ((tool: DrawingTool) => void) | null = null
  private onSelectionChange: ((record: DrawingRecord | null) => void) | null = null

  private readonly clickHandler = (param: MouseEventParams<Time>) =>
    this.onClick(param)
  private readonly moveHandler = (param: MouseEventParams<Time>) =>
    this.onMouseMove(param)
  private readonly keyHandler = (event: KeyboardEvent) => this.onKeyDown(event)

  private readonly containerPointerDownHandler = (event: PointerEvent) =>
    this.onContainerPointerDown(event)
  private readonly containerPointerMoveHandler = (event: PointerEvent) =>
    this.onContainerPointerMove(event)
  private readonly containerPointerUpHandler = (event: PointerEvent) =>
    this.onContainerPointerUp(event)

  bind(
    chart: IChartApi,
    series: ISeriesApi<SeriesType>,
    container: HTMLElement,
    symbol: string,
    onToolChange?: (tool: DrawingTool) => void,
    onSelectionChange?: (record: DrawingRecord | null) => void,
  ): void {
    this.unbind()
    this.chart = chart
    this.series = series
    this.container = container
    this.symbol = symbol
    this.onToolChange = onToolChange ?? null
    this.onSelectionChange = onSelectionChange ?? null

    this.records = loadStoredDrawings(symbol)
    this.restoreDrawings()

    chart.subscribeClick(this.clickHandler)
    chart.subscribeCrosshairMove(this.moveHandler)
    window.addEventListener("keydown", this.keyHandler)

    container.addEventListener("pointerdown", this.containerPointerDownHandler)
    container.addEventListener("pointermove", this.containerPointerMoveHandler)
    container.addEventListener("pointerup", this.containerPointerUpHandler)
    container.addEventListener("pointercancel", this.containerPointerUpHandler)
  }

  unbind(): void {
    if (this.chart) {
      this.chart.unsubscribeClick(this.clickHandler)
      this.chart.unsubscribeCrosshairMove(this.moveHandler)
    }
    window.removeEventListener("keydown", this.keyHandler)

    if (this.container) {
      this.container.removeEventListener("pointerdown", this.containerPointerDownHandler)
      this.container.removeEventListener("pointermove", this.containerPointerMoveHandler)
      this.container.removeEventListener("pointerup", this.containerPointerUpHandler)
      this.container.removeEventListener("pointercancel", this.containerPointerUpHandler)
    }
    if (this.dragFrame !== null) cancelAnimationFrame(this.dragFrame)
    if (this.hoverFrame !== null) cancelAnimationFrame(this.hoverFrame)
    this.dragFrame = null
    this.hoverFrame = null
    this.pendingDragPoint = null
    this.pendingHoverHit = null

    this.persistCurrentSymbol()
    this.detachAll()
    this.chart = null
    this.series = null
    this.container = null
    this.onToolChange = null
    this.onSelectionChange = null
  }

  switchSymbol(symbol: string): void {
    if (!this.chart || !this.series) return
    if (symbol === this.symbol) return

    this.cancelPlacement()
    this.deselect()
    this.persistCurrentSymbol()
    this.detachAll()

    this.symbol = symbol
    this.records = loadStoredDrawings(symbol)
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
    this.deselect()
    this.activeTool = tool
  }

  getActiveTool(): DrawingTool {
    return this.activeTool
  }

  getSelectedRecord(): DrawingRecord | null {
    if (!this.selectedDrawingId) return null
    return this.records.find((r) => r.id === this.selectedDrawingId) ?? null
  }

  updateSelectedStyle(styleUpdate: Partial<DrawingStyle>): void {
    if (!this.selectedDrawingId) return
    const recordIndex = this.records.findIndex(
      (r) => r.id === this.selectedDrawingId,
    )
    if (recordIndex === -1) return

    const record = { ...this.records[recordIndex]!, style: { ...this.records[recordIndex]!.style, ...styleUpdate } } as DrawingRecord
    this.records = this.records.map((item, index) => index === recordIndex ? record : item)

    const primitive = this.attachedPrimitivesMap.get(this.selectedDrawingId)
    if (primitive) {
      primitive.options = { ...primitive.options, ...styleUpdate }
      primitive.updateAllViews()
    }

    this.persistCurrentSymbol()
    this.onSelectionChange?.(record)
  }

  deleteSelectedDrawing(): void {
    if (!this.selectedDrawingId) return
    this.deleteDrawing(this.selectedDrawingId)
  }

  deleteDrawing(id: string): void {
    const primitive = this.attachedPrimitivesMap.get(id)
    if (primitive && this.series) {
      try {
        this.series.detachPrimitive(primitive as unknown as ISeriesPrimitive<Time>)
      } catch {
        // ignore
      }
    }
    this.attachedPrimitivesMap.delete(id)
    this.records = this.records.filter((r) => r.id !== id)

    if (this.selectedDrawingId === id) {
      this.deselect()
    }
    this.persistCurrentSymbol()
  }

  clearDrawings(): void {
    this.cancelPlacement()
    this.deselect()
    this.records = []
    symbolDrawingsCache.delete(this.symbol)
    window.localStorage.removeItem(storageKey(this.symbol))
    this.detachAll()
  }

  private deselect(): void {
    if (this.selectedDrawingId) {
      const primitive = this.attachedPrimitivesMap.get(this.selectedDrawingId)
      if (primitive) {
        primitive.setSelected(false)
      }
      this.selectedDrawingId = null
      this.onSelectionChange?.(null)
    }
  }

  private selectDrawing(id: string): void {
    if (this.selectedDrawingId === id) return
    this.deselect()

    this.selectedDrawingId = id
    const primitive = this.attachedPrimitivesMap.get(id)
    if (primitive) {
      primitive.setSelected(true)
    }
    const record = this.records.find((r) => r.id === id) ?? null
    this.onSelectionChange?.(record)
  }

  private persistCurrentSymbol(): void {
    if (!this.symbol) return
    if (this.records.length === 0) {
      symbolDrawingsCache.delete(this.symbol)
      window.localStorage.removeItem(storageKey(this.symbol))
      return
    }
    const records = this.records.map((record) => structuredClone(record))
    symbolDrawingsCache.set(this.symbol, records)
    try {
      window.localStorage.setItem(storageKey(this.symbol), JSON.stringify({ version: DRAWING_STORAGE_VERSION, drawings: records }))
    } catch {
      // Storage can be unavailable in privacy mode; drawings still work for this session.
    }
  }

  private detachAll(): void {
    this.removePreview()
    if (!this.series) {
      this.attachedPrimitivesMap.clear()
      return
    }
    for (const [, primitive] of this.attachedPrimitivesMap) {
      try {
        this.series.detachPrimitive(primitive as unknown as ISeriesPrimitive<Time>)
      } catch {
        // ignore
      }
    }
    this.attachedPrimitivesMap.clear()
  }

  private attachPrimitive(primitive: CustomPrimitive): void {
    if (!this.series) return
    this.series.attachPrimitive(primitive as unknown as ISeriesPrimitive<Time>)
    this.attachedPrimitivesMap.set(primitive.id, primitive)
  }

  private restoreDrawings(): void {
    if (!this.chart || !this.series) return
    for (const record of this.records) {
      const primitive = this.createPrimitiveFromRecord(record)
      if (primitive) this.attachPrimitive(primitive)
    }
  }

  private createPrimitiveFromRecord(record: DrawingRecord): CustomPrimitive | null {
    if (!this.chart || !this.series) return null

    switch (record.type) {
      case "trendline":
        return new TrendLinePrimitive(
          record.id,
          this.chart,
          this.series,
          record.p1,
          record.p2,
          record.style,
        )
      case "rectangle":
        return new RectanglePrimitive(
          record.id,
          this.chart,
          this.series,
          record.p1,
          record.p2,
          record.style,
        )
      case "risk-reward":
        return new RiskRewardPrimitive(
          record.id,
          this.chart,
          this.series,
          record.entry,
          record.stop,
          record.target,
          record.style,
        )
      case "horizontal-line":
        return new HorizontalLinePrimitive(
          record.id,
          this.chart,
          this.series,
          record.price,
          record.time,
          record.style,
        )
      case "price-range":
        return new PriceRangePrimitive(
          record.id,
          this.chart,
          this.series,
          record.p1,
          record.p2,
          record.style,
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
        this.series.detachPrimitive(
          this.previewPrimitive as unknown as ISeriesPrimitive<Time>,
        )
      } catch {
        // ignore
      }
    }
    this.previewPrimitive = null
  }

  private onKeyDown(event: KeyboardEvent): void {
    if (event.key === "Escape") {
      this.cancelPlacement()
      this.deselect()
      if (this.activeTool !== "cursor") {
        this.activeTool = "cursor"
        this.onToolChange?.("cursor")
      }
    } else if (
      (event.key === "Delete" || event.key === "Backspace") &&
      this.selectedDrawingId &&
      !(
        document.activeElement instanceof HTMLInputElement ||
        document.activeElement instanceof HTMLTextAreaElement
      )
    ) {
      this.deleteSelectedDrawing()
    }
  }

  private resolveTimeFromParam(param: MouseEventParams<Time>): Time | null {
    if (param.time !== undefined) return param.time
    if (!param.point || !this.chart) return null
    const timeScale = this.chart.timeScale()
    const logical = timeScale.coordinateToLogical(param.point.x)
    if (logical === null) return null
    const coord = timeScale.logicalToCoordinate(logical)
    if (coord === null) return null
    return timeScale.coordinateToTime(coord) ?? null
  }

  private onClick(param: MouseEventParams<Time>): void {
    if (this.activeTool === "cursor") return
    if (!param.point || !this.series || !this.chart) return

    const price = this.series.coordinateToPrice(param.point.y)
    if (price === null) return

    const time = this.resolveTimeFromParam(param)
    if (!time) return

    const point: ChartPoint = { time, price }

    // Single-click placement tools (e.g. Horizontal Line)
    if (this.activeTool === "horizontal-line") {
      this.finalizeHorizontalLine(point)
      return
    }

    this.pendingPoints.push(point)
    const required = anchorsForTool(this.activeTool)

    if (this.pendingPoints.length < required) {
      this.ensurePreview()
      return
    }

    this.finalizeDrawing()
  }

  private onMouseMove(param: MouseEventParams<Time>): void {
    if (!this.series || !this.chart) return

    if (this.activeTool !== "cursor") {
      if (this.pendingPoints.length === 0) return
      if (!param.point) return
      const price = this.series.coordinateToPrice(param.point.y)
      if (price === null) return
      const time = this.resolveTimeFromParam(param)
      if (!time) return
      const cursor: ChartPoint = { time, price }
      this.updatePreview(cursor)
      return
    }

    // In Cursor Mode: Hover Hit Testing
    if (this.isDragging) return
    if (!param.point) {
      this.updateHoverState(null)
      return
    }
    this.pendingHoverHit = hitFromHoveredInfo(param)
    if (this.hoverFrame !== null) return
    this.hoverFrame = requestAnimationFrame(() => {
      this.hoverFrame = null
      const hit = this.pendingHoverHit
      this.pendingHoverHit = null
      this.updateHoverState(hit)
    })
  }

  private updateHoverState(hit: HitTestResult | null): void {
    if (!hit) {
      if (this.hoveredDrawingId) {
        const prev = this.attachedPrimitivesMap.get(this.hoveredDrawingId)
        if (prev) prev.setHovered(false)
        this.hoveredDrawingId = null
      }
      if (this.container) this.container.style.cursor = "default"
      return
    }

    if (this.hoveredDrawingId !== hit.drawingId) {
      if (this.hoveredDrawingId) {
        const prev = this.attachedPrimitivesMap.get(this.hoveredDrawingId)
        if (prev) prev.setHovered(false)
      }
      this.hoveredDrawingId = hit.drawingId
    }

    const current = this.attachedPrimitivesMap.get(hit.drawingId)
    if (current) {
      current.setHovered(true, hit.handleIndex ?? null)
    }

    if (this.container) {
      this.container.style.cursor = hit.type === "handle" ? "crosshair" : "grab"
    }
  }

  // --- HIT TESTING ENGINE ---
  private hitTest(mouseX: number, mouseY: number): HitTestResult | null {
    if (!this.chart || !this.series) return null
    const timeScale = this.chart.timeScale()

    // 1. Test Handles First (Selected drawing gets handle priority)
    for (const [id, primitive] of this.attachedPrimitivesMap) {
      if (!primitive.selected) continue
      const handles = this.getPrimitiveHandleCoordinates(primitive, timeScale)
      for (const h of handles) {
        if (h.x === null || h.y === null) continue
        const dist = Math.hypot(mouseX - h.x, mouseY - h.y)
        if (dist <= 8) {
          return { drawingId: id, type: "handle", handleIndex: h.index }
        }
      }
    }

    // 2. Test Handles of Unselected Drawings
    for (const [id, primitive] of this.attachedPrimitivesMap) {
      if (primitive.selected) continue
      const handles = this.getPrimitiveHandleCoordinates(primitive, timeScale)
      for (const h of handles) {
        if (h.x === null || h.y === null) continue
        const dist = Math.hypot(mouseX - h.x, mouseY - h.y)
        if (dist <= 8) {
          return { drawingId: id, type: "handle", handleIndex: h.index }
        }
      }
    }

    // 3. Test Body of Drawings
    for (const [id, primitive] of this.attachedPrimitivesMap) {
      const isHit = this.isPointOverPrimitiveBody(primitive, mouseX, mouseY, timeScale)
      if (isHit) {
        return { drawingId: id, type: "body" }
      }
    }

    return null
  }

  private getPrimitiveHandleCoordinates(
    primitive: CustomPrimitive,
    timeScale: import("lightweight-charts").ITimeScaleApi<Time>,
  ): { x: number | null; y: number | null; index: number }[] {
    if (!this.series) return []
    const s = this.series

    if (primitive instanceof TrendLinePrimitive || primitive instanceof PriceRangePrimitive) {
      return [
        {
          x: timeScale.timeToCoordinate(primitive.p1.time),
          y: s.priceToCoordinate(primitive.p1.price),
          index: 0,
        },
        {
          x: timeScale.timeToCoordinate(primitive.p2.time),
          y: s.priceToCoordinate(primitive.p2.price),
          index: 1,
        },
      ]
    }

    if (primitive instanceof RectanglePrimitive) {
      const x1 = timeScale.timeToCoordinate(primitive.p1.time)
      const y1 = s.priceToCoordinate(primitive.p1.price)
      const x2 = timeScale.timeToCoordinate(primitive.p2.time)
      const y2 = s.priceToCoordinate(primitive.p2.price)
      return [
        { x: x1, y: y1, index: 0 },
        { x: x2, y: y2, index: 1 },
        { x: x1, y: y2, index: 2 },
        { x: x2, y: y1, index: 3 },
      ]
    }

    if (primitive instanceof RiskRewardPrimitive) {
      const xValues = [primitive.entry, primitive.stop, primitive.target]
        .map((point) => timeScale.timeToCoordinate(point.time))
        .filter((value): value is NonNullable<typeof value> => value !== null)
      if (xValues.length === 0) return []
      const left = Math.min(...xValues)
      const right = Math.max(...xValues)
      const xCandidate = left + Math.max(right - left, 40) / 2
      return [
        { x: xCandidate, y: s.priceToCoordinate(primitive.entry.price), index: 0 },
        { x: xCandidate, y: s.priceToCoordinate(primitive.stop.price), index: 1 },
        { x: xCandidate, y: s.priceToCoordinate(primitive.target.price), index: 2 },
      ]
    }

    if (primitive instanceof HorizontalLinePrimitive) {
      const width = this.container?.clientWidth ?? 800
      return [
        { x: width / 2, y: s.priceToCoordinate(primitive.price), index: 0 },
      ]
    }

    return []
  }

  private isPointOverPrimitiveBody(
    primitive: CustomPrimitive,
    mouseX: number,
    mouseY: number,
    timeScale: import("lightweight-charts").ITimeScaleApi<Time>,
  ): boolean {
    if (!this.series) return false
    const s = this.series

    if (primitive instanceof TrendLinePrimitive) {
      const x1 = timeScale.timeToCoordinate(primitive.p1.time)
      const y1 = s.priceToCoordinate(primitive.p1.price)
      const x2 = timeScale.timeToCoordinate(primitive.p2.time)
      const y2 = s.priceToCoordinate(primitive.p2.price)
      if (x1 === null || y1 === null || x2 === null || y2 === null) return false
      return pointToSegmentDistance(mouseX, mouseY, x1, y1, x2, y2) <= 7
    }

    if (primitive instanceof RectanglePrimitive) {
      const x1 = timeScale.timeToCoordinate(primitive.p1.time)
      const y1 = s.priceToCoordinate(primitive.p1.price)
      const x2 = timeScale.timeToCoordinate(primitive.p2.time)
      const y2 = s.priceToCoordinate(primitive.p2.price)
      if (x1 === null || y1 === null || x2 === null || y2 === null) return false
      return pointInBox(mouseX, mouseY, x1, y1, x2, y2, 2)
    }

    if (primitive instanceof RiskRewardPrimitive) {
      const xValues = [primitive.entry, primitive.stop, primitive.target]
        .map((point) => timeScale.timeToCoordinate(point.time))
        .filter((value): value is NonNullable<typeof value> => value !== null)
      const entryY = s.priceToCoordinate(primitive.entry.price)
      const stopY = s.priceToCoordinate(primitive.stop.price)
      const targetY = s.priceToCoordinate(primitive.target.price)
      if (xValues.length === 0 || entryY === null || stopY === null || targetY === null)
        return false

      const minY = Math.min(entryY, stopY, targetY)
      const maxY = Math.max(entryY, stopY, targetY)
      const left = Math.min(...xValues)
      const width = Math.max(Math.max(...xValues) - left, 40)
      return pointInBox(mouseX, mouseY, left, minY, left + width, maxY, 2)
    }

    if (primitive instanceof HorizontalLinePrimitive) {
      const lineY = s.priceToCoordinate(primitive.price)
      if (lineY === null) return false
      return Math.abs(mouseY - lineY) <= 6
    }

    if (primitive instanceof PriceRangePrimitive) {
      const x1 = timeScale.timeToCoordinate(primitive.p1.time)
      const y1 = s.priceToCoordinate(primitive.p1.price)
      const x2 = timeScale.timeToCoordinate(primitive.p2.time)
      const y2 = s.priceToCoordinate(primitive.p2.price)
      if (x1 === null || y1 === null || x2 === null || y2 === null) return false
      return pointInBox(mouseX, mouseY, x1, y1, x2, y2, 2)
    }

    return false
  }

  // --- POINTER-CAPTURED DRAGGING ---
  private onContainerPointerDown(event: PointerEvent): void {
    if (this.activeTool !== "cursor" || !this.container) return

    const rect = this.container.getBoundingClientRect()
    const mouseX = event.clientX - rect.left
    const mouseY = event.clientY - rect.top

    const hit = this.hitTest(mouseX, mouseY)

    if (!hit) {
      this.deselect()
      return
    }

    this.selectDrawing(hit.drawingId)

    // Lock chart scrolling during handle/body drag
    if (this.chart) {
      this.chart.applyOptions({ handleScroll: false, handleScale: false })
    }

    this.isDragging = true
    this.container.setPointerCapture(event.pointerId)
    this.dragType = hit.type
    this.dragDrawingId = hit.drawingId
    this.dragHandleIndex = hit.handleIndex ?? null
    this.dragStartMousePos = { x: mouseX, y: mouseY }

    const primitive = this.attachedPrimitivesMap.get(hit.drawingId)
    if (primitive) {
      if (primitive instanceof TrendLinePrimitive || primitive instanceof PriceRangePrimitive) {
        this.dragStartPoints = [{ ...primitive.p1 }, { ...primitive.p2 }]
      } else if (primitive instanceof RectanglePrimitive) {
        this.dragStartPoints = [{ ...primitive.p1 }, { ...primitive.p2 }]
      } else if (primitive instanceof RiskRewardPrimitive) {
        this.dragStartPoints = [
          { ...primitive.entry },
          { ...primitive.stop },
          { ...primitive.target },
        ]
      } else if (primitive instanceof HorizontalLinePrimitive) {
        this.dragStartPoints = [{ time: primitive.time, price: primitive.price }]
      }
    }
  }

  private onContainerPointerMove(event: PointerEvent): void {
    if (
      !this.isDragging ||
      !this.dragDrawingId ||
      !this.container ||
      !this.chart ||
      !this.series
    )
      return

    const rect = this.container.getBoundingClientRect()
    const mouseX = event.clientX - rect.left
    const mouseY = event.clientY - rect.top

    this.pendingDragPoint = { x: mouseX, y: mouseY }
    if (this.dragFrame !== null) return
    this.dragFrame = requestAnimationFrame(() => {
      this.dragFrame = null
      const point = this.pendingDragPoint
      this.pendingDragPoint = null
      if (point) this.applyDrag(point.x, point.y)
    })
  }

  private applyDrag(mouseX: number, mouseY: number): void {
    if (!this.dragDrawingId || !this.chart || !this.series) return
    const primitive = this.attachedPrimitivesMap.get(this.dragDrawingId)
    if (!primitive) return

    const timeScale = this.chart.timeScale()
    const currentPrice = this.series.coordinateToPrice(mouseY)
    const currentLogical = timeScale.coordinateToLogical(mouseX)
    if (currentPrice === null || currentLogical === null) return

    const currentTime = paramTimeToTime(timeScale, currentLogical)
    if (!currentTime) return

    if (this.dragType === "handle") {
      this.updatePrimitiveHandleDrag(
        primitive,
        this.dragHandleIndex ?? 0,
        { time: currentTime, price: currentPrice },
      )
    } else if (this.dragType === "body" && this.dragStartMousePos) {
      this.updatePrimitiveBodyDrag(
        primitive,
        mouseX - this.dragStartMousePos.x,
        mouseY - this.dragStartMousePos.y,
        timeScale,
      )
    }
  }

  private updatePrimitiveHandleDrag(
    primitive: CustomPrimitive,
    handleIndex: number,
    point: ChartPoint,
  ): void {
    if (primitive instanceof TrendLinePrimitive || primitive instanceof PriceRangePrimitive) {
      if (handleIndex === 0) primitive.p1 = point
      else primitive.p2 = point
      primitive.updateAllViews()
    } else if (primitive instanceof RectanglePrimitive) {
      if (handleIndex === 0) primitive.p1 = point
      else if (handleIndex === 1) primitive.p2 = point
      else if (handleIndex === 2) {
        primitive.p1 = { ...primitive.p1, time: point.time }
        primitive.p2 = { ...primitive.p2, price: point.price }
      } else if (handleIndex === 3) {
        primitive.p2 = { ...primitive.p2, time: point.time }
        primitive.p1 = { ...primitive.p1, price: point.price }
      }
      primitive.updateAllViews()
    } else if (primitive instanceof RiskRewardPrimitive) {
      if (handleIndex === 0) primitive.updateEntry({ ...primitive.entry, price: point.price })
      else if (handleIndex === 1) primitive.updateStop({ ...primitive.stop, price: point.price })
      else if (handleIndex === 2) primitive.updateTarget({ ...primitive.target, price: point.price })
    } else if (primitive instanceof HorizontalLinePrimitive) {
      primitive.updatePrice(point.price)
    }
  }

  private updatePrimitiveBodyDrag(
    primitive: CustomPrimitive,
    deltaPixelX: number,
    deltaPixelY: number,
    timeScale: import("lightweight-charts").ITimeScaleApi<Time>,
  ): void {
    if (!this.series || this.dragStartPoints.length === 0) return
    const s = this.series

    if (primitive instanceof TrendLinePrimitive || primitive instanceof PriceRangePrimitive) {
      const p1Start = this.dragStartPoints[0]!
      const p2Start = this.dragStartPoints[1]!

      const x1 = timeScale.timeToCoordinate(p1Start.time)
      const y1 = s.priceToCoordinate(p1Start.price)
      const x2 = timeScale.timeToCoordinate(p2Start.time)
      const y2 = s.priceToCoordinate(p2Start.price)
      if (x1 === null || y1 === null || x2 === null || y2 === null) return

      const newY1 = y1 + deltaPixelY
      const newY2 = y2 + deltaPixelY
      const newPrice1 = s.coordinateToPrice(newY1)
      const newPrice2 = s.coordinateToPrice(newY2)

      const newLog1 = timeScale.coordinateToLogical(x1 + deltaPixelX)
      const newLog2 = timeScale.coordinateToLogical(x2 + deltaPixelX)
      if (newPrice1 === null || newPrice2 === null || newLog1 === null || newLog2 === null) return

      const newTime1 = paramTimeToTime(timeScale, newLog1)
      const newTime2 = paramTimeToTime(timeScale, newLog2)
      if (newTime1 && newTime2) {
        primitive.p1 = { time: newTime1, price: newPrice1 }
        primitive.p2 = { time: newTime2, price: newPrice2 }
        primitive.updateAllViews()
      }
    } else if (primitive instanceof RectanglePrimitive) {
      const p1Start = this.dragStartPoints[0]!
      const p2Start = this.dragStartPoints[1]!

      const x1 = timeScale.timeToCoordinate(p1Start.time)
      const y1 = s.priceToCoordinate(p1Start.price)
      const x2 = timeScale.timeToCoordinate(p2Start.time)
      const y2 = s.priceToCoordinate(p2Start.price)
      if (x1 === null || y1 === null || x2 === null || y2 === null) return

      const newPrice1 = s.coordinateToPrice(y1 + deltaPixelY)
      const newPrice2 = s.coordinateToPrice(y2 + deltaPixelY)
      const newLog1 = timeScale.coordinateToLogical(x1 + deltaPixelX)
      const newLog2 = timeScale.coordinateToLogical(x2 + deltaPixelX)

      if (newPrice1 !== null && newPrice2 !== null && newLog1 !== null && newLog2 !== null) {
        const newTime1 = paramTimeToTime(timeScale, newLog1)
        const newTime2 = paramTimeToTime(timeScale, newLog2)
        if (newTime1 && newTime2) {
          primitive.p1 = { time: newTime1, price: newPrice1 }
          primitive.p2 = { time: newTime2, price: newPrice2 }
          primitive.updateAllViews()
        }
      }
    } else if (primitive instanceof RiskRewardPrimitive) {
      const entryStart = this.dragStartPoints[0]!
      const stopStart = this.dragStartPoints[1]!
      const targetStart = this.dragStartPoints[2]!
      const translated = [entryStart, stopStart, targetStart].map((start) => {
        const startX = timeScale.timeToCoordinate(start.time)
        const startY = s.priceToCoordinate(start.price)
        if (startX === null || startY === null) return null
        const logical = timeScale.coordinateToLogical(startX + deltaPixelX)
        const price = s.coordinateToPrice(startY + deltaPixelY)
        if (logical === null || price === null) return null
        const time = paramTimeToTime(timeScale, logical)
        return time ? { time, price } : null
      })
      const [entry, stop, target] = translated
      if (entry && stop && target) {
        primitive.entry = { time: entry.time, price: Number(entry.price) }
        primitive.stop = { time: stop.time, price: Number(stop.price) }
        primitive.target = { time: target.time, price: Number(target.price) }
        primitive.updateAllViews()
      }
    } else if (primitive instanceof HorizontalLinePrimitive) {
      const startPrice = this.dragStartPoints[0]!.price
      const y = s.priceToCoordinate(startPrice)
      if (y === null) return
      const newPrice = s.coordinateToPrice(y + deltaPixelY)
      if (newPrice !== null) {
        primitive.updatePrice(newPrice)
      }
    }
  }

  private onContainerPointerUp(event: PointerEvent): void {
    if (!this.isDragging) return

    if (this.container?.hasPointerCapture(event.pointerId)) this.container.releasePointerCapture(event.pointerId)
    if (this.dragFrame !== null) cancelAnimationFrame(this.dragFrame)
    this.dragFrame = null
    if (this.pendingDragPoint) {
      this.applyDrag(this.pendingDragPoint.x, this.pendingDragPoint.y)
      this.pendingDragPoint = null
    }

    this.isDragging = false
    this.dragType = null
    this.dragDrawingId = null
    this.dragHandleIndex = null
    this.dragStartMousePos = null
    this.dragStartPoints = []

    // Re-enable chart scrolling
    if (this.chart) {
      this.chart.applyOptions({
        handleScroll: CHART_SCROLL_OPTIONS,
        handleScale: CHART_SCALE_OPTIONS,
      })
    }

    // Sync records to updated primitives
    this.syncRecordsFromPrimitives()
    this.persistCurrentSymbol()

    if (this.selectedDrawingId) {
      const record = this.records.find((r) => r.id === this.selectedDrawingId) ?? null
      this.onSelectionChange?.(record)
    }
  }

  private syncRecordsFromPrimitives(): void {
    for (const [id, primitive] of this.attachedPrimitivesMap) {
      const idx = this.records.findIndex((r) => r.id === id)
      if (idx === -1) continue

      if (primitive instanceof TrendLinePrimitive || primitive instanceof PriceRangePrimitive) {
        this.records[idx] = {
          ...this.records[idx]!,
          p1: { ...primitive.p1 },
          p2: { ...primitive.p2 },
        } as DrawingRecord
      } else if (primitive instanceof RectanglePrimitive) {
        this.records[idx] = {
          ...this.records[idx]!,
          p1: { ...primitive.p1 },
          p2: { ...primitive.p2 },
        } as DrawingRecord
      } else if (primitive instanceof RiskRewardPrimitive) {
        this.records[idx] = {
          ...this.records[idx]!,
          entry: { ...primitive.entry },
          stop: { ...primitive.stop },
          target: { ...primitive.target },
        } as DrawingRecord
      } else if (primitive instanceof HorizontalLinePrimitive) {
        this.records[idx] = {
          ...this.records[idx]!,
          price: primitive.price,
          time: primitive.time,
        } as DrawingRecord
      }
    }
  }

  private ensurePreview(): void {
    if (!this.chart || !this.series || this.pendingPoints.length === 0) return

    const anchor = this.pendingPoints[0]!
    this.removePreview()

    if (this.activeTool === "trendline") {
      this.previewPrimitive = new TrendLinePrimitive(
        "preview",
        this.chart,
        this.series,
        anchor,
        anchor,
      )
    } else if (this.activeTool === "rectangle") {
      this.previewPrimitive = new PreviewRectanglePrimitive(
        "preview",
        this.chart,
        this.series,
        anchor,
        anchor,
      )
    } else if (this.activeTool === "risk-reward") {
      const entry = this.pendingPoints[0]!
      const stop = this.pendingPoints[1] ?? entry
      this.previewPrimitive = new RiskRewardPrimitive(
        "preview",
        this.chart,
        this.series,
        entry,
        stop,
        entry,
      )
    } else if (this.activeTool === "price-range") {
      this.previewPrimitive = new PriceRangePrimitive(
        "preview",
        this.chart,
        this.series,
        anchor,
        anchor,
      )
    }

    if (this.previewPrimitive) {
      this.series.attachPrimitive(
        this.previewPrimitive as unknown as ISeriesPrimitive<Time>,
      )
    }
  }

  private updatePreview(cursor: ChartPoint): void {
    if (!this.previewPrimitive) {
      this.ensurePreview()
      return
    }

    if (
      this.activeTool === "trendline" &&
      this.previewPrimitive instanceof TrendLinePrimitive
    ) {
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
    } else if (
      this.activeTool === "price-range" &&
      this.previewPrimitive instanceof PriceRangePrimitive
    ) {
      this.previewPrimitive.updateEndPoint(cursor)
    }
  }

  private finalizeHorizontalLine(point: ChartPoint): void {
    if (!this.chart || !this.series) return

    const id = nextDrawingId()
    const record: DrawingRecord = {
      id,
      type: "horizontal-line",
      price: point.price,
      time: point.time,
    }
    const primitive = new HorizontalLinePrimitive(
      id,
      this.chart,
      this.series,
      point.price,
      point.time,
    )

    this.records.push(record)
    this.attachPrimitive(primitive)
    this.persistCurrentSymbol()
    this.selectDrawing(id)

    this.activeTool = "cursor"
    this.onToolChange?.("cursor")
  }

  private finalizeDrawing(): void {
    if (!this.chart || !this.series) return

    const id = nextDrawingId()
    let record: DrawingRecord | null = null
    let primitive: CustomPrimitive | null = null

    if (this.activeTool === "trendline" && this.pendingPoints.length >= 2) {
      const p1 = this.pendingPoints[0]!
      const p2 = this.pendingPoints[1]!
      record = { id, type: "trendline", p1, p2 }
      primitive = new TrendLinePrimitive(id, this.chart, this.series, p1, p2)
    } else if (
      this.activeTool === "rectangle" &&
      this.pendingPoints.length >= 2
    ) {
      const p1 = this.pendingPoints[0]!
      const p2 = this.pendingPoints[1]!
      record = { id, type: "rectangle", p1, p2 }
      primitive = new RectanglePrimitive(id, this.chart, this.series, p1, p2)
    } else if (
      this.activeTool === "risk-reward" &&
      this.pendingPoints.length >= 3
    ) {
      const entry = this.pendingPoints[0]!
      const stop = this.pendingPoints[1]!
      const target = this.pendingPoints[2]!
      record = { id, type: "risk-reward", entry, stop, target }
      primitive = new RiskRewardPrimitive(
        id,
        this.chart,
        this.series,
        entry,
        stop,
        target,
      )
    } else if (
      this.activeTool === "price-range" &&
      this.pendingPoints.length >= 2
    ) {
      const p1 = this.pendingPoints[0]!
      const p2 = this.pendingPoints[1]!
      record = { id, type: "price-range", p1, p2 }
      primitive = new PriceRangePrimitive(id, this.chart, this.series, p1, p2)
    }

    this.removePreview()
    this.pendingPoints = []

    if (record && primitive) {
      this.records.push(record)
      this.attachPrimitive(primitive)
      this.persistCurrentSymbol()
      this.selectDrawing(id)
    }

    this.activeTool = "cursor"
    this.onToolChange?.("cursor")
  }
}

function paramTimeToTime(
  timeScale: import("lightweight-charts").ITimeScaleApi<Time>,
  logical: number,
): Time | null {
  const coord = timeScale.logicalToCoordinate(
    logical as unknown as import("lightweight-charts").Logical,
  )
  if (coord === null) return null
  const time = timeScale.coordinateToTime(coord)
  return time ?? null
}

export function clearAllChartDrawings(): void {
  symbolDrawingsCache.clear()
}
