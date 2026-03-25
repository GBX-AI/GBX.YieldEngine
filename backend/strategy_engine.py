"""
Option Strategy Scanner — Yield Engine v3, Section 4G.

Scans holdings and market data for yield-generating option strategies,
scores them for safety, and returns ranked recommendations.

Supported strategies:
  - COVERED_CALL: Sell calls against F&O-eligible holdings (zero margin).
  - CASH_SECURED_PUT: Sell puts on NIFTY/BANKNIFTY using pledged collateral.
  - PUT_CREDIT_SPREAD: Defined-risk bull put spread on indices.
  - COLLAR: Protective collar on profitable positions (>8% gain).
"""

import math
from datetime import datetime, timedelta

from models import (
    SIMULATION_STOCKS,
    SIMULATION_INDICES,
    get_setting,
    generate_id,
)
from black_scholes import (
    option_price,
    compute_greeks,
    RISK_FREE_RATE,
)
from strike_selector import select_strike, select_strike_price, generate_alternatives, generate_strike_alternatives
from fee_calculator import calculate_fees, calculate_trade_fees
import live_price_service


# ─── Constants ────────────────────────────────────────────────────────────────

STRATEGY_TYPES = ("COVERED_CALL", "CASH_SECURED_PUT", "PUT_CREDIT_SPREAD", "COLLAR")

SAFETY_TAGS = ("VERY_SAFE", "SAFE", "MODERATE", "AGGRESSIVE")

SAFETY_TAG_ORDER = {tag: i for i, tag in enumerate(SAFETY_TAGS)}

# Collar eligibility: minimum unrealized gain percentage
COLLAR_MIN_GAIN_PCT = 0.08

# Put credit spread width bounds (index points)
SPREAD_WIDTH_MIN = 200
SPREAD_WIDTH_MAX = 300

# Default days to expiry for scanning
DEFAULT_DTE = 7

# Index strike step sizes
INDEX_STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100}

# Stock strike step size (universal)
STOCK_STRIKE_STEP = 50


# ─── Safety Scoring ──────────────────────────────────────────────────────────

def classify_safety(prob_otm: float, otm_pct: float) -> str:
    """
    Assign a safety tag based on probability of expiring OTM and
    how far out-of-the-money the strike sits.

    Args:
        prob_otm: Probability the option expires worthless (0.0–1.0).
        otm_pct:  Distance from spot as a fraction (e.g. 0.05 = 5% OTM).

    Returns:
        One of VERY_SAFE, SAFE, MODERATE, AGGRESSIVE.
    """
    if prob_otm >= 0.90 and otm_pct >= 0.04:
        return "VERY_SAFE"
    if prob_otm >= 0.85:
        return "SAFE"
    if prob_otm >= 0.75:
        return "MODERATE"
    return "AGGRESSIVE"


def passes_risk_filter(safety_tag: str, risk_profile: str) -> bool:
    """
    Check whether a recommendation passes the user's risk profile filter.

    - conservative: only VERY_SAFE and SAFE
    - moderate:     everything except AGGRESSIVE
    - aggressive:   all strategies allowed
    """
    profile = risk_profile.lower()
    if profile == "conservative":
        return safety_tag in ("VERY_SAFE", "SAFE")
    if profile == "moderate":
        return safety_tag != "AGGRESSIVE"
    return True  # aggressive — show everything


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _round_to_step(value: float, step: float) -> float:
    """Round a value to the nearest strike step."""
    return round(value / step) * step


def _get_dte(settings: dict) -> int:
    """Read preferred DTE from settings, falling back to module default."""
    raw = settings.get("preferred_dte") or get_setting("preferred_dte")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_DTE


def _time_to_expiry(dte: int) -> float:
    """Convert days-to-expiry into year fraction for Black-Scholes."""
    return max(dte, 1) / 365.0


def _annualized_return(premium: float, capital_at_risk: float, dte: int) -> float:
    """
    Compute annualized return on capital.

    Returns 0 if capital_at_risk is zero or negative.
    """
    if capital_at_risk <= 0 or dte <= 0:
        return 0.0
    period_return = premium / capital_at_risk
    return period_return * (365.0 / dte)


