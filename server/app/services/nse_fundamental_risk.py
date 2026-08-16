"""Best-effort, read-only NSE filing enrichment for personal scanner survivors."""

from __future__ import annotations

import datetime
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx

from app.services.screening_ranker import normalize_industry_key


PARSER_VERSION = "nse_fundamental_risk_v1"
NSE_ARCHIVE_HOST = "nsearchives.nseindia.com"
_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"

# Scaling is explicit per supported taxonomy. NSE's pure ratio facts are
# fractions (for example .0049 represents 0.49), while the scanner displays
# ordinary ratio values and percentages.
INTEGRATED_TAXONOMIES: dict[str, dict[str, tuple[str, float]]] = {
    version: {
        "debt_to_equity": ("DebtEquityRatio", 100.0),
        "interest_service_coverage": ("InterestServiceCoverageRatio", 100.0),
        "debt_service_coverage": ("DebtServiceCoverageRatio", 100.0),
    }
    for version in ("2025-01-31", "2026-01-31")
}
SHAREHOLDING_TAXONOMIES: dict[str, dict[str, tuple[str, float]]] = {
    "2025-10-31": {
        "promoter_shares": ("NumberOfShares", 1.0),
        "pledged_shares": ("NumberOfSharesEncumberedUnderPledged", 1.0),
        "reported_pledge_ratio": (
            "EncumberedShareUnderPledgedAsPercentageOfTotalNumberOfShares",
            100.0,
        ),
    }
}

# Deterministic fundamental-score adjustments. These never change the
# technical VCP score, shortlist eligibility, or automatic trade behavior.
PLEDGE_SCORE_IMPACT = {
    "warning": -3,
    "red": -8,
    "severe": -15,
}
LEVERAGE_SCORE_IMPACT = {
    "warning": -2,
    "red": -5,
    "severe": -10,
}


class NseFundamentalRiskError(RuntimeError):
    """Official NSE filing data could not be fetched or interpreted safely."""


@dataclass(frozen=True)
class XbrlContext:
    context_id: str
    entity: str | None
    kind: str
    start_date: datetime.date | None
    end_date: datetime.date
    dimensions: dict[str, str]


@dataclass(frozen=True)
class XbrlFact:
    concept: str
    context_ref: str
    value: float
    unit_ref: str | None


@dataclass(frozen=True)
class NseSnapshot:
    provider: str
    role: str
    statement_type: str
    source_url: str
    filing_date: datetime.datetime | None
    revision_date: datetime.datetime | None
    reporting_period: datetime.date
    taxonomy_version: str | None
    fetch_status: str
    raw_payload: dict[str, Any]
    normalized_facts: dict[str, Any]
    provider_metadata: dict[str, Any]

    @property
    def content_hash(self) -> str:
        payload = json.dumps(
            self.raw_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class NseEnrichment:
    snapshots: tuple[NseSnapshot, ...] = ()
    risk_checks: dict[str, Any] = field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":", 1)[-1]


