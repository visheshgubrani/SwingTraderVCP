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

import { avg, formatPercentChange, formatPrice, pointInBox } from "./math-utils"
import type { ChartPoint, DrawingStyle, ViewPoint } from "./types"

export interface PriceRangeOptions extends DrawingStyle {
  showStats: boolean
}

const defaultOptions: PriceRangeOptions = {
  color: "#8b5cf6",
  lineWidth: 1.5,
  lineStyle: 0,
  showStats: true,
}

class PriceRangePaneRenderer implements IPrimitivePaneRenderer {
  private readonly p1: ViewPoint
  private readonly p2: ViewPoint
  private readonly p1Price: number
  private readonly p2Price: number
  private readonly options: PriceRangeOptions
  private readonly selected: boolean
  private readonly hovered: boolean
  private readonly hoverHandleIndex: number | null

  constructor(
    p1: ViewPoint,
    p2: ViewPoint,
    p1Price: number,
    p2Price: number,
    options: PriceRangeOptions,
    selected: boolean,
    hovered: boolean,
    hoverHandleIndex: number | null,
  ) {
    this.p1 = p1
    this.p2 = p2
    this.p1Price = p1Price
    this.p2Price = p2Price
    this.options = options
    this.selected = selected
    this.hovered = hovered
    this.hoverHandleIndex = hoverHandleIndex
  }

  draw(target: Parameters<IPrimitivePaneRenderer["draw"]>[0]): void {
    target.useBitmapCoordinateSpace((scope) => {
      if (
        this.p1.x === null ||
        this.p1.y === null ||
        this.p2.x === null ||
        this.p2.y === null
      ) {
        return
      }

      const ctx = scope.context
      const hr = scope.horizontalPixelRatio
      const vr = scope.verticalPixelRatio

      const x1 = Math.round(this.p1.x * hr)
      const y1 = Math.round(this.p1.y * vr)
      const x2 = Math.round(this.p2.x * hr)
      const y2 = Math.round(this.p2.y * vr)

      const left = Math.min(x1, x2)
      const top = Math.min(y1, y2)
      const width = Math.abs(x2 - x1)
      const height = Math.abs(y2 - y1)

      const diff = this.p2Price - this.p1Price
      const isPositive = diff >= 0
      const accentColor = isPositive ? "#089981" : "#f23645"
      const fillColor = isPositive
        ? "rgba(8, 153, 129, 0.16)"
        : "rgba(242, 54, 69, 0.16)"

      // 1. Shaded Box
      ctx.save()
      ctx.fillStyle = fillColor
      ctx.fillRect(left, top, width, height)

      // 2. Dashed Border & Center Line
      ctx.lineWidth = this.options.lineWidth * avg(hr, vr)
      ctx.strokeStyle = accentColor
      ctx.setLineDash([4 * hr, 4 * hr])
      ctx.strokeRect(left, top, width, height)

      // Arrow center vertical line
      const centerX = (x1 + x2) / 2
      ctx.beginPath()
      ctx.moveTo(centerX, y1)
      ctx.lineTo(centerX, y2)
      ctx.stroke()
      ctx.restore()

      // 3. Stats Badge Callout
      const pctStr = formatPercentChange(this.p1Price, this.p2Price)
      const diffStr = `${diff >= 0 ? "+" : ""}${formatPrice(diff)}`
      const text = `Measure: ${pctStr} (${diffStr})`

      const midX = (x1 + x2) / 2
      const badgeY = (y1 + y2) / 2

      ctx.save()
      ctx.font = `${10.5 * vr}px "JetBrains Mono", monospace`
      const textWidth = ctx.measureText(text).width
      const padX = 8 * hr
      const padY = 4 * vr

      ctx.fillStyle = "#131722"
      ctx.strokeStyle = accentColor
      ctx.lineWidth = 1.5 * hr
      ctx.beginPath()
      ctx.roundRect(
        midX - textWidth / 2 - padX,
        badgeY - 10 * vr - padY,
        textWidth + padX * 2,
        20 * vr + padY * 2,
        4 * avg(hr, vr),
      )
      ctx.fill()
      ctx.stroke()

      ctx.fillStyle = "#ffffff"
      ctx.textAlign = "center"
      ctx.textBaseline = "middle"
      ctx.fillText(text, midX, badgeY + 1 * vr)
      ctx.restore()

      // 4. Control Handle Dots
      if (this.selected || this.hovered) {
        const handles = [
          { x: x1, y: y1, index: 0 },
          { x: x2, y: y2, index: 1 },
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
          ctx.strokeStyle = accentColor
          ctx.stroke()
          ctx.restore()
        }
      }
    })
  }
}