def _build_leg(action: str, strike: float, premium: float, qty: int,
               option_type: str = "CE") -> dict:
    """Construct a single leg dict."""
    return {
        "action": action,
        "strike": strike,
        "premium": round(premium, 2),
        "quantity": qty,
        "option_type": option_type,
    }


def _fee_estimate(legs: list) -> float:
    """Compute total estimated transaction fees for a set of legs."""
    fee_legs = [
        {
            "action": leg["action"],
            "premium": leg["premium"],
            "quantity": leg["quantity"],
            "is_exercise": False,
        }
        for leg in legs
    ]
    result = calculate_trade_fees(fee_legs)
    return result["total"]


def _strike_rationale(strategy_type: str, strike: float, spot: float,
                      delta_val: float, prob_otm: float) -> str:
    """Generate a human-readable rationale for the chosen strike."""
    otm_pct = abs(strike - spot) / spot * 100
    direction = "above" if strike > spot else "below"
    return (
        f"{strategy_type}: strike {strike:.0f} is {otm_pct:.1f}% {direction} spot "
        f"({spot:.0f}), delta {abs(delta_val):.2f}, "
        f"prob OTM {prob_otm * 100:.1f}%"
    )


def _resolve_settings(settings: dict | None) -> dict:
    """
    Merge caller-supplied settings with database defaults.
    Caller values take precedence.
    """
    base = {
        "risk_profile": get_setting("risk_profile") or "moderate",
        "max_loss_per_trade": float(get_setting("max_loss_per_trade") or 10000),
        "min_prob_otm": float(get_setting("min_prob_otm") or 0.75),
        "preferred_dte": int(get_setting("preferred_dte") or DEFAULT_DTE),
        "manual_target_delta_puts": float(get_setting("manual_target_delta_puts") or 0.20),
        "manual_target_delta_calls": float(get_setting("manual_target_delta_calls") or 0.15),
        "allowed_strategies": (get_setting("allowed_strategies") or ",".join(STRATEGY_TYPES)),
    }
    if settings:
        for k, v in settings.items():
            if v is not None:
                base[k] = v
    # Normalize allowed_strategies to a set
    allowed = base["allowed_strategies"]
    if isinstance(allowed, str):
        base["allowed_strategies"] = {s.strip() for s in allowed.split(",")}
    return base


# ─── Live Data Resolution ────────────────────────────────────────────────────

