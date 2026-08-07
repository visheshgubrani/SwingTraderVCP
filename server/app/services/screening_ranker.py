from typing import Any


def fundamental_selection_status(
    result_rank: int,
    *,
    limit: int,
    enabled: bool,
) -> tuple[bool, str]:
    selected = enabled and result_rank <= limit
    return selected, "queued" if selected else "not_requested"


def rank_and_cap_shortlist(
    candidates: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Rank scored setups deterministically and retain at most ``limit``."""
    ordered = sorted(
        candidates,
        key=lambda candidate: (
            -candidate["technical_score"],
            -candidate["rs_rating"],
            candidate["pct_from_52w_high"],
            candidate["symbol"],
        ),
    )
    return [
        {**candidate, "result_rank": rank}
        for rank, candidate in enumerate(ordered[:limit], start=1)
    ]
