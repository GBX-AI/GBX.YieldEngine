"""
Dry-Run Validator — Yield Engine v3, Section 4K.

Pre-order validation layer. Every order MUST pass all checks
before reaching the Kite API. This is the last line of defense
against fat-finger errors, runaway automation, and invalid orders.

Checks:
  1. Quantity cap (per-symbol lot limits)
  2. Order value cap
  3. Price deviation (<20% of LTP)
  4. Exchange whitelist (NFO/NSE only)
  5. Product whitelist (NRML/CNC only)
  6. Daily order count limit
  7. Open position count limit
  8. F&O symbol validation (must be a known tradable symbol)
"""

import logging
from datetime import datetime

from models import get_db, SAFETY_HARD_CAPS, SIMULATION_STOCKS, SIMULATION_INDICES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known F&O symbols — union of simulation stocks + indices
# ---------------------------------------------------------------------------

_VALID_FNO_UNDERLYINGS = set(SIMULATION_STOCKS.keys()) | set(SIMULATION_INDICES.keys())

# Lot sizes by underlying
_LOT_SIZES = {}
_LOT_SIZES.update({sym: info["lotSize"] for sym, info in SIMULATION_STOCKS.items()})
_LOT_SIZES.update({sym: info["lotSize"] for sym, info in SIMULATION_INDICES.items()})


def _get_max_lots(symbol):
    """Return the hard-cap lot limit for a given underlying symbol."""
    upper = symbol.upper()
    if upper == "NIFTY":
        return SAFETY_HARD_CAPS["MAX_LOTS_PER_ORDER_NIFTY"]
    elif upper == "BANKNIFTY":
        return SAFETY_HARD_CAPS["MAX_LOTS_PER_ORDER_BANKNIFTY"]
    else:
        return SAFETY_HARD_CAPS["MAX_LOTS_PER_ORDER_STOCK"]


def _extract_underlying(tradingsymbol):
    """
    Extract the underlying name from a trading symbol.
    e.g. 'NIFTY2430622500CE' -> 'NIFTY', 'RELIANCE' -> 'RELIANCE'
    """
    upper = tradingsymbol.upper()
    for name in sorted(_VALID_FNO_UNDERLYINGS, key=len, reverse=True):
        if upper.startswith(name):
            return name
    return upper


def _get_daily_order_count():
    """Count orders placed today."""
    db = get_db()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        row = db.execute(
            "SELECT COUNT(*) FROM trades WHERE created_at >= ?", (today,)
        ).fetchone()
        return row[0]
    finally:
        db.close()


def _get_open_position_count():
    """Count currently open positions."""
    db = get_db()
    try:
        row = db.execute(
            "SELECT COUNT(*) FROM trades WHERE status = 'OPEN'"
        ).fetchone()
        return row[0]
    finally:
        db.close()