# NSE F&O lot sizes for common stocks (updated periodically by NSE)
# Fallback: if a stock is not here and not in SIMULATION_STOCKS, assume not F&O eligible
_FNO_LOT_SIZES = {
    "ABB": 250, "ADANIGREEN": 500, "ADANIPORTS": 625, "APOLLOHOSP": 125,
    "ASHOKLEY": 5000, "ASIANPAINT": 300, "AUBANK": 1000, "AUROPHARMA": 650,
    "AXISBANK": 600, "BAJAJ-AUTO": 250, "BAJAJFINSV": 500, "BAJFINANCE": 125,
    "BALKRISIND": 400, "BANDHANBNK": 1800, "BANKBARODA": 5850, "BEL": 1500,
    "BHARATFORG": 500, "BHARTIARTL": 475, "BHEL": 3750, "BIOCON": 2300,
    "BOSCHLTD": 50, "BPCL": 2100, "BRITANNIA": 200, "BSOFT": 1000,
    "CANBK": 7500, "CHAMBLFERT": 1500, "CHOLAFIN": 625, "CIPLA": 650,
    "COALINDIA": 2100, "COFORGE": 200, "COLPAL": 350, "CONCOR": 1000,
    "COROMANDEL": 500, "CUB": 5500, "CUMMINSIND": 300, "DABUR": 1250,
    "DALBHARAT": 500, "DEEPAKNTR": 250, "DIVISLAB": 200, "DIXON": 125,
    "DLF": 1375, "DRREDDY": 125, "EICHERMOT": 175, "ESCORTS": 250,
    "EXIDEIND": 1800, "FEDERALBNK": 5000, "GAIL": 5850, "GLENMARK": 500,
    "GMRAIRPORT": 10000, "GNFC": 1600, "GODREJCP": 500, "GODREJPROP": 325,
    "GRANULES": 2000, "GRASIM": 350, "GUJGASLTD": 1250, "HAL": 150,
    "HCLTECH": 350, "HDFCAMC": 150, "HDFCBANK": 550, "HDFCLIFE": 1100,
    "HEROMOTOCO": 150, "HINDALCO": 1075, "HINDCOPPER": 2300, "HINDPETRO": 1350,
    "HINDUNILVR": 300, "ICICIBANK": 700, "ICICIGI": 400, "ICICIPRULI": 1500,
    "IDEA": 50000, "IDFC": 5000, "IDFCFIRSTB": 7500, "IEX": 3750,
    "IGL": 2875, "INDHOTEL": 1250, "INDIAMART": 150, "INDIGO": 300,
    "INDUSINDBK": 500, "INFY": 400, "IOC": 4350, "IPCALAB": 550,
    "IRCTC": 750, "ITC": 1600, "JINDALSTEL": 750, "JKCEMENT": 125,
    "JSWSTEEL": 675, "JUBLFOOD": 1000, "KOTAKBANK": 400, "LALPATHLAB": 250,
    "LAURUSLABS": 1750, "LICHSGFIN": 1500, "LICI": 700, "LT": 150,
    "LTIM": 150, "LTTS": 150, "LUPIN": 550, "M&M": 350,
    "M&MFIN": 2000, "MANAPPURAM": 4000, "MARICO": 1200, "MARUTI": 100,
    "MCDOWELL-N": 625, "MCX": 200, "METROPOLIS": 400, "MFSL": 650,
    "MGL": 575, "MPHASIS": 350, "MRF": 10, "MUTHOOTFIN": 375,
    "NATIONALUM": 2500, "NAUKRI": 125, "NAVINFLUOR": 175, "NESTLEIND": 50,
    "NMDC": 3400, "NTPC": 2850, "OBEROIRLTY": 375, "OFSS": 100,
    "ONGC": 3075, "PAGEIND": 15, "PEL": 550, "PERSISTENT": 150,
    "PETRONET": 3000, "PFC": 2500, "PIDILITIND": 250, "PIIND": 250,
    "PNB": 8000, "POLYCAB": 125, "POWERGRID": 2700, "PVRINOX": 500,
    "RAMCOCEM": 600, "RBLBANK": 5000, "RECLTD": 1500, "RELIANCE": 250,
    "SAIL": 4750, "SBICARD": 800, "SBILIFE": 750, "SBIN": 1500,
    "SHREECEM": 25, "SHRIRAMFIN": 200, "SIEMENS": 150, "SRF": 375,
    "SUNPHARMA": 700, "SUNTV": 1000, "SYNGENE": 1000, "TATACHEM": 500,
    "TATACOMM": 500, "TATACONSUM": 900, "TATAELXSI": 125, "TATAMOTORS": 550,
    "TATAPOWER": 1875, "TATASTEEL": 5500, "TCS": 175, "TECHM": 600,
    "TITAN": 250, "TORNTPHARM": 250, "TRENT": 175, "TVSMOTOR": 175,
    "UBL": 350, "ULTRACEMCO": 100, "UNIONBANK": 7500, "UNITDSPR": 700,
    "UPL": 1300, "VEDL": 1550, "VOLTAS": 500, "WIPRO": 1500,
    "ZEEL": 5000, "ZYDUSLIFE": 650,
}

# Default IV estimate by market cap tier (rough approximation)
_DEFAULT_IV = 0.30  # 30% — conservative default for mid/small cap

# Default haircut by tier
_DEFAULT_HAIRCUT = 0.25


