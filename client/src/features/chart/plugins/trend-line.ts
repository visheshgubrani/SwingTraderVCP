import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesType,
  Time,
} from "lightweight-charts"

import type { ChartPoint, ViewPoint } from "./types"

export interface TrendLineOptions {
  lineColor: string
  width: number
}

const defaultOptions: TrendLineOptions = {
  lineColor: "#3b82f6",
  width: 2,
}

class TrendLinePaneRenderer implements IPrimitivePaneRenderer {
  private readonly p1: ViewPoint
  private readonly p2: ViewPoint
  private readonly options: TrendLineOptions

  constructor(p1: ViewPoint, p2: ViewPoint, options: TrendLineOptions) {
    this.p1 = p1
    this.p2 = p2
    this.options = options
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
      const x1 = Math.round(this.p1.x * scope.horizontalPixelRatio)
      const y1 = Math.round(this.p1.y * scope.verticalPixelRatio)
      const x2 = Math.round(this.p2.x * scope.horizontalPixelRatio)
      const y2 = Math.round(this.p2.y * scope.verticalPixelRatio)

      ctx.lineWidth = this.options.width
      ctx.strokeStyle = this.options.lineColor
      ctx.beginPath()
      ctx.moveTo(x1, y1)
      ctx.lineTo(x2, y2)
      ctx.stroke()
    })
  }
}

class TrendLinePaneView implements IPrimitivePaneView {
  private p1: ViewPoint = { x: null, y: null }
  private p2: ViewPoint = { x: null, y: null }
  private readonly source: TrendLinePrimitive

  constructor(source: TrendLinePrimitive) {
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
    return new TrendLinePaneRenderer(this.p1, this.p2, this.source.options)
  }
}

export class TrendLinePrimitive implements ISeriesPrimitive<Time> {
  readonly chart: IChartApi
  readonly series: ISeriesApi<SeriesType>
  p1: ChartPoint
  p2: ChartPoint
  readonly options: TrendLineOptions
  private readonly _paneViews: TrendLinePaneView[]

  constructor(
    chart: IChartApi,
    series: ISeriesApi<SeriesType>,
    p1: ChartPoint,
    p2: ChartPoint,
    options?: Partial<TrendLineOptions>,
  ) {
    this.chart = chart
    this.series = series
    this.p1 = p1
    this.p2 = p2
    this.options = { ...defaultOptions, ...options }
    this._paneViews = [new TrendLinePaneView(this)]
  }

  updateEndPoint(p2: ChartPoint): void {
    this.p2 = p2
    this.updateAllViews()
  }

  updateAllViews(): void {
    for (const view of this._paneViews) {
      view.update()
    }
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this._paneViews
  }
}