def validate_order(order_legs, current_state=None):
    """
    Validate an order before sending to Kite.

    Args:
        order_legs: list of dicts, each with keys:
            - tradingsymbol (str): e.g. "NIFTY2430622500CE"
            - exchange (str): "NFO" or "NSE"
            - product (str): "NRML" or "CNC"
            - quantity (int): number of shares/units
            - price (float): limit price (0 for market orders)
            - transaction_type (str): "BUY" or "SELL"
            - ltp (float, optional): last traded price for deviation check

        current_state: optional dict with pre-fetched state:
            - daily_order_count (int)
            - open_position_count (int)

    Returns:
        dict: {"valid": bool, "errors": list[str]}
    """
    errors = []

    if not order_legs:
        return {"valid": False, "errors": ["No order legs provided"]}

    state = current_state or {}
    daily_count = state.get("daily_order_count", _get_daily_order_count())
    open_count = state.get("open_position_count", _get_open_position_count())

    for i, leg in enumerate(order_legs):
        prefix = f"Leg {i + 1}"
        symbol = leg.get("tradingsymbol", "")
        exchange = leg.get("exchange", "")
        product = leg.get("product", "")
        quantity = leg.get("quantity", 0)
        price = leg.get("price", 0)
        ltp = leg.get("ltp", 0)

        # ------------------------------------------------------------------
        # Check 4: Exchange whitelist (NFO/NSE only)
        # ------------------------------------------------------------------
        if exchange not in SAFETY_HARD_CAPS["ALLOWED_EXCHANGES"]:
            errors.append(
                f"{prefix}: Exchange '{exchange}' not allowed. "
                f"Permitted: {SAFETY_HARD_CAPS['ALLOWED_EXCHANGES']}"
            )

        # ------------------------------------------------------------------
        # Check 5: Product whitelist (NRML/CNC only)
        # ------------------------------------------------------------------
        if product not in SAFETY_HARD_CAPS["ALLOWED_PRODUCTS"]:
            errors.append(
                f"{prefix}: Product '{product}' not allowed. "
                f"Permitted: {SAFETY_HARD_CAPS['ALLOWED_PRODUCTS']}"
            )

        # ------------------------------------------------------------------
        # Check 8: F&O symbol validation
        # ------------------------------------------------------------------
        underlying = _extract_underlying(symbol)
        if underlying not in _VALID_FNO_UNDERLYINGS:
            errors.append(
                f"{prefix}: Symbol '{symbol}' (underlying '{underlying}') "
                f"is not a recognized F&O tradable."
            )

        # ------------------------------------------------------------------
        # Check 1: Quantity cap
        # ------------------------------------------------------------------
        if underlying in _LOT_SIZES and quantity > 0:
            lot_size = _LOT_SIZES[underlying]
            lots = quantity / lot_size
            max_lots = _get_max_lots(underlying)
            if lots > max_lots:
                errors.append(
                    f"{prefix}: {lots:.1f} lots of {underlying} exceeds cap of "
                    f"{max_lots} lots ({max_lots * lot_size} qty)."
                )

        # ------------------------------------------------------------------
        # Check 2: Order value cap
        # ------------------------------------------------------------------
        order_value = abs(quantity * price) if price > 0 else 0
        if order_value > SAFETY_HARD_CAPS["MAX_ORDER_VALUE"]:
            errors.append(
                f"{prefix}: Order value ₹{order_value:,.0f} exceeds cap of "
                f"₹{SAFETY_HARD_CAPS['MAX_ORDER_VALUE']:,}."
            )

        # ------------------------------------------------------------------
        # Check 3: Price deviation (<20% of LTP)
        # ------------------------------------------------------------------
        if price > 0 and ltp > 0:
            deviation = abs(price - ltp) / ltp
            limit = SAFETY_HARD_CAPS["PRICE_DEVIATION_LIMIT"]
            if deviation > limit:
                errors.append(
                    f"{prefix}: Price ₹{price:.2f} deviates {deviation:.1%} from "
                    f"LTP ₹{ltp:.2f} (limit: {limit:.0%})."
                )

    # ----------------------------------------------------------------------
    # Check 6: Daily order count
    # ----------------------------------------------------------------------
    new_total = daily_count + len(order_legs)
    max_daily = SAFETY_HARD_CAPS["MAX_ORDERS_PER_DAY"]
    if new_total > max_daily:
        errors.append(
            f"Daily order count would reach {new_total}, exceeding limit of {max_daily}. "
            f"Already placed: {daily_count}."
        )

    # ----------------------------------------------------------------------
    # Check 7: Open position count
    # ----------------------------------------------------------------------
    max_open = SAFETY_HARD_CAPS["MAX_OPEN_POSITIONS"]
    # Each sell leg potentially opens a new position
    new_positions = sum(1 for leg in order_legs if leg.get("transaction_type") == "SELL")
    if open_count + new_positions > max_open:
        errors.append(
            f"Open positions would reach {open_count + new_positions}, "
            f"exceeding limit of {max_open}. Currently open: {open_count}."
        )

    is_valid = len(errors) == 0

    if not is_valid:
        logger.warning(
            "Order validation FAILED with %d error(s): %s",
            len(errors), "; ".join(errors),
        )
    else:
        logger.info("Order validation PASSED for %d leg(s)", len(order_legs))

    return {"valid": is_valid, "errors": errors}
