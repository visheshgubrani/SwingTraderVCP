import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  PrimitiveHoveredItem,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts"

import { avg, formatPrice } from "./math-utils"
import type { ChartPoint, DrawingStyle, ViewPoint } from "./types"

export interface RiskRewardOptions extends DrawingStyle {
  profitFillColor: string
  lossFillColor: string
  entryLineColor: string
  targetLineColor: string
  stopLineColor: string
}

const defaultOptions: RiskRewardOptions = {
  color: "#38bdf8",
  profitFillColor: "rgba(8, 153, 129, 0.22)",
  lossFillColor: "rgba(242, 54, 69, 0.22)",
  entryLineColor: "#38bdf8",
  targetLineColor: "#22c55e",
  stopLineColor: "#ef4444",
  lineWidth: 1.5,
  lineStyle: 0,
}

class RiskRewardPaneRenderer implements IPrimitivePaneRenderer {
  private readonly entry: ViewPoint
  private readonly stop: ViewPoint
  private readonly target: ViewPoint
  private readonly entryPoint: ChartPoint
  private readonly stopPoint: ChartPoint
  private readonly targetPoint: ChartPoint
  private readonly options: RiskRewardOptions
  private readonly selected: boolean
  private readonly hovered: boolean
  private readonly hoverHandleIndex: number | null

  constructor(
    entry: ViewPoint,
    stop: ViewPoint,
    target: ViewPoint,
    entryPoint: ChartPoint,
    stopPoint: ChartPoint,
    targetPoint: ChartPoint,
    options: RiskRewardOptions,
    selected: boolean,
    hovered: boolean,
    hoverHandleIndex: number | null,
  ) {
    this.entry = entry
    this.stop = stop
    this.target = target
    this.entryPoint = entryPoint
    this.stopPoint = stopPoint
    this.targetPoint = targetPoint
    this.options = options
    this.selected = selected
    this.hovered = hovered
    this.hoverHandleIndex = hoverHandleIndex
  }

