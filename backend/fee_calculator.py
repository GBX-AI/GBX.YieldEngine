"""
Exact Indian market fee computation for F&O trades.
Covers: brokerage, STT, exchange charges, SEBI, stamp duty, GST, IPFT.
"""


def calculate_fees(transaction_type, premium, quantity, is_exercise=False):
    """
    Calculate exact fees for an F&O trade.

    Args:
        transaction_type: "BUY" or "SELL"
        premium: option premium per unit
        quantity: total quantity (lots * lot_size)
        is_exercise: True if this is an exercise/assignment (higher STT)

    Returns:
        dict with itemized fees and total
    """
    turnover = premium * quantity

    fees = {
        "brokerage": min(20, turnover * 0.0003),  # ₹20 or 0.03%, whichever is lower
        "stt": 0,
        "exchange_txn": turnover * 0.000495,  # 0.0495% of premium
        "sebi_charges": turnover * 0.000001,  # ₹10 per crore
        "stamp_duty": 0,
        "gst": 0,
        "ipft": turnover * 0.0000001,  # ₹0.01 per crore
    }

    # STT: only on sell side for F&O, different rate for exercise
    if transaction_type == "SELL":
        if is_exercise:
            fees["stt"] = turnover * 0.00125  # 0.125% on exercise
        else:
            fees["stt"] = turnover * 0.000625  # 0.0625% of premium

    # Stamp duty: only on buy side
    if transaction_type == "BUY":
        fees["stamp_duty"] = turnover * 0.00003  # 0.003%

    # GST: 18% of (brokerage + exchange_txn + sebi)
    fees["gst"] = (fees["brokerage"] + fees["exchange_txn"] + fees["sebi_charges"]) * 0.18

    fees["total"] = sum(fees.values())

    return fees


def calculate_trade_fees(legs):
    """
    Calculate total fees for a multi-leg trade.

    Args:
        legs: list of dicts with keys: action (BUY/SELL), premium, quantity

    Returns:
        dict with per-leg fees and grand total
    """
    total = 0
    leg_fees = []

    for leg in legs:
        f = calculate_fees(
            transaction_type=leg["action"],
            premium=leg["premium"],
            quantity=leg["quantity"],
            is_exercise=leg.get("is_exercise", False)
        )
        leg_fees.append(f)
        total += f["total"]

    return {
        "legs": leg_fees,
        "total": round(total, 2),
    }


def calculate_exercise_stt(intrinsic_value, quantity):
    """
    Calculate STT cost if option expires ITM and is exercised.
    STT on exercise = 0.125% of intrinsic value (not premium).
    """
    return round(intrinsic_value * quantity * 0.00125, 2)


def estimate_slippage(symbol, is_index=False, is_volatile=False):
    """
    Estimate slippage per unit for an option trade.

    Returns:
        float: estimated slippage per unit in ₹
    """
    if is_index:
        base = 1.5  # NIFTY/BANKNIFTY are liquid
    else:
        base = 3.5  # Stock options less liquid

    if is_volatile:
        base *= 2  # Double during volatile moments

    return base


def format_fee_breakdown(fees):
    """Format fees dict into human-readable string."""
    parts = []
    if fees.get("brokerage", 0) > 0:
        parts.append(f"brokerage ₹{fees['brokerage']:.2f}")
    if fees.get("stt", 0) > 0:
        parts.append(f"STT ₹{fees['stt']:.2f}")
    if fees.get("exchange_txn", 0) > 0:
        parts.append(f"exchange ₹{fees['exchange_txn']:.2f}")
    if fees.get("gst", 0) > 0:
        parts.append(f"GST ₹{fees['gst']:.2f}")
    if fees.get("stamp_duty", 0) > 0:
        parts.append(f"stamp ₹{fees['stamp_duty']:.2f}")
    if fees.get("sebi_charges", 0) > 0:
        parts.append(f"SEBI ₹{fees['sebi_charges']:.4f}")

    return " + ".join(parts)


def net_pnl(gross_pnl, entry_fees, exit_fees):
    """Calculate net P&L after deducting fees."""
    return round(gross_pnl - entry_fees - exit_fees, 2)
