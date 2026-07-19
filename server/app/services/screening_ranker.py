from typing import Any


def rank_and_cap_shortlist(
    candidates: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Rank by RS descending, then pivot proximity, and retain at most ``limit``."""
    ordered = sorted(
        candidates,
        key=lambda candidate: (
            -candidate["rs_rating"],
            candidate["pct_from_52w_high"],
        ),
    )
    return [
        {**candidate, "result_rank": rank}
        for rank, candidate in enumerate(ordered[:limit], start=1)
    ]
