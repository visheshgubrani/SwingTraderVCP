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
import type { DrawingStyle, ViewPoint } from "./types"

export interface HorizontalLineOptions extends DrawingStyle {
  label: string
}

const defaultOptions: HorizontalLineOptions = {
  color: "#f59e0b",
  lineWidth: 1.5,
  lineStyle: 0,
  label: "Level",
}

class HorizontalLinePaneRenderer implements IPrimitivePaneRenderer {
  private readonly y: number | null
  private readonly price: number
  private readonly options: HorizontalLineOptions
  private readonly selected: boolean
  private readonly hovered: boolean

  constructor(
    y: number | null,
    price: number,
    options: HorizontalLineOptions,
    selected: boolean,
    hovered: boolean,
  ) {
    this.y = y
    this.price = price
    this.options = options
    this.selected = selected
    this.hovered = hovered
  }

  draw(target: Parameters<IPrimitivePaneRenderer["draw"]>[0]): void {
    target.useBitmapCoordinateSpace((scope) => {
      if (this.y === null) return

      const ctx = scope.context
      const hr = scope.horizontalPixelRatio
      const vr = scope.verticalPixelRatio

      const lineY = Math.round(this.y * vr)
      const width = scope.bitmapSize.width

      // 1. Draw Selection Glow Line
      if (this.selected) {
        ctx.save()
        ctx.lineWidth = (this.options.lineWidth + 4) * avg(hr, vr)
        ctx.strokeStyle = "rgba(245, 158, 11, 0.25)"
        ctx.beginPath()
        ctx.moveTo(0, lineY)
        ctx.lineTo(width, lineY)
        ctx.stroke()
        ctx.restore()
      }

      // 2. Draw Main Line
      ctx.save()
      ctx.lineWidth = this.options.lineWidth * avg(hr, vr)
      ctx.strokeStyle = this.options.color

      if (this.options.lineStyle === 1) {
        ctx.setLineDash([3 * hr, 3 * hr])
      } else if (this.options.lineStyle === 2) {
        ctx.setLineDash([6 * hr, 4 * hr])
      }

      ctx.beginPath()
      ctx.moveTo(0, lineY)
      ctx.lineTo(width, lineY)
      ctx.stroke()
      ctx.restore()

      // 3. Right Price Tag Pill
      const priceText = `${formatPrice(this.price)}`
      ctx.save()
      ctx.font = `${10 * vr}px "JetBrains Mono", monospace`
      const textWidth = ctx.measureText(priceText).width
      const tagWidth = textWidth + 12 * hr
      const tagHeight = 18 * vr
      const tagX = width - tagWidth - 8 * hr
      const tagY = lineY - tagHeight / 2

      ctx.fillStyle = this.options.color
      ctx.beginPath()
      ctx.roundRect(tagX, tagY, tagWidth, tagHeight, 3 * avg(hr, vr))
      ctx.fill()

      ctx.fillStyle = "#ffffff"
      ctx.textAlign = "center"
      ctx.textBaseline = "middle"
      ctx.fillText(priceText, tagX + tagWidth / 2, lineY)
      ctx.restore()

      // 4. Center Drag Handle Dot
      if (this.selected || this.hovered) {
        const handleX = width / 2
        const radius = 5.5 * avg(hr, vr)

        ctx.save()
        ctx.beginPath()
        ctx.arc(handleX, lineY, radius, 0, 2 * Math.PI)
        ctx.fillStyle = "#ffffff"
        ctx.shadowColor = "rgba(0, 0, 0, 0.4)"
        ctx.shadowBlur = 4 * avg(hr, vr)
        ctx.fill()

        ctx.lineWidth = 2 * avg(hr, vr)
        ctx.strokeStyle = this.options.color
        ctx.stroke()
        ctx.restore()
      }
    })
  }
}

class HorizontalLinePaneView implements IPrimitivePaneView {
  private viewPoint: ViewPoint = { x: null, y: null }
  private readonly source: HorizontalLinePrimitive

  constructor(source: HorizontalLinePrimitive) {
    this.source = source
  }

  update(): void {
    const series = this.source.series
    this.viewPoint = {
      x: null,
      y: series.priceToCoordinate(this.source.price),
    }
  }

  renderer(): IPrimitivePaneRenderer {
    return new HorizontalLinePaneRenderer(
      this.viewPoint.y,
      this.source.price,
      this.source.options,
      this.source.selected,
      this.source.hovered,
    )
  }
}

export class HorizontalLinePrimitive implements ISeriesPrimitive<Time> {
  readonly id: string
  readonly chart: IChartApi
  readonly series: ISeriesApi<SeriesType>
  price: number
  time: Time
  options: HorizontalLineOptions
  selected = false
  hovered = false

  private _requestUpdate?: () => void
  private readonly _paneViews: HorizontalLinePaneView[]

  constructor(
    id: string,
    chart: IChartApi,
    series: ISeriesApi<SeriesType>,
    price: number,
    time: Time,
    options?: Partial<HorizontalLineOptions>,
  ) {
    this.id = id
    this.chart = chart
    this.series = series
    this.price = price
    this.time = time
    this.options = { ...defaultOptions, ...options }
    this._paneViews = [new HorizontalLinePaneView(this)]
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this._requestUpdate = param.requestUpdate
    this.updateAllViews()
  }

  detached(): void {
    this._requestUpdate = undefined
  }

  updatePrice(price: number): void {
    this.price = price
    this.updateAllViews()
  }

  setSelected(selected: boolean): void {
    this.selected = selected
    this.updateAllViews()
  }

  setHovered(hovered: boolean): void {
    if (this.hovered === hovered) return
    this.hovered = hovered
    this.updateAllViews()
  }

  hitTest(_x: number, y: number): PrimitiveHoveredItem | null {
    const lineY = this.series.priceToCoordinate(this.price)
    if (lineY === null) return null
    const distance = Math.abs(y - lineY)
    return distance <= 6 ? { externalId: `drawing:${this.id}:body`, zOrder: "normal", distance, hitTestPriority: 1, cursorStyle: "grab" } : null
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
