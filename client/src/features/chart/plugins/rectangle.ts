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

export interface RectangleOptions {
  fillColor: string
  previewFillColor: string
}

const defaultOptions: RectangleOptions = {
  fillColor: "rgba(59, 130, 246, 0.18)",
  previewFillColor: "rgba(59, 130, 246, 0.08)",
}

function box(a: number, b: number, ratio: number) {
  const pos = Math.round(Math.min(a, b) * ratio)
  const length = Math.round(Math.abs(a - b) * ratio)
  return { position: pos, length }
}

class RectanglePaneRenderer implements IPrimitivePaneRenderer {
  private readonly p1: ViewPoint
  private readonly p2: ViewPoint
  private readonly fillColor: string

  constructor(p1: ViewPoint, p2: ViewPoint, fillColor: string) {
    this.p1 = p1
    this.p2 = p2
    this.fillColor = fillColor
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
      const horizontal = box(
        this.p1.x,
        this.p2.x,
        scope.horizontalPixelRatio,
      )
      const vertical = box(this.p1.y, this.p2.y, scope.verticalPixelRatio)

      ctx.fillStyle = this.fillColor
      ctx.fillRect(
        horizontal.position,
        vertical.position,
        horizontal.length,
        vertical.length,
      )
    })
  }
}

class RectanglePaneView implements IPrimitivePaneView {
  private p1: ViewPoint = { x: null, y: null }
  private p2: ViewPoint = { x: null, y: null }
  private readonly source: RectanglePrimitive

  constructor(source: RectanglePrimitive) {
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
    return new RectanglePaneRenderer(
      this.p1,
      this.p2,
      this.source.options.fillColor,
    )
  }
}

export class RectanglePrimitive implements ISeriesPrimitive<Time> {
  readonly chart: IChartApi
  readonly series: ISeriesApi<SeriesType>
  p1: ChartPoint
  p2: ChartPoint
  readonly options: RectangleOptions
  private readonly _paneViews: RectanglePaneView[]

  constructor(
    chart: IChartApi,
    series: ISeriesApi<SeriesType>,
    p1: ChartPoint,
    p2: ChartPoint,
    options?: Partial<RectangleOptions>,
  ) {
    this.chart = chart
    this.series = series
    this.p1 = p1
    this.p2 = p2
    this.options = { ...defaultOptions, ...options }
    this._paneViews = [new RectanglePaneView(this)]
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

export class PreviewRectanglePrimitive extends RectanglePrimitive {
  constructor(
    chart: IChartApi,
    series: ISeriesApi<SeriesType>,
    p1: ChartPoint,
    p2: ChartPoint,
    options?: Partial<RectangleOptions>,
  ) {
    super(chart, series, p1, p2, {
      ...options,
      fillColor: options?.previewFillColor ?? defaultOptions.previewFillColor,
    })
  }
}