  draw(target: Parameters<IPrimitivePaneRenderer["draw"]>[0]): void {
    target.useBitmapCoordinateSpace((scope) => {
      if (
        this.entry.x === null ||
        this.entry.y === null ||
        this.stop.y === null ||
        this.target.y === null
      ) {
        return
      }

      const ctx = scope.context
      const hr = scope.horizontalPixelRatio
      const vr = scope.verticalPixelRatio

      const xCandidates = [this.entry.x, this.stop.x, this.target.x].filter(
        (x): x is number => x !== null,
      )
      if (xCandidates.length === 0) return

      const xLeft = Math.round(Math.min(...xCandidates) * hr)
      const xRight = Math.round(Math.max(...xCandidates) * hr)
      const width = Math.max(xRight - xLeft, 40 * hr)

      const entryY = Math.round(this.entry.y * vr)
      const stopY = Math.round(this.stop.y * vr)
      const targetY = Math.round(this.target.y * vr)

      // 1. Draw Target (Profit) Shaded Zone Box
      const profitTop = Math.min(entryY, targetY)
      const profitHeight = Math.abs(entryY - targetY)
      ctx.save()
      ctx.fillStyle = this.options.profitFillColor
      ctx.fillRect(xLeft, profitTop, width, profitHeight)
      ctx.strokeStyle = this.options.targetLineColor
      ctx.lineWidth = this.options.lineWidth * avg(hr, vr)
      ctx.strokeRect(xLeft, profitTop, width, profitHeight)
      ctx.restore()

      // 2. Draw Stop Loss Shaded Zone Box
      const lossTop = Math.min(entryY, stopY)
      const lossHeight = Math.abs(entryY - stopY)
      ctx.save()
      ctx.fillStyle = this.options.lossFillColor
      ctx.fillRect(xLeft, lossTop, width, lossHeight)
      ctx.strokeStyle = this.options.stopLineColor
      ctx.lineWidth = this.options.lineWidth * avg(hr, vr)
      ctx.strokeRect(xLeft, lossTop, width, lossHeight)
      ctx.restore()

      // 3. Draw Entry Dashed Line
      ctx.save()
      ctx.strokeStyle = this.options.entryLineColor
      ctx.lineWidth = (this.options.lineWidth + 0.5) * avg(hr, vr)
      ctx.setLineDash([4 * hr, 4 * hr])
      ctx.beginPath()
      ctx.moveTo(xLeft, entryY)
      ctx.lineTo(xLeft + width, entryY)
      ctx.stroke()
      ctx.restore()

      // 4. Calculate Risk / Reward Ratios & Percentages
      const entryPrice = this.entryPoint.price
      const stopPrice = this.stopPoint.price
      const targetPrice = this.targetPoint.price

      const risk = Math.abs(entryPrice - stopPrice)
      const reward = Math.abs(targetPrice - entryPrice)
      const rrRatio = risk > 0 ? (reward / risk).toFixed(2) : "0.00"

      const targetPct =
        entryPrice > 0
          ? (((targetPrice - entryPrice) / entryPrice) * 100).toFixed(2)
          : "0.00"
      const stopPct =
        entryPrice > 0
          ? (((stopPrice - entryPrice) / entryPrice) * 100).toFixed(2)
          : "0.00"

      // 5. Draw Floating TradingView Stats Badge
      const badgeX = xLeft + width / 2
      const badgeY = entryY

      const text = `Risk/Reward: 1 : ${rrRatio}  |  Target: +${targetPct}%  |  Stop: ${stopPct}%`

      ctx.save()
      ctx.font = `${10.5 * vr}px "JetBrains Mono", monospace`
      const textWidth = ctx.measureText(text).width
      const padX = 8 * hr
      const padY = 4 * vr

      ctx.fillStyle = "#070b12"
      ctx.strokeStyle = "#38bdf8"
      ctx.lineWidth = 1.5 * hr
      ctx.beginPath()
      ctx.roundRect(
        badgeX - textWidth / 2 - padX,
        badgeY - 10 * vr - padY,
        textWidth + padX * 2,
        20 * vr + padY * 2,
        5 * avg(hr, vr),
      )
      ctx.fill()
      ctx.stroke()

      ctx.fillStyle = "#ffffff"
      ctx.textAlign = "center"
      ctx.textBaseline = "middle"
      ctx.fillText(text, badgeX, badgeY + 1 * vr)
      ctx.restore()

      // 6. Price labels on right edge of box
      ctx.save()
      ctx.font = `${9.5 * vr}px "JetBrains Mono", monospace`
      ctx.fillStyle = "#22c55e"
      ctx.fillText(
        `Target: ${formatPrice(targetPrice)}`,
        xLeft + width + 6 * hr,
        targetY,
      )
      ctx.fillStyle = "#ef4444"
      ctx.fillText(
        `Stop: ${formatPrice(stopPrice)}`,
        xLeft + width + 6 * hr,
        stopY,
      )
      ctx.fillStyle = "#38bdf8"
      ctx.fillText(
        `Entry: ${formatPrice(entryPrice)}`,
        xLeft + width + 6 * hr,
        entryY,
      )
      ctx.restore()

      // 7. Control Drag Handles on Entry (0), Stop (1), Target (2)
      if (this.selected || this.hovered) {
        const handleCenterX = xLeft + width / 2
        const handles = [
          { x: handleCenterX, y: entryY, index: 0, color: "#38bdf8" },
          { x: handleCenterX, y: stopY, index: 1, color: "#ef4444" },
          { x: handleCenterX, y: targetY, index: 2, color: "#22c55e" },
        ]

        for (const h of handles) {
          const isHoveredHandle = this.hoverHandleIndex === h.index
          const radius = (isHoveredHandle ? 6.5 : 5) * avg(hr, vr)

          ctx.save()
          ctx.beginPath()
          ctx.arc(h.x, h.y, radius, 0, 2 * Math.PI)
          ctx.fillStyle = "#ffffff"
          ctx.shadowColor = "rgba(0, 0, 0, 0.4)"
          ctx.shadowBlur = 4 * avg(hr, vr)
          ctx.fill()

          ctx.lineWidth = 2 * avg(hr, vr)
          ctx.strokeStyle = h.color
          ctx.stroke()
          ctx.restore()
        }
      }
    })
  }
}

class RiskRewardPaneView implements IPrimitivePaneView {
  private entry: ViewPoint = { x: null, y: null }
  private stop: ViewPoint = { x: null, y: null }
  private target: ViewPoint = { x: null, y: null }
  private readonly source: RiskRewardPrimitive

