/**
 * Geometry and formatting utilities for chart drawing primitives and hit-testing.
 */

export interface Point2D {
  x: number
  y: number
}

/**
 * Returns average of two numbers.
 */
export function avg(a: number, b: number): number {
  return (a + b) / 2
}

/**
 * Returns the shortest distance from point (px, py) to line segment (x1, y1) -> (x2, y2).
 */
export function pointToSegmentDistance(
  px: number,
  py: number,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
): number {
  const dx = x2 - x1
  const dy = y2 - y1
  const lenSq = dx * dx + dy * dy

  if (lenSq === 0) {
    return Math.hypot(px - x1, py - y1)
  }

  let t = ((px - x1) * dx + (py - y1) * dy) / lenSq
  t = Math.max(0, Math.min(1, t))

  const projX = x1 + t * dx
  const projY = y1 + t * dy

  return Math.hypot(px - projX, py - projY)
}

/**
 * Checks if point (px, py) is inside rectangle defined by (x1, y1) and (x2, y2)
 * with an optional pixel padding.
 */
export function pointInBox(
  px: number,
  py: number,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  padding = 0,
): boolean {
  const minX = Math.min(x1, x2) - padding
  const maxX = Math.max(x1, x2) + padding
  const minY = Math.min(y1, y2) - padding
  const maxY = Math.max(y1, y2) + padding

  return px >= minX && px <= maxX && py >= minY && py <= maxY
}

/**
 * Formats price change percentage with sign and 2 decimals (e.g. "+5.24%" or "-2.10%").
 */
export function formatPercentChange(p1: number, p2: number): string {
  if (p1 === 0) return "0.00%"
  const pct = ((p2 - p1) / p1) * 100
  const sign = pct >= 0 ? "+" : ""
  return `${sign}${pct.toFixed(2)}%`
}

/**
 * Formats price value (e.g. 2450.75).
 */
export function formatPrice(val: number): string {
  return val >= 1000 ? val.toFixed(2) : val.toFixed(2)
}