def _resolve_stock_info(symbol: str, holding: dict | None = None) -> dict | None:
    """
    Resolve spot price, IV, lot size, and haircut for any stock.
    Tries: 1) SIMULATION_STOCKS 2) Live Yahoo price + FNO lot size lookup.
    Returns dict with ltp, iv, lotSize, haircut — or None if not F&O eligible.
    """
    # Check if in the hardcoded simulation data first
    sim = SIMULATION_STOCKS.get(symbol)
    if sim:
        # Try live price, fall back to hardcoded
        live_spot = live_price_service.get_live_spot(symbol)
        return {
            "ltp": live_spot if live_spot else sim["ltp"],
            "iv": sim["iv"],
            "lotSize": sim["lotSize"],
            "haircut": sim["haircut"],
            "source": "yahoo" if live_spot else "simulated",
        }

    # Check if F&O eligible via lot size table
    lot_size = _FNO_LOT_SIZES.get(symbol)
    if not lot_size:
        return None  # Not F&O eligible — skip

    # Get live price
    live_spot = live_price_service.get_live_spot(symbol)
    if not live_spot and holding:
        live_spot = holding.get("ltp") or holding.get("avgPrice")
    if not live_spot:
        return None

    return {
        "ltp": live_spot,
        "iv": _DEFAULT_IV,
        "lotSize": lot_size,
        "haircut": _DEFAULT_HAIRCUT,
        "source": "yahoo" if live_price_service.get_live_spot(symbol) else "holding",
    }


def _resolve_index_info(index_name: str) -> dict:
    """Resolve live spot price for indices, falling back to hardcoded."""
    sim = SIMULATION_INDICES.get(index_name, {})
    live_spot = live_price_service.get_live_spot(index_name)
    return {
        "spot": live_spot if live_spot else sim.get("spot", 23000),
        "iv": sim.get("iv", 0.15),
        "lotSize": sim.get("lotSize", 25),
        "source": "yahoo" if live_spot else "simulated",
    }


def _add_frontend_aliases(rec: dict) -> dict:
    """Add frontend-compatible field aliases to a recommendation."""
    rec["premium"] = rec.get("premium_income", 0)
    rec["safety"] = rec.get("safety_tag", "MODERATE")
    rec["strategy"] = rec.get("strategy_type", "")
    rec["margin"] = rec.get("margin_needed", 0)
    return rec


# ─── Strategy Scanners ───────────────────────────────────────────────────────

def _scan_covered_calls(holdings: list, settings: dict, dte: int) -> list:
    """
    Scan for covered call opportunities on F&O-eligible holdings.

    A covered call sells a call option against shares already held.
    Requires qty >= lotSize for the stock. Zero additional margin.
    """
    recs = []
    T = _time_to_expiry(dte)
    target_delta = float(settings.get("manual_target_delta_calls", 0.15))

    for holding in holdings:
        symbol = holding.get("symbol", holding.get("tradingsymbol", ""))
        qty = holding.get("qty", holding.get("quantity", 0))

        stock_info = _resolve_stock_info(symbol, holding)
        if not stock_info:
            continue

        lot_size = stock_info["lotSize"]
        if qty < lot_size:
            continue

        spot = stock_info["ltp"]
        iv = stock_info["iv"]
        avg_cost = holding.get("average_price", holding.get("avgPrice", spot))

        # Select strike via strike_selector
        step = max(5, _round_to_step(spot * 0.01, 5))  # ~1% of spot, min 5
        if spot > 2000:
            step = STOCK_STRIKE_STEP
        strike = select_strike_price(
            spot=spot,
            option_type="CE",
            target_delta=target_delta,
            iv=iv,
            dte=dte,
            step=step,
        )
        if strike <= spot:
            # Ensure the call strike is OTM
            strike = _round_to_step(spot * (1 + 0.02), step)

        greeks = compute_greeks(spot, strike, T, RISK_FREE_RATE, iv, "CE")
        premium = greeks["price"]
        prob_otm = greeks["prob_otm"]
        delta_val = greeks["delta"]
        theta_day = greeks["theta"]

        if premium < 0.5:
            continue

        lots = qty // lot_size
        trade_qty = lots * lot_size
        total_premium = premium * trade_qty
        otm_pct = (strike - spot) / spot
        safety_tag = classify_safety(prob_otm, otm_pct)

        if not passes_risk_filter(safety_tag, settings["risk_profile"]):
            continue

        # Capital at risk = stock value (shares are collateral, no extra margin)
        capital_at_risk = spot * trade_qty
        ann_return = _annualized_return(total_premium, capital_at_risk, dte)

        legs = [_build_leg("SELL", strike, premium, trade_qty, "CE")]
        fees = _fee_estimate(legs)

        # Generate alternative strikes
        alts = generate_strike_alternatives(
            spot=spot,
            option_type="CE",
            iv=iv,
            dte=dte,
            step=step,
        )

        recs.append(_add_frontend_aliases({
            "id": generate_id(),
            "rank": 0,
            "symbol": symbol,
            "strategy_type": "COVERED_CALL",
            "strike": strike,
            "option_type": "CE",
            "legs": legs,
            "premium_income": round(total_premium, 2),
            "margin_needed": 0,
            "max_loss": round((spot - avg_cost) * trade_qty, 2),
            "prob_otm": round(prob_otm, 4),
            "delta": round(delta_val, 4),
            "annualized_return": round(ann_return, 4),
            "theta_per_day": round(theta_day * trade_qty, 2),
            "safety_tag": safety_tag,
            "strike_rationale": _strike_rationale("COVERED_CALL", strike, spot, delta_val, prob_otm),
            "alternatives": alts,
            "fee_estimate": fees,
            "dte": dte,
            "lots": lots,
            "spot": spot,
        }))

    return recs


