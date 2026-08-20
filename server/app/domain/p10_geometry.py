"""P10 Geometry & Structural Rules.

Deterministic Python calculation of ATR14, contraction geometry, structural stop,
chase ceiling, and target validation according to AGENTS.md §5.
"""

from __future__ import annotations

import datetime as dt
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
    base_chase_ceiling: Decimal | None = None
    rr_adjusted_chase_ceiling: Decimal | None = None
    r_at_pivot: Decimal | None = None
    r_at_base_chase_ceiling: Decimal | None = None
    final_r_at_chase_ceiling: Decimal | None = None
    t1_r: Decimal | None = None
    t2_r: Decimal | None = None
    t3_r: Decimal | None = None
    t2_below_2r: bool = False
    t3_below_3r: bool = False


@dataclass(frozen=True)
class ChartGeometryAnchor:
    date: str
    price: Decimal
    anchor_type: str


@dataclass(frozen=True)
class VcpContractionWave:
    index: int
    high_date: str
    high_price: Decimal
    low_date: str
    low_price: Decimal
    depth_pct: Decimal


@dataclass(frozen=True)
class DeterministicChartGeometry:
    anchors: tuple[ChartGeometryAnchor, ...]
    resistance: Decimal
    final_contraction_low: Decimal
    initial_stop: Decimal
    cheat_pivot: Decimal | None = None
    cheat_stop: Decimal | None = None
    contractions: tuple[VcpContractionWave, ...] = ()


@dataclass(frozen=True)
class ValidatedPatternAnchor:
    """A model anchor grounded to the exact frozen candle High or Low."""

    date: dt.date
    price: Decimal
    anchor_type: str


@dataclass(frozen=True)
class ResistanceZone:
    """A complete-link cluster of validated resistance observations."""

    low: Decimal
    high: Decimal
    median: Decimal
    members: tuple[ValidatedPatternAnchor, ...]

    @property
    def most_recent_date(self) -> dt.date:
        return max(member.date for member in self.members)


@dataclass(frozen=True)
class PivotResistanceGrounding:
    """Deterministic evidence and outcome for grounding one proposed pivot."""

    frozen_atr14: Decimal
    tolerance: Decimal
    recent_start_date: dt.date
    eligible_anchors: tuple[ValidatedPatternAnchor, ...]
    older_boundary_dates: tuple[dt.date, ...]
    zones: tuple[ResistanceZone, ...]
    selected_zone: ResistanceZone | None
    higher_zones: tuple[ResistanceZone, ...]
    boundary_distance: Decimal | None
    next_higher_distance: Decimal | None
    next_higher_distance_atr: Decimal | None
    next_higher_distance_pct: Decimal | None
    audit_flags: tuple[str, ...]
    is_grounded: bool
    subreason: str | None


def _anchor_key(anchor: ValidatedPatternAnchor) -> tuple[Decimal, dt.date, str]:
    return (anchor.price, anchor.date, anchor.anchor_type)


def _deduplicate_price_observations(
    anchors: Sequence[ValidatedPatternAnchor],
) -> list[ValidatedPatternAnchor]:
    """Count one structural observation per candle date and exact price."""

    observations: dict[tuple[dt.date, Decimal], ValidatedPatternAnchor] = {}
    for anchor in anchors:
        key = (anchor.date, anchor.price)
        existing = observations.get(key)
        if existing is None or (
            anchor.anchor_type == "resistance"
            and existing.anchor_type != "resistance"
        ):
            observations[key] = anchor
    return sorted(observations.values(), key=_anchor_key)


def _zone_for_members(
    members: Sequence[ValidatedPatternAnchor],
) -> ResistanceZone:
    ordered = tuple(sorted(members, key=_anchor_key))
    prices = [member.price for member in ordered]
    midpoint = len(prices) // 2
    median = (
        prices[midpoint]
        if len(prices) % 2
        else (prices[midpoint - 1] + prices[midpoint]) / Decimal("2")
    )
    return ResistanceZone(
        low=prices[0],
        high=prices[-1],
        median=median,
        members=ordered,
    )


