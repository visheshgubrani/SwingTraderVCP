from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Literal


class TradeValidationError(ValueError):
    """Raised when a human trade plan violates broker/domain constraints."""


def is_tick_aligned(price: Decimal, tick_size: Decimal) -> bool:
    if tick_size <= 0:
        raise ValueError("tick_size must be positive")
    return price % tick_size == 0


def validate_trade_plan(
    *,
    side: Literal["buy", "sell"],
    quantity: int,
    lot_size: int,
    tick_size: Decimal,
    planned_entry_price: Decimal,
    entry_order_type: str,
    entry_limit_price: Decimal | None,
    initial_stop_loss: Decimal,
    initial_target: Decimal | None,
) -> None:
    if lot_size <= 0:
        raise TradeValidationError("Instrument lot size is invalid.")
    if quantity <= 0 or quantity % lot_size != 0:
        raise TradeValidationError(
            f"Quantity must be a positive multiple of the lot size ({lot_size})."
        )

    prices = {
        "planned entry price": planned_entry_price,
        "stop loss": initial_stop_loss,
        "target": initial_target,
    }
    if entry_limit_price is not None:
        prices["limit price"] = entry_limit_price

    for label, price in prices.items():
        if price is None:
            continue
        if price <= 0:
            raise TradeValidationError(f"{label.capitalize()} must be positive.")
        if not is_tick_aligned(price, tick_size):
            raise TradeValidationError(
                f"{label.capitalize()} must align to tick size {tick_size}."
            )

    if entry_order_type == "limit":
        if entry_limit_price is None:
            raise TradeValidationError("A limit order requires entry_limit_price.")
        if entry_limit_price != planned_entry_price:
            raise TradeValidationError(
                "For a limit order, planned_entry_price must equal entry_limit_price."
            )
    elif entry_order_type == "market":
        if entry_limit_price is not None:
            raise TradeValidationError(
                "A market order cannot include entry_limit_price."
            )
    else:
        raise TradeValidationError(
            "Entry supports only market and limit order types."
        )

    if side == "buy":
        if initial_stop_loss >= planned_entry_price:
            raise TradeValidationError(
                "A buy instruction requires stop loss below planned entry."
            )
        if initial_target is not None and initial_target <= planned_entry_price:
            raise TradeValidationError(
                "A buy instruction requires target above planned entry."
            )
    else:
        if initial_stop_loss <= planned_entry_price:
            raise TradeValidationError(
                "A sell instruction requires stop loss above planned entry."
            )
        if initial_target is not None and initial_target >= planned_entry_price:
            raise TradeValidationError(
                "A sell instruction requires target below planned entry."
            )


PositionSide = Literal["long", "short"]
ExitIntentType = Literal[
    "stop_loss_exit",
    "target_exit",
    "trailing_exit",
    "risk_reduction_exit",
    "invalid_fill_exit",
]


@dataclass(frozen=True)
class ExitSignal:
    intent_type: ExitIntentType
    trigger_price: Decimal


def snap_to_tick(
    price: Decimal,
    tick_size: Decimal,
    *,
    side: PositionSide,
    for_stop: bool = True,
) -> Decimal:
    """Snap a price to the exchange tick grid."""
    if tick_size <= 0:
        raise ValueError("tick_size must be positive")
    units = price / tick_size
    if for_stop:
        # Long stops snap down; short stops snap up — conservative for exits.
        rounding = ROUND_DOWN if side == "long" else ROUND_UP
    else:
        rounding = ROUND_UP if side == "long" else ROUND_DOWN
    return (units.to_integral_value(rounding=rounding)) * tick_size


def evaluate_exit(
    *,
    side: PositionSide,
    ltp: Decimal,
    stop: Decimal,
    target: Decimal | None,
    trailing_active: bool,
) -> ExitSignal | None:
    """Return the first triggered exit rule for the observed LTP."""
    if side == "long":
        if ltp <= stop:
            return ExitSignal(
                intent_type="trailing_exit" if trailing_active else "stop_loss_exit",
                trigger_price=ltp,
            )
        if target is not None and ltp >= target:
            return ExitSignal(
                intent_type="target_exit",
                trigger_price=ltp,
            )
        return None

    if ltp >= stop:
        return ExitSignal(
            intent_type="trailing_exit" if trailing_active else "stop_loss_exit",
            trigger_price=ltp,
        )
    if target is not None and ltp <= target:
        return ExitSignal(
            intent_type="target_exit",
            trigger_price=ltp,
        )
    return None


def apply_step_pct_trail(
    *,
    side: PositionSide,
    ltp: Decimal,
    current_stop: Decimal,
    step_pct: Decimal,
    tick_size: Decimal,
) -> Decimal | None:
    """
    Ratchet the stop in the favorable direction using a step percentage trail.

    Returns the new stop when it moves; otherwise None.
    """
    if step_pct <= 0 or step_pct >= 100:
        raise ValueError("step_pct must be between 0 and 100 exclusive.")

    if side == "long":
        candidate = snap_to_tick(
            ltp * (Decimal("1") - step_pct / Decimal("100")),
            tick_size,
            side=side,
            for_stop=True,
        )
        if candidate > current_stop:
            return candidate
        return None

    candidate = snap_to_tick(
        ltp * (Decimal("1") + step_pct / Decimal("100")),
        tick_size,
        side=side,
        for_stop=True,
    )
    if candidate < current_stop:
        return candidate
    return None


def unrealized_pnl(
    *,
    side: PositionSide,
    average_entry_price: Decimal,
    open_quantity: int,
    ltp: Decimal,
) -> Decimal:
    if open_quantity <= 0:
        return Decimal("0")
    if side == "long":
        return (ltp - average_entry_price) * open_quantity
    return (average_entry_price - ltp) * open_quantity


def realized_pnl_on_exit(
    *,
    side: PositionSide,
    average_entry_price: Decimal,
    quantity: int,
    exit_price: Decimal,
) -> Decimal:
    if side == "long":
        return (exit_price - average_entry_price) * quantity
    return (average_entry_price - exit_price) * quantity