def _scan_cash_secured_puts(cash_balance: float, settings: dict, dte: int) -> list:
    """
    Scan for cash-secured put opportunities on NIFTY and BANKNIFTY.

    Uses pledged collateral (cash_balance) to cover margin.
    Strike selected by target delta for puts.
    """
    recs = []
    T = _time_to_expiry(dte)
    target_delta = float(settings.get("manual_target_delta_puts", 0.20))

    for index_name in SIMULATION_INDICES:
        idx = _resolve_index_info(index_name)
        spot = idx["spot"]
        iv = idx["iv"]
        lot_size = idx["lotSize"]
        step = INDEX_STRIKE_STEP.get(index_name, 50)

        strike = select_strike_price(
            spot=spot,
            option_type="PE",
            target_delta=target_delta,
            iv=iv,
            dte=dte,
            step=step,
        )
        if strike >= spot:
            strike = _round_to_step(spot * (1 - 0.02), step)

        greeks = compute_greeks(spot, strike, T, RISK_FREE_RATE, iv, "PE")
        premium = greeks["price"]
        prob_otm = greeks["prob_otm"]
        delta_val = greeks["delta"]
        theta_day = greeks["theta"]

        if premium < 0.5:
            continue

        # Margin for naked put — approximate as 15% of notional
        margin_per_lot = spot * lot_size * 0.15
        max_lots = max(1, int(cash_balance / margin_per_lot)) if cash_balance > 0 else 1
        lots = min(max_lots, 2)  # Cap at 2 lots for safety
        trade_qty = lots * lot_size
        total_premium = premium * trade_qty
        margin_needed = margin_per_lot * lots

        if margin_needed > cash_balance and cash_balance > 0:
            continue

        otm_pct = (spot - strike) / spot
        safety_tag = classify_safety(prob_otm, otm_pct)

        if not passes_risk_filter(safety_tag, settings["risk_profile"]):
            continue

        ann_return = _annualized_return(total_premium, margin_needed, dte)
        max_loss = (strike - 0) * trade_qty  # Theoretical max; practically limited

        legs = [_build_leg("SELL", strike, premium, trade_qty, "PE")]
        fees = _fee_estimate(legs)

        alts = generate_strike_alternatives(
            spot=spot,
            option_type="PE",
            iv=iv,
            dte=dte,
            step=step,
        )

        recs.append(_add_frontend_aliases({
            "id": generate_id(),
            "rank": 0,
            "symbol": index_name,
            "strategy_type": "CASH_SECURED_PUT",
            "strike": strike,
            "option_type": "PE",
            "legs": legs,
            "premium_income": round(total_premium, 2),
            "margin_needed": round(margin_needed, 2),
            "max_loss": round(max_loss, 2),
            "prob_otm": round(prob_otm, 4),
            "delta": round(delta_val, 4),
            "annualized_return": round(ann_return, 4),
            "theta_per_day": round(theta_day * trade_qty, 2),
            "safety_tag": safety_tag,
            "strike_rationale": _strike_rationale("CASH_SECURED_PUT", strike, spot, delta_val, prob_otm),
            "alternatives": alts,
            "fee_estimate": fees,
            "dte": dte,
            "lots": lots,
            "spot": spot,
        }))

    return recs