def build_resistance_zones(
    anchors: Sequence[ValidatedPatternAnchor],
    *,
    tolerance: Decimal,
) -> tuple[ResistanceZone, ...]:
    """Cluster all supplied highs with complete-link width <= ``tolerance``."""

    if tolerance < 0:
        raise ValueError("Resistance-zone tolerance cannot be negative")
    observations = _deduplicate_price_observations(anchors)
    if not observations:
        return ()

    clusters: list[list[ValidatedPatternAnchor]] = []
    current: list[ValidatedPatternAnchor] = []
    for anchor in observations:
        if current and anchor.price - current[0].price > tolerance:
            clusters.append(current)
            current = []
        current.append(anchor)
    if current:
        clusters.append(current)
    return tuple(_zone_for_members(cluster) for cluster in clusters)


def resistance_zone_distance(pivot: Decimal, zone: ResistanceZone) -> Decimal:
    """Return zero inside a zone, otherwise distance to its nearest boundary."""

    if pivot < zone.low:
        return zone.low - pivot
    if pivot > zone.high:
        return pivot - zone.high
    return Decimal("0")


def _zone_canonical_key(
    zone: ResistanceZone,
) -> tuple[tuple[Decimal, dt.date, str], ...]:
    return tuple(_anchor_key(member) for member in zone.members)


def ground_pivot_to_resistance_zones(
    *,
    pivot: Decimal,
    anchors: Sequence[ValidatedPatternAnchor],
    session_dates: Sequence[dt.date],
    frozen_atr14: Decimal,
    recent_session_count: int = 60,
) -> PivotResistanceGrounding:
    """Ground a pivot against current-base resistance zones.

    ``frozen_atr14`` is calculated once at the proposal reference date. It is
    deliberately not recomputed at each anchor date.
    """

    if pivot <= 0:
        raise ValueError("Pivot must be positive")
    if frozen_atr14 <= 0:
        raise ValueError("Frozen ATR14 must be positive")
    if recent_session_count <= 0:
        raise ValueError("Recent session count must be positive")

    detail_dates = sorted(set(session_dates))[-126:]
    if not detail_dates:
        raise ValueError("At least one frozen detail session is required")
    recent_dates = detail_dates[-recent_session_count:]
    recent_date_set = set(recent_dates)
    detail_date_set = set(detail_dates)
    recent_start_date = recent_dates[0]
    tolerance = frozen_atr14 * Decimal("0.50")

    detail_anchors = [
        anchor for anchor in anchors if anchor.date in detail_date_set
    ]
    high_anchors = _deduplicate_price_observations(
        [
            anchor
            for anchor in detail_anchors
            if anchor.anchor_type in {"resistance", "contraction_high"}
        ]
    )
    low_anchors = [
        anchor
        for anchor in detail_anchors
        if anchor.anchor_type == "contraction_low"
    ]
    recent_highs = [
        anchor for anchor in high_anchors if anchor.date in recent_date_set
    ]

    older_boundaries: list[ValidatedPatternAnchor] = []
    for boundary in high_anchors:
        if (
            boundary.anchor_type != "resistance"
            or boundary.date in recent_date_set
        ):
            continue
        later_low_dates = {
            anchor.date for anchor in low_anchors if anchor.date > boundary.date
        }
        retested = any(
            recent_high.date > boundary.date
            and abs(recent_high.price - boundary.price) <= tolerance
            for recent_high in recent_highs
        )
        if len(later_low_dates) >= 2 and retested:
            older_boundaries.append(boundary)

    eligible = _deduplicate_price_observations(
        [*recent_highs, *older_boundaries]
    )
    zones = build_resistance_zones(eligible, tolerance=tolerance)
    if not zones:
        return PivotResistanceGrounding(
            frozen_atr14=frozen_atr14,
            tolerance=tolerance,
            recent_start_date=recent_start_date,
            eligible_anchors=(),
            older_boundary_dates=(),
            zones=(),
            selected_zone=None,
            higher_zones=(),
            boundary_distance=None,
            next_higher_distance=None,
            next_higher_distance_atr=None,
            next_higher_distance_pct=None,
            audit_flags=(),
            is_grounded=False,
            subreason="no_eligible_resistance_evidence",
        )

    selected = min(
        zones,
        key=lambda zone: (
            resistance_zone_distance(pivot, zone),
            -zone.high,
            -zone.most_recent_date.toordinal(),
            _zone_canonical_key(zone),
        ),
    )
    boundary_distance = resistance_zone_distance(pivot, selected)
    higher_zones = tuple(
        sorted(
            (zone for zone in zones if zone.low > selected.high),
            key=lambda zone: (zone.low, zone.high, _zone_canonical_key(zone)),
        )
    )
    next_higher_distance = (
        max(Decimal("0"), higher_zones[0].low - pivot)
        if higher_zones
        else None
    )
    next_higher_distance_atr = (
        next_higher_distance / frozen_atr14
        if next_higher_distance is not None
        else None
    )
    next_higher_distance_pct = (
        next_higher_distance / pivot * Decimal("100")
        if next_higher_distance is not None
        else None
    )
    material_overhead = any(
        len(zone.members) >= 2
        and any(member.date in recent_date_set for member in zone.members)
        for zone in higher_zones
    )
    grounded = boundary_distance <= tolerance
    return PivotResistanceGrounding(
        frozen_atr14=frozen_atr14,
        tolerance=tolerance,
        recent_start_date=recent_start_date,
        eligible_anchors=tuple(
            sorted(eligible, key=lambda anchor: (anchor.date, anchor.price, anchor.anchor_type))
        ),
        older_boundary_dates=tuple(
            sorted(boundary.date for boundary in older_boundaries)
        ),
        zones=zones,
        selected_zone=selected,
        higher_zones=higher_zones,
        boundary_distance=boundary_distance,
        next_higher_distance=next_higher_distance,
        next_higher_distance_atr=next_higher_distance_atr,
        next_higher_distance_pct=next_higher_distance_pct,
        audit_flags=(
            ("pivot_below_material_overhead_zone",)
            if grounded and material_overhead
            else ()
        ),
        is_grounded=grounded,
        subreason=None if grounded else "outside_resistance_zone_tolerance",
    )


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


