"""Pure Fyers-symbol → Upstox instrument-key mapping.

`fyers_symbol` stays the canonical internal key (Redis LTP keys, 5m bars,
tick subscriptions). Only the Upstox market-data adapter translates at the
edge. Equities derive from ISIN; indices have no ISIN and use names verified
against the live Upstox LTP echo (`instrument_token`) on 2026-10-25.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

UPSTOX_INDEX_KEYS: Mapping[str, str] = {
    "NSE:NIFTY50-INDEX": "NSE_INDEX|Nifty 50",
    "NSE:NIFTY500-INDEX": "NSE_INDEX|Nifty 500",
    "NSE:NIFTYMIDCAP150-INDEX": "NSE_INDEX|NIFTY MIDCAP 150",
    "NSE:NIFTYBANK-INDEX": "NSE_INDEX|Nifty Bank",
    "NSE:FINNIFTY-INDEX": "NSE_INDEX|Nifty Fin Service",
    "NSE:NIFTYIT-INDEX": "NSE_INDEX|Nifty IT",
    "NSE:NIFTYAUTO-INDEX": "NSE_INDEX|Nifty Auto",
    "NSE:NIFTYFMCG-INDEX": "NSE_INDEX|Nifty FMCG",
    "NSE:NIFTYPHARMA-INDEX": "NSE_INDEX|Nifty Pharma",
    "NSE:NIFTYMETAL-INDEX": "NSE_INDEX|Nifty Metal",
    "NSE:NIFTYREALTY-INDEX": "NSE_INDEX|Nifty Realty",
    "NSE:NIFTYENERGY-INDEX": "NSE_INDEX|Nifty Energy",
    "NSE:NIFTYMEDIA-INDEX": "NSE_INDEX|Nifty Media",
    "NSE:NIFTYINFRA-INDEX": "NSE_INDEX|Nifty Infra",
    "NSE:NIFTYHEALTHCARE-INDEX": "NSE_INDEX|NIFTY HEALTHCARE",
    "NSE:NIFTYOILANDGAS-INDEX": "NSE_INDEX|NIFTY OIL AND GAS",
    "NSE:NIFTYCONSRDURBL-INDEX": "NSE_INDEX|NIFTY CONSR DURBL",
    "NSE:NIFTYCHEMICALS-INDEX": "NSE_INDEX|Nifty Chemicals",
    "NSE:NIFTYSERVSECTOR-INDEX": "NSE_INDEX|Nifty Serv Sector",
}

_ISIN = re.compile(r"^IN[A-Z0-9]{9}[0-9]$")


def equity_instrument_key(isin: str | None) -> str | None:
    if not isin or not _ISIN.match(isin.strip().upper()):
        return None
    return f"NSE_EQ|{isin.strip().upper()}"


def resolve_instrument_key(
    fyers_symbol: str,
    isin: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> str | None:
    """Metadata override → verified index map → ISIN-derived equity key."""
    override = (metadata or {}).get("upstox_instrument_key")
    if isinstance(override, str) and "|" in override:
        return override
    if fyers_symbol in UPSTOX_INDEX_KEYS:
        return UPSTOX_INDEX_KEYS[fyers_symbol]
    if fyers_symbol.endswith("-INDEX"):
        return None
    return equity_instrument_key(isin)


def encode_key(key: str) -> str:
    """URL-path encoding: `|` → %7C, spaces → %20."""
    return quote(key, safe="")