def _scan_put_credit_spreads(cash_balance: float, settings: dict, dte: int) -> list:
    """
    Scan for put credit spread (bull put spread) on indices.

    Defined-risk strategy: sell higher put, buy lower put.
    Spread width: 200-300 index points.
    Max loss capped at user's max_loss_per_trade setting.
    """
    recs = []
    T = _time_to_expiry(dte)
    max_loss_limit = float(settings.get("max_loss_per_trade", 10000))
    target_delta = float(settings.get("manual_target_delta_puts", 0.20))

    for index_name in SIMULATION_INDICES:
        idx = _resolve_index_info(index_name)
        spot = idx["spot"]
        iv = idx["iv"]
        lot_size = idx["lotSize"]
        step = INDEX_STRIKE_STEP.get(index_name, 50)

        # Sell put (higher strike, closer to spot)
        sell_strike = select_strike_price(
            spot=spot,
            option_type="PE",
            target_delta=target_delta,
            iv=iv,
            dte=dte,
            step=step,
        )
        if sell_strike >= spot:
            sell_strike = _round_to_step(spot * (1 - 0.02), step)

        # Choose spread width within bounds
        for width in range(SPREAD_WIDTH_MIN, SPREAD_WIDTH_MAX + 1, step):
            buy_strike = _round_to_step(sell_strike - width, step)
            actual_width = sell_strike - buy_strike

            if actual_width < SPREAD_WIDTH_MIN:
                continue

            sell_greeks = compute_greeks(spot, sell_strike, T, RISK_FREE_RATE, iv, "PE")
            buy_greeks = compute_greeks(spot, buy_strike, T, RISK_FREE_RATE, iv, "PE")

            sell_premium = sell_greeks["price"]
            buy_premium = buy_greeks["price"]
            net_credit = sell_premium - buy_premium

            if net_credit <= 0:
                continue

            # Max loss per lot = (width - net_credit) * lot_size
            max_loss_per_lot = (actual_width - net_credit) * lot_size
            if max_loss_per_lot <= 0:
                continue

            lots = max(1, int(max_loss_limit / max_loss_per_lot))
            lots = min(lots, 2)  # Safety cap
            trade_qty = lots * lot_size
            total_credit = net_credit * trade_qty
            total_max_loss = max_loss_per_lot * lots

            if total_max_loss > max_loss_limit:
                lots = max(1, int(max_loss_limit / max_loss_per_lot))
                trade_qty = lots * lot_size
                total_credit = net_credit * trade_qty
                total_max_loss = max_loss_per_lot * lots

            # Margin for spread = max loss (defined risk)
            margin_needed = total_max_loss

            prob_otm = sell_greeks["prob_otm"]
            delta_val = sell_greeks["delta"]
            theta_day = (sell_greeks["theta"] - buy_greeks["theta"])

            otm_pct = (spot - sell_strike) / spot
            safety_tag = classify_safety(prob_otm, otm_pct)

            if not passes_risk_filter(safety_tag, settings["risk_profile"]):
                continue

            ann_return = _annualized_return(total_credit, margin_needed, dte)

            legs = [
                _build_leg("SELL", sell_strike, sell_premium, trade_qty, "PE"),
                _build_leg("BUY", buy_strike, buy_premium, trade_qty, "PE"),
            ]
            fees = _fee_estimate(legs)

            alts = generate_strike_alternatives(
                spot=spot,
                option_type="PE",
                iv=iv,
                dte=dte,
                step=step,
            )

            recs.append(_add_frontend_aliases({
                "id": generate_id(),
                "rank": 0,
                "symbol": index_name,
                "strategy_type": "PUT_CREDIT_SPREAD",
                "strike": sell_strike,
                "option_type": "PE",
                "legs": legs,
                "premium_income": round(total_credit, 2),
                "margin_needed": round(margin_needed, 2),
                "max_loss": round(total_max_loss, 2),
                "prob_otm": round(prob_otm, 4),
                "delta": round(delta_val, 4),
                "annualized_return": round(ann_return, 4),
                "theta_per_day": round(theta_day * trade_qty, 2),
                "safety_tag": safety_tag,
                "strike_rationale": _strike_rationale(
                    "PUT_CREDIT_SPREAD", sell_strike, spot, delta_val, prob_otm
                ),
                "alternatives": alts,
                "fee_estimate": fees,
                "dte": dte,
                "lots": lots,
                "spot": spot,
                "spread_width": actual_width,
            }))

            # Only take the first valid width per index
            break

    return recs


