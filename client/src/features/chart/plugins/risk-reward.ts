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

export interface RiskRewardOptions {
  profitFillColor: string
  lossFillColor: string
  entryLineColor: string
  lineWidth: number
}

const defaultOptions: RiskRewardOptions = {
  profitFillColor: "rgba(34, 197, 94, 0.15)",
  lossFillColor: "rgba(239, 68, 68, 0.15)",
  entryLineColor: "#3b82f6",
  lineWidth: 1,
}

function box(a: number, b: number, ratio: number) {
  const pos = Math.round(Math.min(a, b) * ratio)
  const length = Math.round(Math.abs(a - b) * ratio)
  return { position: pos, length }
}

class RiskRewardPaneRenderer implements IPrimitivePaneRenderer {
  private readonly entry: ViewPoint
  private readonly stop: ViewPoint
  private readonly target: ViewPoint
  private readonly options: RiskRewardOptions
  private readonly riskRewardLabel: string

  constructor(
    entry: ViewPoint,
    stop: ViewPoint,
    target: ViewPoint,
    options: RiskRewardOptions,
    riskRewardLabel: string,
  ) {
    this.entry = entry
    this.stop = stop
    this.target = target
    this.options = options
    this.riskRewardLabel = riskRewardLabel
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

      const xLeft = Math.min(...xCandidates)
      const xRight = Math.max(...xCandidates)
      const horizontal = box(xLeft, xRight, hr)

      const entryY = this.entry.y
      const stopY = this.stop.y
      const targetY = this.target.y

      const lossBox = box(entryY, stopY, vr)
      ctx.fillStyle = this.options.lossFillColor
      ctx.fillRect(
        horizontal.position,
        lossBox.position,
        horizontal.length,
        lossBox.length,
      )

      const profitBox = box(entryY, targetY, vr)
      ctx.fillStyle = this.options.profitFillColor
      ctx.fillRect(
        horizontal.position,
        profitBox.position,
        horizontal.length,
        profitBox.length,
      )

      const entryLineY = Math.round(entryY * vr)
      ctx.strokeStyle = this.options.entryLineColor
      ctx.lineWidth = this.options.lineWidth
      ctx.setLineDash([4 * hr, 4 * hr])
      ctx.beginPath()
      ctx.moveTo(horizontal.position, entryLineY)
      ctx.lineTo(horizontal.position + horizontal.length, entryLineY)
      ctx.stroke()
      ctx.setLineDash([])

      if (this.riskRewardLabel) {
        ctx.font = `${11 * vr}px "JetBrains Mono", monospace`
        ctx.fillStyle = "#8b949e"
        ctx.fillText(
          this.riskRewardLabel,
          Math.round(xRight * hr) + 6 * hr,
          Math.round(entryY * vr) - 6 * vr,
        )
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
      this.source.options,
      this.source.riskRewardLabel,
    )
  }
}

function computeRiskRewardLabel(
  entry: ChartPoint,
  stop: ChartPoint,
  target: ChartPoint,
): string {
  const risk = Math.abs(entry.price - stop.price)
  const reward = Math.abs(target.price - entry.price)
  if (risk <= 0) return "R:R —"
  const ratio = reward / risk
  return `R:R 1:${ratio.toFixed(2)}`
}

export class RiskRewardPrimitive implements ISeriesPrimitive<Time> {
  readonly chart: IChartApi
  readonly series: ISeriesApi<SeriesType>
  entry: ChartPoint
  stop: ChartPoint
  target: ChartPoint
  readonly options: RiskRewardOptions
  riskRewardLabel: string
  private readonly _paneViews: RiskRewardPaneView[]

  constructor(
    chart: IChartApi,
    series: ISeriesApi<SeriesType>,
    entry: ChartPoint,
    stop: ChartPoint,
    target: ChartPoint,
    options?: Partial<RiskRewardOptions>,
  ) {
    this.chart = chart
    this.series = series
    this.entry = entry
    this.stop = stop
    this.target = target
    this.options = { ...defaultOptions, ...options }
    this.riskRewardLabel = computeRiskRewardLabel(entry, stop, target)
    this._paneViews = [new RiskRewardPaneView(this)]
  }

  updateStop(stop: ChartPoint): void {
    this.stop = stop
    this.riskRewardLabel = computeRiskRewardLabel(this.entry, stop, this.target)
    this.updateAllViews()
  }

  updateTarget(target: ChartPoint): void {
    this.target = target
    this.riskRewardLabel = computeRiskRewardLabel(this.entry, this.stop, target)
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
