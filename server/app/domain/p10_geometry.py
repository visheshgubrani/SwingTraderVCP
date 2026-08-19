"""P10 Geometry & Structural Rules.

Deterministic Python calculation of ATR14, contraction geometry, structural stop,
chase ceiling, and target validation according to AGENTS.md §5.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from typing import Sequence


DEFAULT_TICK_SIZE = Decimal("0.05")
MAX_STOP_DISTANCE_PCT = Decimal("8.0")


@dataclass(frozen=True)
class CandleData:
    open: float
    high: float
    low: float
    close: float
    volume: int
    date: str | None = None


@dataclass(frozen=True)
class ContractionAnchor:
    index: int
    high: float
    low: float
    date: str | None = None


@dataclass(frozen=True)
class ProposalGeometry:
    atr14: Decimal
    pivot_price: Decimal
    initial_stop: Decimal
    stop_distance_pct: Decimal
    chase_ceiling: Decimal
    t1: Decimal
    t2: Decimal
    t3: Decimal
    r_distance: Decimal
    is_valid: bool
    rejection_reason: str | None = None


@dataclass(frozen=True)
class ChartGeometryAnchor:
    date: str
    price: Decimal
    anchor_type: str


@dataclass(frozen=True)
class DeterministicChartGeometry:
    anchors: tuple[ChartGeometryAnchor, ...]
    resistance: Decimal
    final_contraction_low: Decimal
    initial_stop: Decimal


def snap_to_tick(price: float | Decimal, tick_size: Decimal = DEFAULT_TICK_SIZE) -> Decimal:
    """Snaps price to the nearest instrument tick size (e.g. 0.05)."""
    p = Decimal(str(price)) if isinstance(price, (int, float, str)) else price
    ticks = (p / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return ticks * tick_size


def floor_to_tick(price: float | Decimal, tick_size: Decimal = DEFAULT_TICK_SIZE) -> Decimal:
    """Floor a price to the nearest tradable tick.

    Long-side stop and chase calculations use this helper so deterministic
    rounding can never make a stop less conservative or enlarge a chase cap.
    """
    p = Decimal(str(price)) if isinstance(price, (int, float, str)) else price
    ticks = (p / tick_size).to_integral_value(rounding=ROUND_FLOOR)
    return ticks * tick_size


def is_tick_aligned(price: Decimal, tick_size: Decimal = DEFAULT_TICK_SIZE) -> bool:
    if price <= 0 or tick_size <= 0:
        return False
    return price % tick_size == 0


def compute_atr14(candles: Sequence[CandleData]) -> Decimal:
    """Computes standard 14-period Average True Range (ATR) over candles."""
    if len(candles) < 15:
        raise ValueError("Need at least 15 candles to calculate ATR14")

    tr_list: list[float] = []
    for i in range(1, len(candles)):
        curr = candles[i]
        prev = candles[i - 1]
        tr = max(
            curr.high - curr.low,
            abs(curr.high - prev.close),
            abs(curr.low - prev.close),
        )
        tr_list.append(tr)

    # Initial 14-period simple average
    atr = sum(tr_list[:14]) / 14.0
    # Wilder's smoothing for subsequent candles
    for tr in tr_list[14:]:
        atr = (atr * 13.0 + tr) / 14.0

    return Decimal(str(round(atr, 4)))


def derive_chart_geometry(
    candles: Sequence[CandleData],
    *,
    tick_size: Decimal = DEFAULT_TICK_SIZE,
) -> DeterministicChartGeometry:
    """Derive reproducible chart annotations without model involvement.

    The detail window's final 60 sessions are split into three ordered swing
    windows.  Dated extrema are merged when their prices are within 0.5 ATR14.
    These anchors guide the visual read; proposal validity still requires the
    model's dated anchors to snap back to frozen OHLCV.
    """
    if len(candles) < 60:
        raise ValueError("Need at least 60 candles to derive chart geometry")
    if any(candle.date is None for candle in candles[-60:]):
        raise ValueError("Dated candles are required to derive chart geometry")

    atr14 = compute_atr14(candles)
    tolerance = atr14 * Decimal("0.50")
    detail = list(candles[-60:])
    raw: list[ChartGeometryAnchor] = []
    for start in (0, 20, 40):
        window = detail[start:start + 20]
        high = max(window, key=lambda candle: candle.high)
        low = min(window, key=lambda candle: candle.low)
        raw.extend(
            (
                ChartGeometryAnchor(
                    date=str(high.date),
                    price=Decimal(str(high.high)),
                    anchor_type="contraction_high",
                ),
                ChartGeometryAnchor(
                    date=str(low.date),
                    price=Decimal(str(low.low)),
                    anchor_type="contraction_low",
                ),
            )
        )

    merged: list[ChartGeometryAnchor] = []
    for anchor in sorted(raw, key=lambda item: (item.date, item.anchor_type)):
        merge_index = next(
            (
                index
                for index in range(len(merged) - 1, -1, -1)
                if merged[index].anchor_type == anchor.anchor_type
                and abs(merged[index].price - anchor.price) <= tolerance
            ),
            None,
        )
        if merge_index is None:
            merged.append(anchor)
        else:
            # Prefer the later dated observation for a merged price zone.
            merged[merge_index] = anchor
    merged.sort(key=lambda anchor: (anchor.date, anchor.anchor_type))

    final_window = detail[-20:]
    resistance = floor_to_tick(
        Decimal(str(max(candle.high for candle in final_window))),
        tick_size,
    )
    final_low = Decimal(str(min(candle.low for candle in final_window)))
    return DeterministicChartGeometry(
        anchors=tuple(merged),
        resistance=resistance,
        final_contraction_low=final_low,
        initial_stop=calculate_structural_stop(final_low, atr14, tick_size),
    )


def calculate_structural_stop(
    final_contraction_low: float | Decimal,
    atr14: Decimal,
    tick_size: Decimal = DEFAULT_TICK_SIZE,
) -> Decimal:
    """Initial structural stop = final-contraction low minus 0.25 * ATR14, snapped to tick."""
    raw_low = Decimal(str(final_contraction_low))
    buffer = atr14 * Decimal("0.25")
    raw_stop = raw_low - buffer
    return floor_to_tick(raw_stop, tick_size)


def calculate_chase_ceiling(
    pivot: Decimal,
    initial_stop: Decimal,
    tick_size: Decimal = DEFAULT_TICK_SIZE,
) -> tuple[Decimal, Decimal]:
    """Maximum acceptable entry = pivot + min(2% of pivot, 0.5 * (pivot - initial_stop)).
    
    Returns (chase_ceiling, r_distance).
    """
    r_distance = pivot - initial_stop
    if r_distance <= 0:
        raise ValueError(f"Pivot ({pivot}) must be greater than initial stop ({initial_stop})")

    cap_pct = pivot * Decimal("0.02")
    cap_r = r_distance * Decimal("0.50")
    max_slippage = min(cap_pct, cap_r)
    raw_ceiling = pivot + max_slippage
    return floor_to_tick(raw_ceiling, tick_size), r_distance


def validate_proposal_targets(
    pivot: Decimal,
    initial_stop: Decimal,
    chase_ceiling: Decimal,
    t1: Decimal,
    t2: Decimal,
    t3: Decimal,
    tick_size: Decimal = DEFAULT_TICK_SIZE,
) -> tuple[bool, str | None]:
    """Validates targets conservatively against chase ceiling and stop:
    - T1 >= chase_ceiling + 1.0 * R (providing at least 1R from chase ceiling)
    - T2 >= chase_ceiling + 2.0 * R (providing at least 2R from chase ceiling)
    - T3 >= chase_ceiling + 3.0 * R (providing at least 3R from chase ceiling)
    - Strictly ordered: chase_ceiling < T1 < T2 < T3
    - Tick snapped and valid.
    """
    # The approved entry can fill anywhere through the chase ceiling.  The
    # conservative unit of risk is therefore worst-entry minus stop, not
    # pivot minus stop.
    r = chase_ceiling - initial_stop
    if r <= 0:
        return False, "Initial stop is at or above pivot price"

    stop_pct = ((pivot - initial_stop) / pivot) * Decimal("100")
    if stop_pct > MAX_STOP_DISTANCE_PCT:
        return False, f"Stop distance {stop_pct:.2f}% exceeds maximum allowable 8.0%"

    if not (pivot < chase_ceiling <= t1 < t2 < t3):
        return False, f"Targets must be strictly ordered: pivot ({pivot}) < ceiling ({chase_ceiling}) <= T1 ({t1}) < T2 ({t2}) < T3 ({t3})"

    # Conservative R:R check from chase ceiling
    r1 = t1 - chase_ceiling
    r2 = t2 - chase_ceiling
    r3 = t3 - chase_ceiling

    if r1 < r:
        return False, f"T1 ({t1}) provides {r1 / r:.2f}R from chase ceiling, requires >= 1.0R"
    if r2 < Decimal("2.0") * r:
        return False, f"T2 ({t2}) provides {r2 / r:.2f}R from chase ceiling, requires >= 2.0R"
    if r3 < Decimal("3.0") * r:
        return False, f"T3 ({t3}) provides {r3 / r:.2f}R from chase ceiling, requires >= 3.0R"

    # Tick validity
    for name, target in [("T1", t1), ("T2", t2), ("T3", t3), ("Pivot", pivot), ("Stop", initial_stop)]:
        snapped = snap_to_tick(target, tick_size)
        if target != snapped:
            return False, f"{name} ({target}) is not aligned with tick size ({tick_size})"

    return True, None


def construct_and_validate_proposal(
    pivot_price: float | Decimal,
    final_contraction_low: float | Decimal,
    t1: float | Decimal,
    t2: float | Decimal,
    t3: float | Decimal,
    atr14: Decimal,
    tick_size: Decimal = DEFAULT_TICK_SIZE,
) -> ProposalGeometry:
    """Pure constructor and validator for a complete proposal geometry."""
    pivot = Decimal(str(pivot_price))
    dec_t1 = Decimal(str(t1))
    dec_t2 = Decimal(str(t2))
    dec_t3 = Decimal(str(t3))

    # AI-authored anchors are evidence, not values Python is allowed to
    # silently repair.  An off-tick pivot or target invalidates the proposal.
    for name, value in (
        ("pivot", pivot),
        ("T1", dec_t1),
        ("T2", dec_t2),
        ("T3", dec_t3),
    ):
        if not is_tick_aligned(value, tick_size):
            return ProposalGeometry(
                atr14=atr14,
                pivot_price=pivot,
                initial_stop=Decimal("0"),
                stop_distance_pct=Decimal("0"),
                chase_ceiling=pivot,
                t1=dec_t1,
                t2=dec_t2,
                t3=dec_t3,
                r_distance=Decimal("0"),
                is_valid=False,
                rejection_reason=f"{name} ({value}) is not aligned with tick size ({tick_size})",
            )

    stop = calculate_structural_stop(final_contraction_low, atr14, tick_size)
    
    if stop >= pivot:
        return ProposalGeometry(
            atr14=atr14,
            pivot_price=pivot,
            initial_stop=stop,
            stop_distance_pct=Decimal("0"),
            chase_ceiling=pivot,
            t1=dec_t1,
            t2=dec_t2,
            t3=dec_t3,
            r_distance=Decimal("0"),
            is_valid=False,
            rejection_reason="Structural stop is at or above pivot",
        )

    r_distance = pivot - stop
    stop_dist_pct = (r_distance / pivot) * Decimal("100")

    if stop_dist_pct > MAX_STOP_DISTANCE_PCT:
        return ProposalGeometry(
            atr14=atr14,
            pivot_price=pivot,
            initial_stop=stop,
            stop_distance_pct=stop_dist_pct,
            chase_ceiling=pivot,
            t1=dec_t1,
            t2=dec_t2,
            t3=dec_t3,
            r_distance=r_distance,
            is_valid=False,
            rejection_reason=f"Stop distance {stop_dist_pct:.2f}% exceeds maximum 8.0%",
        )

    ceiling, _ = calculate_chase_ceiling(pivot, stop, tick_size)
    valid, reason = validate_proposal_targets(
        pivot=pivot,
        initial_stop=stop,
        chase_ceiling=ceiling,
        t1=dec_t1,
        t2=dec_t2,
        t3=dec_t3,
        tick_size=tick_size,
    )

    return ProposalGeometry(
        atr14=atr14,
        pivot_price=pivot,
        initial_stop=stop,
        stop_distance_pct=stop_dist_pct,
        chase_ceiling=ceiling,
        t1=dec_t1,
        t2=dec_t2,
        t3=dec_t3,
        r_distance=r_distance,
        is_valid=valid,
        rejection_reason=reason,
    )