def _scan_collars(holdings: list, settings: dict, dte: int) -> list:
    """
    Scan for collar opportunities on profitable stock positions.

    A collar sells an OTM call and buys an OTM put, ideally for near-zero
    net cost, locking in gains on positions with > 8% unrealized profit.
    """
    recs = []
    T = _time_to_expiry(dte)
    target_delta_call = float(settings.get("manual_target_delta_calls", 0.15))
    target_delta_put = float(settings.get("manual_target_delta_puts", 0.20))

    for holding in holdings:
        symbol = holding.get("symbol", holding.get("tradingsymbol", ""))
        qty = holding.get("qty", holding.get("quantity", 0))
        avg_cost = holding.get("average_price", holding.get("avgPrice", 0))

        stock_info = _resolve_stock_info(symbol, holding)
        if not stock_info:
            continue

        lot_size = stock_info["lotSize"]
        if qty < lot_size:
            continue

        spot = stock_info["ltp"]
        iv = stock_info["iv"]

        # Check profitability threshold
        if avg_cost <= 0:
            continue
        gain_pct = (spot - avg_cost) / avg_cost
        if gain_pct < COLLAR_MIN_GAIN_PCT:
            continue

        step = max(5, _round_to_step(spot * 0.01, 5))
        if spot > 2000:
            step = STOCK_STRIKE_STEP

        # Sell OTM call
        call_strike = select_strike_price(
            spot=spot,
            option_type="CE",
            target_delta=target_delta_call,
            iv=iv,
            dte=dte,
            step=step,
        )
        if call_strike <= spot:
            call_strike = _round_to_step(spot * (1 + 0.03), step)

        # Buy OTM put
        put_strike = select_strike_price(
            spot=spot,
            option_type="PE",
            target_delta=target_delta_put,
            iv=iv,
            dte=dte,
            step=step,
        )
        if put_strike >= spot:
            put_strike = _round_to_step(spot * (1 - 0.03), step)

        call_greeks = compute_greeks(spot, call_strike, T, RISK_FREE_RATE, iv, "CE")
        put_greeks = compute_greeks(spot, put_strike, T, RISK_FREE_RATE, iv, "PE")

        call_premium = call_greeks["price"]
        put_premium = put_greeks["price"]
        net_cost = put_premium - call_premium  # Positive means debit

        lots = qty // lot_size
        trade_qty = lots * lot_size

        total_net = (call_premium - put_premium) * trade_qty  # Net credit (or debit if negative)
        max_loss = (spot - put_strike) * trade_qty  # Downside from spot to put strike

        # Use the call's prob_otm as the primary safety metric
        prob_otm = call_greeks["prob_otm"]
        call_otm_pct = (call_strike - spot) / spot
        safety_tag = classify_safety(prob_otm, call_otm_pct)

        if not passes_risk_filter(safety_tag, settings["risk_profile"]):
            continue

        # Collar has zero margin (covered by shares)
        margin_needed = 0
        capital_at_risk = spot * trade_qty
        ann_return = _annualized_return(max(total_net, 0), capital_at_risk, dte)

        theta_day = (call_greeks["theta"] - put_greeks["theta"])

        legs = [
            _build_leg("SELL", call_strike, call_premium, trade_qty, "CE"),
            _build_leg("BUY", put_strike, put_premium, trade_qty, "PE"),
        ]
        fees = _fee_estimate(legs)

        alts = generate_strike_alternatives(
            spot=spot,
            option_type="CE",
            iv=iv,
            dte=dte,
            step=step,
        )

        recs.append(_add_frontend_aliases({
            "id": generate_id(),
            "rank": 0,
            "symbol": symbol,
            "strategy_type": "COLLAR",
            "strike": call_strike,
            "option_type": "CE/PE",
            "legs": legs,
            "premium_income": round(total_net, 2),
            "margin_needed": margin_needed,
            "max_loss": round(max_loss, 2),
            "prob_otm": round(prob_otm, 4),
            "delta": round(call_greeks["delta"] + put_greeks["delta"], 4),
            "annualized_return": round(ann_return, 4),
            "theta_per_day": round(theta_day * trade_qty, 2),
            "safety_tag": safety_tag,
            "strike_rationale": _strike_rationale(
                "COLLAR", call_strike, spot, call_greeks["delta"], prob_otm
            ),
            "alternatives": alts,
            "fee_estimate": fees,
            "dte": dte,
            "lots": lots,
            "spot": spot,
            "put_strike": put_strike,
            "net_cost_per_unit": round(net_cost, 2),
            "unrealized_gain_pct": round(gain_pct * 100, 1),
        }))

    return recs


