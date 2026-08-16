import re
import unicodedata
from collections import Counter
from typing import Any


def fundamental_selection_status(
    result_rank: int,
    *,
    limit: int,
    enabled: bool,
) -> tuple[bool, str]:
    selected = enabled and result_rank <= limit
    return selected, "queued" if selected else "not_requested"


def normalize_industry_key(industry: object, *, symbol: str) -> str:
    """Return a stable cap key while retaining the original label for display."""
    if not isinstance(industry, str) or not industry.strip():
        return f"unknown:{symbol.strip().casefold()}"
    normalized = unicodedata.normalize("NFKC", industry).strip().casefold()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\s*([-\u2010-\u2015/&])\s*", r"\1", normalized)
    return normalized


def apply_fundamental_industry_cap(
    ranked: list[dict[str, Any]],
    *,
    limit: int,
    industry_cap: int,
    enabled: bool,
) -> list[dict[str, Any]]:
    """Annotate ranked results with a separate, industry-capped P7 order."""
    counts: Counter[str] = Counter()
    selection_rank = 0
    annotated: list[dict[str, Any]] = []
    for candidate in ranked:
        item = dict(candidate)
        key = normalize_industry_key(
            item.get("industry"),
            symbol=str(item.get("symbol") or "missing"),
        )
        selected = False
        reason: str | None = None
        if not enabled or limit <= 0:
            reason = "fundamentals_disabled"
        elif selection_rank >= limit:
            reason = "fundamental_limit_reached"
        elif counts[key] >= industry_cap:
            reason = "industry_cap_reached"
        else:
            selected = True
            selection_rank += 1
            counts[key] += 1

        item["industry_key"] = key
        item["fundamental_selected"] = selected
        item["fundamental_selection_rank"] = selection_rank if selected else None
        item["fundamental_cap_exclusion_reason"] = reason
        item["llm_status"] = "queued" if selected else "not_requested"
        annotated.append(item)
    return annotated


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