def detect_vcp_swings_and_contractions(
    candles: Sequence[CandleData],
    *,
    lookback_sessions: int = 60,
    fractal_k: int = 2,
    tick_size: Decimal = DEFAULT_TICK_SIZE,
) -> tuple[tuple[ChartGeometryAnchor, ...], tuple[VcpContractionWave, ...], Decimal, Decimal, Decimal | None, Decimal | None]:
    """Detect dynamic VCP swing peaks/troughs and contracting waves.

    Identifies the base peak and following alternating contractions (C1, C2, C3)
    with contracting depths and micro/cheat pivots.
    """
    detail = list(candles[-lookback_sessions:]) if len(candles) >= lookback_sessions else list(candles)
    n = len(detail)
    if n < 5:
        return (), (), Decimal("0"), Decimal("0"), None, None

    # Base peak in the lookback window
    peak_idx = max(range(n), key=lambda i: detail[i].high)
    base_high = detail[peak_idx]

    # Track alternating swings starting from base_high
    swings: list[tuple[str, str, float, int]] = [("H", str(base_high.date), float(base_high.high), peak_idx)]
    curr_type = "H"

    for i in range(peak_idx + 1, n):
        is_high = True
        is_low = True
        for j in range(max(0, i - fractal_k), min(n, i + fractal_k + 1)):
            if j != i:
                if detail[j].high > detail[i].high:
                    is_high = False
                if detail[j].low < detail[i].low:
                    is_low = False

        if is_low and curr_type == "H":
            swings.append(("L", str(detail[i].date), float(detail[i].low), i))
            curr_type = "L"
        elif is_low and curr_type == "L":
            if detail[i].low < swings[-1][2]:
                swings[-1] = ("L", str(detail[i].date), float(detail[i].low), i)
        elif is_high and curr_type == "L":
            swings.append(("H", str(detail[i].date), float(detail[i].high), i))
            curr_type = "H"
        elif is_high and curr_type == "H":
            if detail[i].high > swings[-1][2]:
                swings[-1] = ("H", str(detail[i].date), float(detail[i].high), i)

    # Check unclosed pullback at the end of the window
    if curr_type == "H" and n - 1 > swings[-1][3]:
        min_idx = min(range(swings[-1][3] + 1, n), key=lambda i: detail[i].low)
        swings.append(("L", str(detail[min_idx].date), float(detail[min_idx].low), min_idx))

    # Form contraction pairs
    contractions_list: list[VcpContractionWave] = []
    for idx in range(0, len(swings) - 1, 2):
        if idx + 1 < len(swings):
            h_type, h_date, h_price, _ = swings[idx]
            l_type, l_date, l_price, _ = swings[idx + 1]
            if h_type == "H" and l_type == "L" and h_price > 0:
                depth = (h_price - l_price) / h_price * 100.0
                contractions_list.append(
                    VcpContractionWave(
                        index=len(contractions_list) + 1,
                        high_date=h_date,
                        high_price=Decimal(str(h_price)),
                        low_date=l_date,
                        low_price=Decimal(str(l_price)),
                        depth_pct=Decimal(str(round(depth, 2))),
                    )
                )

    # Convert swings to anchors
    anchors_list: list[ChartGeometryAnchor] = []
    for idx, (s_type, s_date, s_price, _) in enumerate(swings):
        if idx == 0:
            a_type = "resistance"
        elif s_type == "H":
            a_type = "contraction_high"
        else:
            a_type = "contraction_low"
        anchors_list.append(
            ChartGeometryAnchor(
                date=s_date,
                price=Decimal(str(s_price)),
                anchor_type=a_type,
            )
        )

    # Determine pivots and stops
    resistance = floor_to_tick(Decimal(str(base_high.high)), tick_size)
    low_swings = [s for s in swings if s[0] == "L"]
    final_low_val = low_swings[-1][2] if low_swings else detail[-1].low
    final_low = Decimal(str(final_low_val))

    cheat_pivot: Decimal | None = None
    cheat_stop: Decimal | None = None
    if len(contractions_list) >= 2:
        # Micro/cheat pivot is the high of the final contraction wave if lower than major resistance
        last_c = contractions_list[-1]
        if last_c.high_price < resistance:
            cheat_pivot = floor_to_tick(last_c.high_price, tick_size)
            cheat_stop = last_c.low_price

    return tuple(anchors_list), tuple(contractions_list), resistance, final_low, cheat_pivot, cheat_stop