  constructor(source: RiskRewardPrimitive) {
    this.source = source
  }

  update(): void {
    const series = this.source.series
    const timeScale = this.source.chart.timeScale()
    const toView = (p: ChartPoint): ViewPoint => ({
      x: timeScale.timeToCoordinate(p.time),
      y: series.priceToCoordinate(p.price),
    })
    this.entry = toView(this.source.entry)
    this.stop = toView(this.source.stop)
    this.target = toView(this.source.target)
  }

  renderer(): IPrimitivePaneRenderer {
    return new RiskRewardPaneRenderer(
      this.entry,
      this.stop,
      this.target,
      this.source.entry,
      this.source.stop,
      this.source.target,
      this.source.options,
      this.source.selected,
      this.source.hovered,
      this.source.hoverHandleIndex,
    )
  }
}

export class RiskRewardPrimitive implements ISeriesPrimitive<Time> {
  readonly id: string
  readonly chart: IChartApi
  readonly series: ISeriesApi<SeriesType>
  entry: ChartPoint
  stop: ChartPoint
  target: ChartPoint
  options: RiskRewardOptions
  selected = false
  hovered = false
  hoverHandleIndex: number | null = null

  private _requestUpdate?: () => void
  private readonly _paneViews: RiskRewardPaneView[]

  constructor(
    id: string,
    chart: IChartApi,
    series: ISeriesApi<SeriesType>,
    entry: ChartPoint,
    stop: ChartPoint,
    target: ChartPoint,
    options?: Partial<RiskRewardOptions>,
  ) {
    this.id = id
    this.chart = chart
    this.series = series
    this.entry = entry
    this.stop = stop
    this.target = target
    this.options = { ...defaultOptions, ...options }
    this._paneViews = [new RiskRewardPaneView(this)]
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this._requestUpdate = param.requestUpdate
    this.updateAllViews()
  }

  detached(): void {
    this._requestUpdate = undefined
  }

  updateEntry(entry: ChartPoint): void {
    this.entry = entry
    this.updateAllViews()
  }

  updateStop(stop: ChartPoint): void {
    this.stop = stop
    this.updateAllViews()
  }

  updateTarget(target: ChartPoint): void {
    this.target = target
    this.updateAllViews()
  }

  setSelected(selected: boolean): void {
    this.selected = selected
    this.updateAllViews()
  }

  setHovered(hovered: boolean, handleIndex: number | null = null): void {
    if (this.hovered === hovered && this.hoverHandleIndex === handleIndex) return
    this.hovered = hovered
    this.hoverHandleIndex = handleIndex
    this.updateAllViews()
  }

  hitTest(x: number, y: number): PrimitiveHoveredItem | null {
    const timeScale = this.chart.timeScale()
    const xValues = [this.entry, this.stop, this.target].map((point) => timeScale.timeToCoordinate(point.time)).filter((value): value is NonNullable<typeof value> => value !== null)
    const entryY = this.series.priceToCoordinate(this.entry.price)
    const stopY = this.series.priceToCoordinate(this.stop.price)
    const targetY = this.series.priceToCoordinate(this.target.price)
    if (!xValues.length || entryY === null || stopY === null || targetY === null) return null
    const left = Math.min(...xValues)
    const width = Math.max(Math.max(...xValues) - left, 40)
    const centerX = left + width / 2
    for (const [index, handleY] of [entryY, stopY, targetY].entries()) {
      const distance = Math.hypot(x - centerX, y - handleY)
      if (distance <= 8) return { externalId: `drawing:${this.id}:handle:${index}`, zOrder: "normal", distance, hitTestPriority: 2, cursorStyle: "crosshair" }
    }
    const minY = Math.min(entryY, stopY, targetY)
    const maxY = Math.max(entryY, stopY, targetY)
    return x >= left - 2 && x <= left + width + 2 && y >= minY - 2 && y <= maxY + 2 ? { externalId: `drawing:${this.id}:body`, zOrder: "normal", distance: 0, hitTestPriority: 0, cursorStyle: "grab" } : null
  }

  updateAllViews(): void {
    for (const view of this._paneViews) {
      view.update()
    }
    this._requestUpdate?.()
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this._paneViews
  }
}