def _parse_date(value: object) -> datetime.date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = value.strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(cleaned.title(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_datetime(value: object) -> datetime.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = value.strip()
    for fmt in (
        "%d-%b-%Y %H:%M:%S",
        "%d-%B-%Y %H:%M:%S",
        "%d-%b-%Y",
        "%d-%B-%Y",
    ):
        try:
            parsed = datetime.datetime.strptime(cleaned.title(), fmt)
            return parsed.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
    return None


def _published_at(record: dict[str, Any]) -> datetime.datetime | None:
    for key in (
        "revised_Date",
        "revisionDate",
        "creation_Date",
        "broadcast_Date",
        "broadcastDate",
        "submissionDate",
    ):
        parsed = _parse_datetime(record.get(key))
        if parsed is not None:
            return parsed
    return None


def select_latest_filing(
    records: Iterable[dict[str, Any]],
    *,
    as_of_date: datetime.date,
    period_key: str,
    prefer_consolidated: bool,
) -> dict[str, Any] | None:
    """Select once from the newest available period; callers never fall back."""
    eligible: list[tuple[dict[str, Any], datetime.date]] = []
    for record in records:
        period = _parse_date(record.get(period_key))
        published = _published_at(record)
        if period is None or period > as_of_date:
            continue
        if published is not None and published.date() > as_of_date:
            continue
        eligible.append((record, period))
    if not eligible:
        return None
    latest_period = max(period for _, period in eligible)
    current = [record for record, period in eligible if period == latest_period]

    def preference(record: dict[str, Any]) -> tuple[int, int, datetime.datetime]:
        scope = str(record.get("consolidated") or "").casefold()
        consolidated = int(prefer_consolidated and scope == "consolidated")
        revision = int(
            str(record.get("type_Sub") or record.get("revisedStatus") or "")
            .casefold()
            .startswith("revis")
        )
        published = _published_at(record) or datetime.datetime.min.replace(
            tzinfo=datetime.timezone.utc
        )
        return consolidated, revision, published

    return max(current, key=preference)


def _taxonomy_version(root: ElementTree.Element, prefix: str) -> str | None:
    for element in root.iter():
        if _local_name(element.tag) != "schemaRef":
            continue
        href = element.attrib.get(_XLINK_HREF) or element.attrib.get("href") or ""
        match = re.search(rf"{re.escape(prefix)}-(\d{{4}}-\d{{2}}-\d{{2}})\.xsd$", href)
        if match:
            return match.group(1)
    return None


def build_context_index(root: ElementTree.Element) -> dict[str, XbrlContext]:
    contexts: dict[str, XbrlContext] = {}
    for element in root.iter():
        if _local_name(element.tag) != "context":
            continue
        context_id = element.attrib.get("id")
        if not context_id:
            continue
        entity = None
        instant = None
        start = None
        end = None
        dimensions: dict[str, str] = {}
        for child in element.iter():
            name = _local_name(child.tag)
            text = (child.text or "").strip()
            if name == "identifier" and text:
                entity = text
            elif name == "instant":
                instant = _parse_date(text)
            elif name == "startDate":
                start = _parse_date(text)
            elif name == "endDate":
                end = _parse_date(text)
            elif name in {"explicitMember", "typedMember"}:
                dimension = child.attrib.get("dimension", name)
                dimensions[dimension] = text
        if instant is not None:
            contexts[context_id] = XbrlContext(
                context_id, entity, "instant", None, instant, dimensions
            )
        elif start is not None and end is not None:
            contexts[context_id] = XbrlContext(
                context_id, entity, "duration", start, end, dimensions
            )
    return contexts


def _facts(root: ElementTree.Element, concepts: set[str]) -> list[XbrlFact]:
    found: list[XbrlFact] = []
    for element in root.iter():
        concept = _local_name(element.tag)
        context_ref = element.attrib.get("contextRef")
        if concept not in concepts or not context_ref:
            continue
        try:
            value = float((element.text or "").strip().replace(",", ""))
        except ValueError:
            continue
        if math.isfinite(value):
            found.append(
                XbrlFact(concept, context_ref, value, element.attrib.get("unitRef"))
            )
    return found


def _agree(values: list[float], tolerance: float = 0.01) -> bool:
    return bool(values) and max(values) - min(values) <= tolerance


def parse_integrated_xbrl(
    xml: str,
    *,
    reporting_period: datetime.date,
) -> tuple[dict[str, Any], str | None, str]:
    """Parse leverage facts with explicit instant/duration disambiguation."""
    root = ElementTree.fromstring(xml)
    version = _taxonomy_version(root, "in-capmkt-ent")
    concept_map = INTEGRATED_TAXONOMIES.get(version or "")
    if concept_map is None:
        return (
            {
                "schema_version": "nse_leverage_v1",
                "status": "unknown",
                "reason": "unsupported_taxonomy",
                "reporting_period": reporting_period.isoformat(),
            },
            version,
            "ambiguous",
        )

    contexts = build_context_index(root)
    aliases = {definition[0] for definition in concept_map.values()}
    all_facts = _facts(root, aliases)

    def matching(key: str, kind: str | None = None) -> list[tuple[XbrlFact, XbrlContext, float]]:
        concept, scale = concept_map[key]
        matches: list[tuple[XbrlFact, XbrlContext, float]] = []
        for fact in all_facts:
            context = contexts.get(fact.context_ref)
            if fact.concept != concept or context is None:
                continue
            if context.end_date != reporting_period or (kind and context.kind != kind):
                continue
            matches.append((fact, context, fact.value * scale))
        return matches

    diagnostics: list[str] = []
    debt_equity: float | None = None
    debt_context: str | None = None
    instant_de = matching("debt_to_equity", "instant")
    chosen_de = instant_de or matching("debt_to_equity", "duration")
    de_values = [value for _, _, value in chosen_de]
    if chosen_de and _agree(de_values):
        debt_equity = round(de_values[0], 4)
        debt_context = chosen_de[0][1].kind
    elif chosen_de:
        diagnostics.append("ambiguous_debt_to_equity_contexts")
    else:
        diagnostics.append("missing_debt_to_equity")

    def longest_duration(key: str) -> tuple[float | None, dict[str, Any] | None]:
        matches = matching(key, "duration")
        if not matches:
            diagnostics.append(f"missing_{key}")
            return None, None
        earliest = min(context.start_date for _, context, _ in matches if context.start_date)
        longest = [item for item in matches if item[1].start_date == earliest]
        values = [value for _, _, value in longest]
        if not _agree(values):
            diagnostics.append(f"ambiguous_{key}_contexts")
            return None, None
        return round(values[0], 4), {
            "kind": "duration",
            "start_date": earliest.isoformat(),
            "end_date": reporting_period.isoformat(),
        }

    iscr, iscr_context = longest_duration("interest_service_coverage")
    dscr, dscr_context = longest_duration("debt_service_coverage")
    status = "ok" if not any(item.startswith("ambiguous_") for item in diagnostics) else "ambiguous"
    return (
        {
            "schema_version": "nse_leverage_v1",
            "status": status,
            "reporting_period": reporting_period.isoformat(),
            "debt_to_equity": debt_equity,
            "interest_service_coverage": iscr,
            "debt_service_coverage": dscr,
            "contexts": {
                "debt_to_equity": debt_context,
                "interest_service_coverage": iscr_context,
                "debt_service_coverage": dscr_context,
            },
            "diagnostics": diagnostics,
        },
        version,
        "ambiguous" if status == "ambiguous" else "succeeded",
    )


def parse_shareholding_xbrl(
    xml: str,
    *,
    reporting_period: datetime.date,
) -> tuple[dict[str, Any], str | None, str]:
    """Compute pledge strictly against promoter-group shares held."""
    root = ElementTree.fromstring(xml)
    version = _taxonomy_version(root, "in-bse-shp")
    concept_map = SHAREHOLDING_TAXONOMIES.get(version or "")
    if concept_map is None:
        return (
            {
                "schema_version": "nse_promoter_pledge_v1",
                "status": "unknown",
                "reason": "unsupported_taxonomy",
                "reporting_period": reporting_period.isoformat(),
            },
            version,
            "ambiguous",
        )
    contexts = build_context_index(root)
    aliases = {definition[0] for definition in concept_map.values()}
    all_facts = _facts(root, aliases)
    promoter_context_ids = {
        context_id
        for context_id, context in contexts.items()
        if context.end_date == reporting_period
        and "shareholdingofpromoterandpromotergroup" in context_id.casefold()
    }

    def values(key: str) -> list[float]:
        concept, scale = concept_map[key]
        return [
            fact.value * scale
            for fact in all_facts
            if fact.concept == concept and fact.context_ref in promoter_context_ids
        ]

    promoter_values = values("promoter_shares")
    pledged_values = values("pledged_shares")
    reported_values = values("reported_pledge_ratio")
    explicit_no_pledge = any(
        _local_name(element.tag)
        == "WhetherAnySharesHeldByPromotersAreEncumberedUnderPledgedForPromoterAndPromoterGroup"
        and (element.text or "").strip().casefold() == "false"
        for element in root.iter()
    )
    diagnostics: list[str] = []
    promoter_shares = promoter_values[0] if _agree(promoter_values, 0.5) else None
    if promoter_values and promoter_shares is None:
        diagnostics.append("ambiguous_promoter_share_denominator")
    pledged_shares = (
        pledged_values[0]
        if _agree(pledged_values, 0.5)
        else 0.0
        if explicit_no_pledge
        else None
    )
    if pledged_values and pledged_shares is None:
        diagnostics.append("ambiguous_pledged_share_numerator")

    ratio = None
    if promoter_shares is None or promoter_shares <= 0:
        diagnostics.append("missing_or_zero_promoter_share_denominator")
    elif pledged_shares is None:
        diagnostics.append("missing_pledged_share_numerator")
    else:
        ratio = 100.0 * pledged_shares / promoter_shares

    reported_ratio = reported_values[0] if _agree(reported_values) else None
    if reported_values and reported_ratio is None:
        diagnostics.append("ambiguous_reported_pledge_ratio")
    if ratio is not None and reported_ratio is not None and abs(ratio - reported_ratio) > 0.05:
        diagnostics.append("reported_pledge_ratio_mismatch")
        ratio = None

    status = "ok" if ratio is not None and not diagnostics else "ambiguous"
    return (
        {
            "schema_version": "nse_promoter_pledge_v1",
            "status": status,
            "reporting_period": reporting_period.isoformat(),
            "pledged_promoter_shares": pledged_shares,
            "total_promoter_group_shares": promoter_shares,
            "pledged_pct_of_promoter_holding": round(ratio, 4) if ratio is not None else None,
            "reported_pct_of_promoter_holding": reported_ratio,
            "denominator": "total_promoter_group_shares",
            "diagnostics": diagnostics,
        },
        version,
        "succeeded" if status == "ok" else "ambiguous",
    )


def promoter_pledge_risk(facts: dict[str, Any] | None) -> dict[str, Any]:
    ratio = (facts or {}).get("pledged_pct_of_promoter_holding")
    if not isinstance(ratio, (int, float)) or not math.isfinite(float(ratio)):
        severity = "unknown"
    elif ratio >= 20:
        severity = "severe"
    elif ratio >= 10:
        severity = "red"
    elif ratio > 0:
        severity = "warning"
    else:
        severity = "ok"
    return {
        "status": severity,
        "value": ratio,
        "unit": "percent_of_promoter_group_holding",
        "score_impact": PLEDGE_SCORE_IMPACT.get(severity, 0),
        "automatic_rejection": False,
        "source": "nse_shareholding_xbrl",
        "details": facts or {},
    }


def is_financial_industry(industry: object, *, symbol: str = "unknown") -> bool:
    key = normalize_industry_key(industry, symbol=symbol)
    markers = ("bank", "financial services", "finance", "nbfc", "insurance")
    return not key.startswith("unknown:") and any(marker in key for marker in markers)


def leverage_risk(
    facts: dict[str, Any] | None,
    *,
    industry: object,
    symbol: str = "unknown",
) -> dict[str, Any]:
    if is_financial_industry(industry, symbol=symbol):
        return {
            "status": "not_applicable",
            "score_impact": 0,
            "automatic_rejection": False,
            "source": "nse_integrated_xbrl",
            "details": facts or {},
        }
    debt_equity = (facts or {}).get("debt_to_equity")
    iscr = (facts or {}).get("interest_service_coverage")
    debt_equity = float(debt_equity) if isinstance(debt_equity, (int, float)) and debt_equity > 0 else None
    iscr = float(iscr) if isinstance(iscr, (int, float)) and iscr > 0 else None
    severity = "unknown" if debt_equity is None and iscr is None else "ok"
    if (debt_equity is not None and debt_equity > 3) or (iscr is not None and iscr < 1.5):
        severity = "severe"
    elif (debt_equity is not None and debt_equity > 2) or (iscr is not None and iscr < 2):
        severity = "red"
    elif (debt_equity is not None and debt_equity > 1) or (iscr is not None and iscr < 3):
        severity = "warning"
    return {
        "status": severity,
        "debt_to_equity": debt_equity,
        "interest_service_coverage": iscr,
        "debt_service_coverage": (facts or {}).get("debt_service_coverage"),
        "score_impact": LEVERAGE_SCORE_IMPACT.get(severity, 0),
        "automatic_rejection": False,
        "source": "nse_integrated_xbrl",
        "details": facts or {},
    }


class NseCorporateFilingsClient:
    """GET-only client for NSE filing indexes and their official archive XBRLs."""

    def __init__(
        self,
        *,
        base_url: str = "https://www.nseindia.com",
        timeout_seconds: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; SwingTraderVCP/1.0)",
                "Accept": "application/json,text/xml;q=0.9,*/*;q=0.8",
                "Referer": f"{self._base_url}/companies-listing/corporate-filings-shareholding-pattern",
            },
        )

    async def __aenter__(self) -> "NseCorporateFilingsClient":
        try:
            await self._client.get(f"{self._base_url}/")
        except httpx.HTTPError:
            # The two filing requests remain independently best-effort and will
            # surface their own diagnostics; session warm-up must not fail P7.
            pass
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _json(self, path: str, *, params: dict[str, Any]) -> Any:
        response = await self._client.get(f"{self._base_url}{path}", params=params)
        response.raise_for_status()
        return response.json()

    async def _xml(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != NSE_ARCHIVE_HOST:
            raise NseFundamentalRiskError("NSE filing returned an untrusted XBRL URL")
        response = await self._client.get(url)
        response.raise_for_status()
        return response.text

    async def fetch(self, *, symbol: str, as_of_date: datetime.date, industry: object) -> NseEnrichment:
        snapshots: list[NseSnapshot] = []
        diagnostics: list[str] = []
        pledge_facts: dict[str, Any] | None = None
        leverage_facts: dict[str, Any] | None = None

        try:
            payload = await self._json(
                "/api/corporate-share-holdings-master",
                params={"index": "equities", "symbol": symbol},
            )
            records = payload if isinstance(payload, list) else []
            record = select_latest_filing(
                records,
                as_of_date=as_of_date,
                period_key="date",
                prefer_consolidated=False,
            )
            if record is None:
                diagnostics.append("shareholding_filing_missing")
            else:
                snapshot, pledge_facts = await self._shareholding_snapshot(record)
                snapshots.append(snapshot)
        except (httpx.HTTPError, ValueError, ElementTree.ParseError, NseFundamentalRiskError) as exc:
            diagnostics.append(f"shareholding:{type(exc).__name__}:{str(exc)[:200]}")

        try:
            payload = await self._json(
                "/api/integrated-filing-results",
                params={
                    "index": "equities",
                    "symbol": symbol,
                    "type": "Integrated Filing- Financials",
                    "page": 1,
                    "pageSize": 100,
                },
            )
            records = payload.get("data", []) if isinstance(payload, dict) else []
            record = select_latest_filing(
                records,
                as_of_date=as_of_date,
                period_key="qe_Date",
                prefer_consolidated=True,
            )
            if record is None:
                diagnostics.append("integrated_filing_missing")
            else:
                snapshot, leverage_facts = await self._integrated_snapshot(record)
                snapshots.append(snapshot)
        except (httpx.HTTPError, ValueError, ElementTree.ParseError, NseFundamentalRiskError) as exc:
            diagnostics.append(f"integrated:{type(exc).__name__}:{str(exc)[:200]}")

        return NseEnrichment(
            snapshots=tuple(snapshots),
            risk_checks={
                "promoter_pledge": promoter_pledge_risk(pledge_facts),
                "leverage": leverage_risk(
                    leverage_facts,
                    industry=industry,
                    symbol=symbol,
                ),
            },
            diagnostics=tuple(diagnostics),
        )

    async def _shareholding_snapshot(self, record: dict[str, Any]) -> tuple[NseSnapshot, dict[str, Any]]:
        period = _parse_date(record.get("date"))
        url = record.get("xbrl")
        if period is None or not isinstance(url, str):
            raise NseFundamentalRiskError("Shareholding filing lacks period or XBRL URL")
        xml = await self._xml(url)
        facts, taxonomy, status = parse_shareholding_xbrl(xml, reporting_period=period)
        return (
            NseSnapshot(
                provider="nse_shareholding_xbrl",
                role="promoter_pledge",
                statement_type="not_applicable",
                source_url=url,
                filing_date=_published_at(record),
                revision_date=_parse_datetime(record.get("revisionDate")),
                reporting_period=period,
                taxonomy_version=taxonomy,
                fetch_status=status,
                raw_payload={"index_record": record, "xbrl_xml": xml},
                normalized_facts=facts,
                provider_metadata={"record_id": record.get("recordId")},
            ),
            facts,
        )

    async def _integrated_snapshot(self, record: dict[str, Any]) -> tuple[NseSnapshot, dict[str, Any]]:
        period = _parse_date(record.get("qe_Date"))
        url = record.get("xbrl")
        if period is None or not isinstance(url, str):
            raise NseFundamentalRiskError("Integrated filing lacks period or XBRL URL")
        xml = await self._xml(url)
        facts, taxonomy, status = parse_integrated_xbrl(xml, reporting_period=period)
        scope = str(record.get("consolidated") or "standalone").casefold()
        statement_type = "consolidated" if scope == "consolidated" else "standalone"
        return (
            NseSnapshot(
                provider="nse_integrated_xbrl",
                role="leverage",
                statement_type=statement_type,
                source_url=url,
                filing_date=_published_at(record),
                revision_date=_parse_datetime(record.get("revised_Date")),
                reporting_period=period,
                taxonomy_version=taxonomy,
                fetch_status=status,
                raw_payload={"index_record": record, "xbrl_xml": xml},
                normalized_facts=facts,
                provider_metadata={
                    "sequence_id": record.get("seq_Id"),
                    "filing_type": record.get("type_Sub"),
                    "audited": record.get("audited"),
                },
            ),
            facts,
        )
