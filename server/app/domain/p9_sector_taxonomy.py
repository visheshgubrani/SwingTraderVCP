"""Checked-in P9 mapping from NSE Nifty 500 industries to 16 index contexts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


TAXONOMY_VERSION = "nifty_sector_taxonomy_v1"


@dataclass(frozen=True)
class SectorDefinition:
    code: str
    name: str
    fyers_symbol: str


SECTORS: tuple[SectorDefinition, ...] = (
    SectorDefinition("auto", "Nifty Auto", "NSE:NIFTYAUTO-INDEX"),
    SectorDefinition("bank", "Nifty Bank", "NSE:NIFTYBANK-INDEX"),
    SectorDefinition("chemicals", "Nifty Chemicals", "NSE:NIFTYCHEMICALS-INDEX"),
    SectorDefinition("consumer_durables", "Nifty Consumer Durables", "NSE:NIFTYCONSRDURBL-INDEX"),
    SectorDefinition("financial_services", "Nifty Financial Services", "NSE:FINNIFTY-INDEX"),
    SectorDefinition("fmcg", "Nifty FMCG", "NSE:NIFTYFMCG-INDEX"),
    SectorDefinition("healthcare", "Nifty Healthcare", "NSE:NIFTYHEALTHCARE-INDEX"),
    SectorDefinition("it", "Nifty IT", "NSE:NIFTYIT-INDEX"),
    SectorDefinition("media", "Nifty Media", "NSE:NIFTYMEDIA-INDEX"),
    SectorDefinition("metal", "Nifty Metal", "NSE:NIFTYMETAL-INDEX"),
    SectorDefinition("oil_gas", "Nifty Oil & Gas", "NSE:NIFTYOILANDGAS-INDEX"),
    SectorDefinition("pharma", "Nifty Pharma", "NSE:NIFTYPHARMA-INDEX"),
    SectorDefinition("power", "Nifty Power", "NSE:NIFTYENERGY-INDEX"),
    SectorDefinition("realty", "Nifty Realty", "NSE:NIFTYREALTY-INDEX"),
    SectorDefinition("infrastructure", "Nifty Infrastructure", "NSE:NIFTYINFRA-INDEX"),
    SectorDefinition("services", "Nifty Services Sector", "NSE:NIFTYSERVSECTOR-INDEX"),
)


INDUSTRY_TO_SECTOR: dict[str, str] = {
    "Automobile and Auto Components": "auto",
    "Capital Goods": "infrastructure",
    "Chemicals": "chemicals",
    "Construction": "infrastructure",
    "Construction Materials": "infrastructure",
    "Consumer Durables": "consumer_durables",
    "Consumer Services": "services",
    "Diversified": "services",
    "Fast Moving Consumer Goods": "fmcg",
    "Financial Services": "financial_services",
    "Healthcare": "healthcare",
    "Information Technology": "it",
    "Media Entertainment & Publication": "media",
    "Metals & Mining": "metal",
    "Oil Gas & Consumable Fuels": "oil_gas",
    "Power": "power",
    "Realty": "realty",
    "Services": "services",
    "Telecommunication": "services",
    "Textiles": "consumer_durables",
}


SECTOR_BY_CODE = {sector.code: sector for sector in SECTORS}


def sector_for_industry(industry: object) -> SectorDefinition | None:
    if not isinstance(industry, str):
        return None
    code = INDUSTRY_TO_SECTOR.get(industry.strip())
    return SECTOR_BY_CODE.get(code) if code else None


def annotate_sector(
    industry: object,
    *,
    run_complete: bool = False,
    result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Map NSE industry to a sector code even when P9 strength is unavailable.

    Gate/RS fields stay unavailable unless a complete 16-sector run produced a
    result row. Incomplete rankings must not be treated as leading/lagging.
    """
    unavailable = {
        "sector_code": None,
        "sector_tier": "unavailable",
        "sector_gate_tier": "unavailable",
        "sector_rs_rating": None,
        "sector_strength_result_id": None,
    }
    sector = sector_for_industry(industry)
    if sector is None:
        return unavailable
    if not run_complete or result is None:
        return {**unavailable, "sector_code": sector.code}
    return {
        "sector_code": sector.code,
        "sector_tier": result.get("raw_tier") or "unavailable",
        "sector_gate_tier": result.get("gate_tier") or "unavailable",
        "sector_rs_rating": result.get("rs_rating"),
        "sector_strength_result_id": result.get("id"),
    }


def validate_taxonomy() -> None:
    if len(SECTORS) != 16 or len(SECTOR_BY_CODE) != 16:
        raise RuntimeError("P9 taxonomy must contain exactly 16 unique sectors")
    unknown = set(INDUSTRY_TO_SECTOR.values()) - set(SECTOR_BY_CODE)
    if unknown:
        raise RuntimeError(f"P9 industry mapping references unknown sectors: {sorted(unknown)}")


validate_taxonomy()