def derive_chart_geometry(
    candles: Sequence[CandleData],
    *,
    tick_size: Decimal = DEFAULT_TICK_SIZE,
) -> DeterministicChartGeometry:
    """Derive reproducible chart annotations without model involvement."""
    if len(candles) < 60:
        raise ValueError("Need at least 60 candles to derive chart geometry")
    if any(candle.date is None for candle in candles[-60:]):
        raise ValueError("Dated candles are required to derive chart geometry")

    atr14 = compute_atr14(candles)
    anchors, contractions, resistance, final_low, cheat_pivot, cheat_stop = (
        detect_vcp_swings_and_contractions(candles, tick_size=tick_size)
    )

    initial_stop = calculate_structural_stop(final_low, atr14, tick_size)
    calculated_cheat_stop = (
        calculate_structural_stop(cheat_stop, atr14, tick_size)
        if cheat_stop is not None
        else None
    )

    return DeterministicChartGeometry(
        anchors=anchors,
        resistance=resistance,
        final_contraction_low=final_low,
        initial_stop=initial_stop,
        cheat_pivot=cheat_pivot,
        cheat_stop=calculated_cheat_stop,
        contractions=contractions,
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
    """Base maximum acceptable entry = pivot + min(2% of pivot, 0.5 * (pivot - initial_stop)).

    Returns (base_chase_ceiling, pivot_r_distance). R:R may later shrink this
    ceiling so T1 still provides at least 1R at the worst allowed fill.
    """
    r_distance = pivot - initial_stop
    if r_distance <= 0:
        raise ValueError(f"Pivot ({pivot}) must be greater than initial stop ({initial_stop})")

    cap_pct = pivot * Decimal("0.02")
    cap_r = r_distance * Decimal("0.50")
    max_slippage = min(cap_pct, cap_r)
    raw_ceiling = pivot + max_slippage
    return floor_to_tick(raw_ceiling, tick_size), r_distance


def _r_multiple(target: Decimal, entry: Decimal, stop: Decimal) -> Decimal | None:
    risk = entry - stop
    if risk <= 0:
        return None
    return (target - entry) / risk


def adjust_chase_ceiling_for_t1_rr(
    pivot: Decimal,
    initial_stop: Decimal,
    t1: Decimal,
    base_ceiling: Decimal,
    tick_size: Decimal = DEFAULT_TICK_SIZE,
) -> Decimal:
    """Shrink the base chase ceiling so T1 still provides at least 1R.

    `max_entry_for_1R = (T1 + SL) / 2`. Never raise the ceiling, and never go
    below pivot; a pivot that still fails 1R is rejected by the caller.
    """
    max_entry = floor_to_tick((t1 + initial_stop) / Decimal("2"), tick_size)
    final = min(base_ceiling, max_entry)
    if final < pivot:
        return pivot
    return final


def entry_vwap_invalidates_t1_rr(
    t1: Decimal,
    entry_vwap: Decimal,
    current_stop: Decimal,
) -> bool:
    """True when actual fill VWAP makes T1 provide less than 1R."""
    r_distance = entry_vwap - current_stop
    return r_distance > 0 and (t1 - entry_vwap) < r_distance


def _rr_audit_fields(
    pivot: Decimal,
    initial_stop: Decimal,
    base_ceiling: Decimal,
    final_ceiling: Decimal,
    t1: Decimal,
    t2: Decimal,
    t3: Decimal,
) -> dict[str, Decimal | bool | None]:
    t1_r = _r_multiple(t1, final_ceiling, initial_stop)
    t2_r = _r_multiple(t2, final_ceiling, initial_stop)
    t3_r = _r_multiple(t3, final_ceiling, initial_stop)
    return {
        "base_chase_ceiling": base_ceiling,
        "rr_adjusted_chase_ceiling": final_ceiling,
        "r_at_pivot": _r_multiple(t1, pivot, initial_stop),
        "r_at_base_chase_ceiling": _r_multiple(t1, base_ceiling, initial_stop),
        "final_r_at_chase_ceiling": t1_r,
        "t1_r": t1_r,
        "t2_r": t2_r,
        "t3_r": t3_r,
        "t2_below_2r": t2_r is not None and t2_r < Decimal("2"),
        "t3_below_3r": t3_r is not None and t3_r < Decimal("3"),
    }


def validate_proposal_targets(
    pivot: Decimal,
    initial_stop: Decimal,
    chase_ceiling: Decimal,
    t1: Decimal,
    t2: Decimal,
    t3: Decimal,
    tick_size: Decimal = DEFAULT_TICK_SIZE,
) -> tuple[bool, str | None]:
    """Validate structural targets against the final chase ceiling.

    T1 must provide at least 1R from the (possibly R:R-shrunk) ceiling.
    T2/T3 must be strictly ordered and tick-valid; they are not hard 2R/3R gates.
    """
    r = chase_ceiling - initial_stop
    if r <= 0:
        return False, "Initial stop is at or above pivot price"

    stop_pct = ((pivot - initial_stop) / pivot) * Decimal("100")
    if stop_pct > MAX_STOP_DISTANCE_PCT:
        return False, f"Stop distance {stop_pct:.2f}% exceeds maximum allowable 8.0%"

    if not (pivot <= chase_ceiling < t1 < t2 < t3):
        return False, (
            f"Targets must be strictly ordered: pivot ({pivot}) <= ceiling "
            f"({chase_ceiling}) < T1 ({t1}) < T2 ({t2}) < T3 ({t3})"
        )

    r1 = t1 - chase_ceiling
    if r1 < r:
        return False, f"T1 ({t1}) provides {r1 / r:.2f}R from chase ceiling, requires >= 1.0R"

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

    base_ceiling, _ = calculate_chase_ceiling(pivot, stop, tick_size)
    ceiling = adjust_chase_ceiling_for_t1_rr(
        pivot, stop, dec_t1, base_ceiling, tick_size
    )
    rr_fields = _rr_audit_fields(
        pivot, stop, base_ceiling, ceiling, dec_t1, dec_t2, dec_t3
    )
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
        **rr_fields,
    )
