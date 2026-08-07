"""Versioned CNC charge estimates for journal P&L."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

CHARGE_VERSION = "fyers_cnc_v1"

Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class FillLeg:
    side: Side
    quantity: int
    price: Decimal


@dataclass(frozen=True)
class ChargeBreakdown:
    version: str
    brokerage: Decimal
    stt: Decimal
    exchange_charges: Decimal
    sebi_charges: Decimal
    stamp_duty: Decimal
    gst: Decimal
    dp_charges: Decimal
    total: Decimal
    per_fill: list[dict[str, str | Decimal]]
    label: Literal["estimated"] = "estimated"


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _per_order_brokerage(turnover: Decimal) -> Decimal:
    pct = turnover * Decimal("0.0003")
    return min(Decimal("20"), pct)


def estimate_cnc_charges(fills: list[FillLeg]) -> ChargeBreakdown:
    """
    Estimate FYERS CNC delivery charges from individual fills.

    Uses the published FYERS calculator schedule (v1). Contract notes remain
    authoritative; these are labeled estimated in the journal.
    """
    brokerage = Decimal("0")
    stt = Decimal("0")
    exchange_charges = Decimal("0")
    sebi_charges = Decimal("0")
    stamp_duty = Decimal("0")
    dp_charges = Decimal("0")
    per_fill: list[dict[str, str | Decimal]] = []

    sell_scrips: set[tuple[Side, int]] = set()

    for index, fill in enumerate(fills, start=1):
        turnover = fill.price * fill.quantity
        fill_brokerage = _per_order_brokerage(turnover)
        fill_exchange = turnover * Decimal("0.0000345")
        fill_sebi = turnover * Decimal("0.000001")
        fill_stt = Decimal("0")
        fill_stamp = Decimal("0")
        fill_dp = Decimal("0")

        if fill.side == "sell":
            fill_stt = turnover * Decimal("0.001")
            scrip_key = (fill.side, fill.quantity)
            if scrip_key not in sell_scrips:
                fill_dp = Decimal("15.93")
                sell_scrips.add(scrip_key)
        else:
            fill_stamp = turnover * Decimal("0.00015")

        taxable = fill_brokerage + fill_exchange + fill_sebi
        fill_gst = taxable * Decimal("0.18")
        fill_total = (
            fill_brokerage
            + fill_stt
            + fill_exchange
            + fill_sebi
            + fill_stamp
            + fill_gst
            + fill_dp
        )

        brokerage += fill_brokerage
        stt += fill_stt
        exchange_charges += fill_exchange
        sebi_charges += fill_sebi
        stamp_duty += fill_stamp
        dp_charges += fill_dp

        per_fill.append(
            {
                "fill_index": index,
                "side": fill.side,
                "quantity": fill.quantity,
                "price": fill.price,
                "brokerage": _money(fill_brokerage),
                "stt": _money(fill_stt),
                "exchange_charges": _money(fill_exchange),
                "sebi_charges": _money(fill_sebi),
                "stamp_duty": _money(fill_stamp),
                "gst": _money(fill_gst),
                "dp_charges": _money(fill_dp),
                "total": _money(fill_total),
            }
        )

    gst = (brokerage + exchange_charges + sebi_charges) * Decimal("0.18")
    total = brokerage + stt + exchange_charges + sebi_charges + stamp_duty + gst + dp_charges

    return ChargeBreakdown(
        version=CHARGE_VERSION,
        brokerage=_money(brokerage),
        stt=_money(stt),
        exchange_charges=_money(exchange_charges),
        sebi_charges=_money(sebi_charges),
        stamp_duty=_money(stamp_duty),
        gst=_money(gst),
        dp_charges=_money(dp_charges),
        total=_money(total),
        per_fill=per_fill,
    )


def charges_to_dict(breakdown: ChargeBreakdown) -> dict:
    return {
        "version": breakdown.version,
        "label": breakdown.label,
        "brokerage": str(breakdown.brokerage),
        "stt": str(breakdown.stt),
        "exchange_charges": str(breakdown.exchange_charges),
        "sebi_charges": str(breakdown.sebi_charges),
        "stamp_duty": str(breakdown.stamp_duty),
        "gst": str(breakdown.gst),
        "dp_charges": str(breakdown.dp_charges),
        "total": str(breakdown.total),
        "per_fill": [
            {key: str(value) if isinstance(value, Decimal) else value for key, value in row.items()}
            for row in breakdown.per_fill
        ],
    }