class PriceRangePaneView implements IPrimitivePaneView {
  private p1: ViewPoint = { x: null, y: null }
  private p2: ViewPoint = { x: null, y: null }
  private readonly source: PriceRangePrimitive

  constructor(source: PriceRangePrimitive) {
    this.source = source
  }

  update(): void {
    const series = this.source.series
    const timeScale = this.source.chart.timeScale()
    this.p1 = {
      x: timeScale.timeToCoordinate(this.source.p1.time),
      y: series.priceToCoordinate(this.source.p1.price),
    }
    this.p2 = {
      x: timeScale.timeToCoordinate(this.source.p2.time),
      y: series.priceToCoordinate(this.source.p2.price),
    }
  }

  renderer(): IPrimitivePaneRenderer {
    return new PriceRangePaneRenderer(
      this.p1,
      this.p2,
      this.source.p1.price,
      this.source.p2.price,
      this.source.options,
      this.source.selected,
      this.source.hovered,
      this.source.hoverHandleIndex,
    )
  }
}

export class PriceRangePrimitive implements ISeriesPrimitive<Time> {
  readonly id: string
  readonly chart: IChartApi
  readonly series: ISeriesApi<SeriesType>
  p1: ChartPoint
  p2: ChartPoint
  options: PriceRangeOptions
  selected = false
  hovered = false
  hoverHandleIndex: number | null = null

  private _requestUpdate?: () => void
  private readonly _paneViews: PriceRangePaneView[]

  constructor(
    id: string,
    chart: IChartApi,
    series: ISeriesApi<SeriesType>,
    p1: ChartPoint,
    p2: ChartPoint,
    options?: Partial<PriceRangeOptions>,
  ) {
    this.id = id
    this.chart = chart
    this.series = series
    this.p1 = p1
    this.p2 = p2
    this.options = { ...defaultOptions, ...options }
    this._paneViews = [new PriceRangePaneView(this)]
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this._requestUpdate = param.requestUpdate
    this.updateAllViews()
  }

  detached(): void {
    this._requestUpdate = undefined
  }

  updatePoints(p1: ChartPoint, p2: ChartPoint): void {
    this.p1 = p1
    this.p2 = p2
    this.updateAllViews()
  }

  updateEndPoint(p2: ChartPoint): void {
    this.p2 = p2
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
    const x1 = this.chart.timeScale().timeToCoordinate(this.p1.time)
    const y1 = this.series.priceToCoordinate(this.p1.price)
    const x2 = this.chart.timeScale().timeToCoordinate(this.p2.time)
    const y2 = this.series.priceToCoordinate(this.p2.price)
    if (x1 === null || y1 === null || x2 === null || y2 === null) return null
    for (const [index, point] of [{ x: x1, y: y1 }, { x: x2, y: y2 }].entries()) {
      const distance = Math.hypot(x - point.x, y - point.y)
      if (distance <= 8) return { externalId: `drawing:${this.id}:handle:${index}`, zOrder: "normal", distance, hitTestPriority: 2, cursorStyle: "crosshair" }
    }
    return pointInBox(x, y, x1, y1, x2, y2, 2) ? { externalId: `drawing:${this.id}:body`, zOrder: "normal", distance: 0, hitTestPriority: 0, cursorStyle: "grab" } : null
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
