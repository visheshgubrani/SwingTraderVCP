import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts"

import { avg } from "./math-utils"

export interface VisionContractionBand {
  label: string
  start: string
  end: string
  high: number
  low: number
}

export interface VisionOverlayOptions {
  bands: VisionContractionBand[]
}

interface BandPoints {
  startX: number | null
  endX: number | null
  highY: number | null
  lowY: number | null
}

const BAND_FILL = "rgba(41, 98, 255, 0.10)"
const BAND_STROKE = "rgba(41, 98, 255, 0.55)"
const FIRST_BAND_FILL = "rgba(139, 92, 246, 0.10)"
const FIRST_BAND_STROKE = "rgba(139, 92, 246, 0.6)"

class VisionOverlayPaneRenderer implements IPrimitivePaneRenderer {
  private readonly points: BandPoints[]
  private readonly labels: string[]
  private readonly first: boolean[]

  constructor(
    points: BandPoints[],
    labels: string[],
    first: boolean[],
  ) {
    this.points = points
    this.labels = labels
    this.first = first
  }

  draw(target: Parameters<IPrimitivePaneRenderer["draw"]>[0]): void {
    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context
      const hr = scope.horizontalPixelRatio
      const vr = scope.verticalPixelRatio

      this.points.forEach((point, index) => {
        if (
          point.startX === null ||
          point.endX === null ||
          point.highY === null ||
          point.lowY === null
        ) {
          return
        }
        const left = Math.min(point.startX, point.endX) * hr
        const right = Math.max(point.startX, point.endX) * hr
        const top = Math.min(point.highY, point.lowY) * vr
        const bottom = Math.max(point.highY, point.lowY) * vr
        const width = right - left
        const height = Math.max(1, bottom - top)

        ctx.save()
        ctx.fillStyle = this.first[index] ? FIRST_BAND_FILL : BAND_FILL
        ctx.fillRect(left, top, width, height)

        ctx.strokeStyle = this.first[index] ? FIRST_BAND_STROKE : BAND_STROKE
        ctx.lineWidth = 1.25 * avg(hr, vr)
        ctx.setLineDash([4 * hr, 3 * hr])
        ctx.beginPath()
        ctx.moveTo(left + ctx.lineWidth, top + ctx.lineWidth)
        ctx.lineTo(left + ctx.lineWidth, bottom - ctx.lineWidth)
        ctx.stroke()
        ctx.setLineDash([])

        const label = this.labels[index]
        if (label) {
          ctx.font = `${10 * vr}px "JetBrains Mono", monospace`
          const textWidth = ctx.measureText(label).width
          const padX = 5 * hr
          const labelY = top + 12 * vr
          ctx.fillStyle = "#101826"
          ctx.fillRect(left + 4 * hr, labelY - 8 * vr, textWidth + padX * 2, 14 * vr)
          ctx.strokeStyle = this.first[index] ? FIRST_BAND_STROKE : BAND_STROKE
          ctx.lineWidth = 1 * avg(hr, vr)
          ctx.strokeRect(left + 4 * hr, labelY - 8 * vr, textWidth + padX * 2, 14 * vr)
          ctx.fillStyle = "#cbd5e1"
          ctx.textBaseline = "middle"
          ctx.fillText(label, left + 4 * hr + padX, labelY + 1 * vr)
        }
        ctx.restore()
      })
    })
  }
}

class VisionOverlayPaneView implements IPrimitivePaneView {
  private points: BandPoints[] = []
  private readonly source: VisionOverlayPrimitive

  constructor(source: VisionOverlayPrimitive) {
    this.source = source
  }

  update(): void {
    const chart = this.source.chart
    const timeScale = chart.timeScale()
    const series = this.source.series
    this.points = this.source.options.bands.map((band) => ({
      startX: timeScale.timeToCoordinate(band.start as Time),
      endX: timeScale.timeToCoordinate(band.end as Time),
      highY: series.priceToCoordinate(band.high),
      lowY: series.priceToCoordinate(band.low),
    }))
  }

  renderer(): IPrimitivePaneRenderer {
    return new VisionOverlayPaneRenderer(
      this.points,
      this.source.options.bands.map((band) => band.label),
      this.source.options.bands.map((_, index) => index === 0),
    )
  }
}

export class VisionOverlayPrimitive implements ISeriesPrimitive<Time> {
  readonly chart: IChartApi
  readonly series: ISeriesApi<SeriesType>
  options: VisionOverlayOptions

  private _requestUpdate?: () => void
  private readonly _paneViews: VisionOverlayPaneView[]

  constructor(
    chart: IChartApi,
    series: ISeriesApi<SeriesType>,
    options: VisionOverlayOptions,
  ) {
    this.chart = chart
    this.series = series
    this.options = options
    this._paneViews = [new VisionOverlayPaneView(this)]
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this._requestUpdate = param.requestUpdate
    this.updateAllViews()
  }

  detached(): void {
    this._requestUpdate = undefined
  }

  updateOptions(options: VisionOverlayOptions): void {
    this.options = options
    this.updateAllViews()
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
