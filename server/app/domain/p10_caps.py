"""P10 Caps, Risk Constraints & Capacity Priority Solver.

Deterministic Python evaluation of portfolio risk, name/sector/cluster caps,
daily loss limits, and 2-point score band priority ranking according to AGENTS.md §6.3 & §6.4.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence


@dataclass(frozen=True)
class RiskPolicyConfig:
    version: int
    name: str
    risk_per_trade_pct: Decimal = Decimal("0.0100")  # 1%
    max_total_open_risk_pct: Decimal = Decimal("0.0400")  # 4%
    max_single_name_notional_pct: Decimal = Decimal("0.1500")  # 15%
    max_sector_notional_pct: Decimal = Decimal("0.3000")  # 30%
    max_cluster_notional_pct: Decimal = Decimal("0.3000")  # 30%
    correlation_cluster_threshold: Decimal = Decimal("0.80")
    correlation_lookback_sessions: int = 60
    daily_loss_limit_pct: Decimal = Decimal("0.0200")  # 2%
    max_open_positions: int = 8
    deployable_capital_override: Decimal | None = None


@dataclass(frozen=True)
class PortfolioState:
    deployable_capital: Decimal
    current_open_risk: Decimal
    current_open_positions_count: int
    daily_realized_losses: Decimal  # Sum of negative realized P&L + charges today
    existing_name_notional: Decimal  # Existing notional in this specific symbol
    existing_sector_notional: Decimal  # Existing notional in this sector
    existing_cluster_notional: Decimal  # Existing notional in correlated cluster (rho >= 0.80)


@dataclass(frozen=True)
class CapCheckResult:
    allowed_risk_budget: Decimal
    allowed_notional_budget: Decimal
    is_blocked: bool
    blocking_reason: str | None = None


@dataclass(frozen=True)
class CompetingCandidate:
    candidate_id: str
    symbol: str
    scanner_score: Decimal
    gemini_confidence: Decimal
    conservative_rr: Decimal
    trigger_timestamp: dt.datetime
    requested_risk: Decimal
    requested_notional: Decimal


@dataclass(frozen=True)
class CandidatePriorityResult:
    ranked_candidates: list[CompetingCandidate]
    has_capacity_conflict: bool
    conflict_candidate_ids: list[str]


def correlation_cluster_members(
    returns_by_symbol: dict[str, Sequence[float]],
    *,
    candidate_symbol: str,
    threshold: Decimal,
    lookback_sessions: int,
) -> set[str]:
    """Return the transitive rho cluster containing ``candidate_symbol``.

    Missing or non-finite history fails closed instead of being interpreted as
    zero correlation.
    """
    if candidate_symbol not in returns_by_symbol:
        raise ValueError(f"Missing correlation history for {candidate_symbol}")
    if lookback_sessions < 2:
        raise ValueError("Correlation lookback must be at least two sessions")

    normalized: dict[str, list[float]] = {}
    for symbol, values in returns_by_symbol.items():
        series = [float(value) for value in values]
        if len(series) < lookback_sessions:
            raise ValueError(f"Insufficient correlation history for {symbol}")
        series = series[-lookback_sessions:]
        if not all(math.isfinite(value) for value in series):
            raise ValueError(f"Non-finite correlation history for {symbol}")
        normalized[symbol] = series

    def pearson(left: Sequence[float], right: Sequence[float]) -> float:
        left_mean = sum(left) / len(left)
        right_mean = sum(right) / len(right)
        left_delta = [value - left_mean for value in left]
        right_delta = [value - right_mean for value in right]
        denominator = math.sqrt(
            sum(value * value for value in left_delta)
            * sum(value * value for value in right_delta)
        )
        if denominator == 0:
            raise ValueError("Constant return series cannot form a correlation cluster")
        return sum(a * b for a, b in zip(left_delta, right_delta)) / denominator

    adjacency = {symbol: set() for symbol in normalized}
    symbols = sorted(normalized)
    for index, left in enumerate(symbols):
        for right in symbols[index + 1:]:
            if Decimal(str(pearson(normalized[left], normalized[right]))) >= threshold:
                adjacency[left].add(right)
                adjacency[right].add(left)

    cluster: set[str] = set()
    stack = [candidate_symbol]
    while stack:
        symbol = stack.pop()
        if symbol in cluster:
            continue
        cluster.add(symbol)
        stack.extend(adjacency[symbol] - cluster)
    return cluster


def evaluate_portfolio_caps(
    policy: RiskPolicyConfig,
    state: PortfolioState,
    symbol: str,
    is_new_position: bool,
) -> CapCheckResult:
    """Evaluates portfolio risk caps and returns the maximum allowed risk & notional budget."""
    cap = state.deployable_capital
    if cap <= 0:
        return CapCheckResult(
            allowed_risk_budget=Decimal("0"),
            allowed_notional_budget=Decimal("0"),
            is_blocked=True,
            blocking_reason="Deployable capital is zero or negative",
        )

    # 1. Daily realized loss stop check (2%)
    max_daily_loss = cap * policy.daily_loss_limit_pct
    if state.daily_realized_losses >= max_daily_loss:
        return CapCheckResult(
            allowed_risk_budget=Decimal("0"),
            allowed_notional_budget=Decimal("0"),
            is_blocked=True,
            blocking_reason=f"Daily realized loss ({state.daily_realized_losses:.2f}) reached or exceeded {policy.daily_loss_limit_pct * 100:.1f}% limit ({max_daily_loss:.2f})",
        )

    # 2. Open position count check
    if is_new_position and state.current_open_positions_count >= policy.max_open_positions:
        return CapCheckResult(
            allowed_risk_budget=Decimal("0"),
            allowed_notional_budget=Decimal("0"),
            is_blocked=True,
            blocking_reason=f"Maximum open positions ({policy.max_open_positions}) reached",
        )

    # 3. Total open risk headroom check (4%)
    max_open_risk = cap * policy.max_total_open_risk_pct
    risk_headroom = max(Decimal("0"), max_open_risk - state.current_open_risk)
    if risk_headroom <= 0:
        return CapCheckResult(
            allowed_risk_budget=Decimal("0"),
            allowed_notional_budget=Decimal("0"),
            is_blocked=True,
            blocking_reason=f"Total open risk ({state.current_open_risk:.2f}) reached or exceeded limit ({max_open_risk:.2f})",
        )

    # 4. Per-trade risk budget (1%)
    max_trade_risk = cap * policy.risk_per_trade_pct
    allowed_risk = min(max_trade_risk, risk_headroom)

    # 5. Notional concentration headroom checks
    # Single-name (15%)
    max_name_notional = cap * policy.max_single_name_notional_pct
    name_headroom = max(Decimal("0"), max_name_notional - state.existing_name_notional)
    if name_headroom <= 0:
        return CapCheckResult(
            allowed_risk_budget=Decimal("0"),
            allowed_notional_budget=Decimal("0"),
            is_blocked=True,
            blocking_reason=f"Single-name notional ({state.existing_name_notional:.2f}) reached limit ({max_name_notional:.2f}) for {symbol}",
        )

    # Sector (30%)
    max_sector_notional = cap * policy.max_sector_notional_pct
    sector_headroom = max(Decimal("0"), max_sector_notional - state.existing_sector_notional)
    if sector_headroom <= 0:
        return CapCheckResult(
            allowed_risk_budget=Decimal("0"),
            allowed_notional_budget=Decimal("0"),
            is_blocked=True,
            blocking_reason=f"Sector notional ({state.existing_sector_notional:.2f}) reached limit ({max_sector_notional:.2f})",
        )

    # Correlation Cluster (30%)
    max_cluster_notional = cap * policy.max_cluster_notional_pct
    cluster_headroom = max(Decimal("0"), max_cluster_notional - state.existing_cluster_notional)
    if cluster_headroom <= 0:
        return CapCheckResult(
            allowed_risk_budget=Decimal("0"),
            allowed_notional_budget=Decimal("0"),
            is_blocked=True,
            blocking_reason=f"Correlation-cluster notional ({state.existing_cluster_notional:.2f}) reached limit ({max_cluster_notional:.2f})",
        )

    allowed_notional = min(name_headroom, sector_headroom, cluster_headroom)

    return CapCheckResult(
        allowed_risk_budget=allowed_risk,
        allowed_notional_budget=allowed_notional,
        is_blocked=False,
        blocking_reason=None,
    )


def sort_competing_candidates(
    candidates: Sequence[CompetingCandidate],
) -> CandidatePriorityResult:
    """Ranks competing triggered candidates:
    1. Descending scanner-score bands (2-point bands from highest score).
    2. Within band: conservative R:R DESC, then trigger timestamp ASC.
    3. Exact ties on all 4 fields produce a capacity_conflict requiring operator choice.
    """
    if not candidates:
        return CandidatePriorityResult([], False, [])

    if len(candidates) == 1:
        return CandidatePriorityResult([candidates[0]], False, [])

    # Build score bands iteratively from the highest still-unassigned score.
    # The lower boundary is inclusive: with a top score of 95, 93 belongs to
    # the same two-point band.  The next band's anchor is then the highest
    # remaining score rather than a fixed global grid.
    band_by_id: dict[str, int] = {}
    remaining = sorted(candidates, key=lambda item: item.scanner_score, reverse=True)
    band_index = 0
    while remaining:
        band_top = remaining[0].scanner_score
        in_band = [
            candidate
            for candidate in remaining
            if candidate.scanner_score >= band_top - Decimal("2.0")
        ]
        for candidate in in_band:
            band_by_id[candidate.candidate_id] = band_index
        assigned = {candidate.candidate_id for candidate in in_band}
        remaining = [
            candidate for candidate in remaining
            if candidate.candidate_id not in assigned
        ]
        band_index += 1

    def sort_key(c: CompetingCandidate):
        # Sort by: band_index ASC, rr DESC (-), timestamp ASC (+)
        return (
            band_by_id[c.candidate_id],
            -c.conservative_rr,
            c.trigger_timestamp,
        )

    sorted_list = sorted(candidates, key=sort_key)

    # Only the first unresolved tie matters. Candidates ranked below it cannot
    # consume capacity until that branch is resolved.
    conflict_ids: list[str] = []
    for i in range(len(sorted_list) - 1):
        c1 = sorted_list[i]
        c2 = sorted_list[i + 1]
        
        same_band = band_by_id[c1.candidate_id] == band_by_id[c2.candidate_id]
        
        exact_tie = (
            same_band
            and c1.conservative_rr == c2.conservative_rr
            and c1.trigger_timestamp == c2.trigger_timestamp
        )
        if exact_tie:
            tie_key = sort_key(c1)
            conflict_ids = [
                candidate.candidate_id
                for candidate in sorted_list
                if sort_key(candidate) == tie_key
            ]
            break

    return CandidatePriorityResult(
        ranked_candidates=sorted_list,
        has_capacity_conflict=len(conflict_ids) > 0,
        conflict_candidate_ids=conflict_ids,
    )