# ─── Ranking ──────────────────────────────────────────────────────────────────

def rank_recommendations(recs: list) -> list:
    """
    Rank recommendations by safety first, then by annualized return (descending).

    Safety ordering: VERY_SAFE > SAFE > MODERATE > AGGRESSIVE.
    Within the same safety tier, higher annualized return ranks first.
    """
    sorted_recs = sorted(
        recs,
        key=lambda r: (
            SAFETY_TAG_ORDER.get(r["safety_tag"], 99),
            -r["annualized_return"],
        ),
    )
    for i, rec in enumerate(sorted_recs, start=1):
        rec["rank"] = i
    return sorted_recs


# ─── Main Entry Point ────────────────────────────────────────────────────────

def scan_strategies(
    holdings: list,
    cash_balance: float,
    settings: dict | None = None,
) -> list:
    """
    Main scanner entry point. Scans all enabled strategy types across
    the user's holdings and market data, then returns a ranked list
    of recommendations.

    Args:
        holdings:     List of holding dicts, each with at minimum:
                      { symbol, quantity, average_price }.
        cash_balance: Available cash / pledged collateral value.
        settings:     Optional overrides for user settings. Keys may include
                      risk_profile, max_loss_per_trade, preferred_dte,
                      manual_target_delta_puts, manual_target_delta_calls,
                      allowed_strategies (comma-separated string or set).

    Returns:
        List of recommendation dicts, sorted by rank (1 = best).
        Each dict contains:
          id, rank, symbol, strategy_type, strike, option_type,
          legs, premium_income, margin_needed, max_loss, prob_otm,
          delta, annualized_return, theta_per_day, safety_tag,
          strike_rationale, alternatives, fee_estimate, dte, lots, spot.
    """
    resolved = _resolve_settings(settings)
    dte = _get_dte(resolved)
    allowed = resolved["allowed_strategies"]

    all_recs = []

    if "COVERED_CALL" in allowed:
        all_recs.extend(_scan_covered_calls(holdings, resolved, dte))

    if "CASH_SECURED_PUT" in allowed:
        all_recs.extend(_scan_cash_secured_puts(cash_balance, resolved, dte))

    if "PUT_CREDIT_SPREAD" in allowed:
        all_recs.extend(_scan_put_credit_spreads(cash_balance, resolved, dte))

    if "COLLAR" in allowed:
        all_recs.extend(_scan_collars(holdings, resolved, dte))

    ranked = rank_recommendations(all_recs)
    return ranked
