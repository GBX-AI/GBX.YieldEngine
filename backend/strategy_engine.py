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
from charges_engine import charges_engine
from execution_filter import execution_check, get_real_fill_price, calculate_spread_net_credit, calculate_confidence, SLIPPAGE_FACTOR
import live_price_service
import market_data
import vix_service
import portfolio_risk
from datetime import date, timezone, timedelta as td

_IST = timezone(td(hours=5, minutes=30))
def _now_ist():
    return datetime.now(_IST).isoformat()


# ─── Constants ────────────────────────────────────────────────────────────────

STRATEGY_TYPES = ("COVERED_CALL", "CASH_SECURED_PUT", "PUT_CREDIT_SPREAD", "COLLAR", "SHORT_STRANGLE", "ATM_SHORT_STRANGLE", "IRON_CONDOR", "RSI_OPTION_SELL", "CALENDAR_SPREAD")

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

def classify_safety(prob_otm: float, otm_pct: float,
                     risk_reward_ratio: float = None,
                     max_loss: float = None,
                     net_premium: float = None) -> str:
    """
    Assign a safety tag based on probability of expiring OTM,
    how far out-of-the-money the strike sits, and risk/reward metrics.

    Args:
        prob_otm: Probability the option expires worthless (0.0–1.0).
        otm_pct:  Distance from spot as a fraction (e.g. 0.05 = 5% OTM).
        risk_reward_ratio: Optional max_profit / max_loss ratio.
        max_loss: Optional maximum loss in rupees.
        net_premium: Optional net premium received in rupees.

    Returns:
        One of VERY_SAFE, SAFE, MODERATE, AGGRESSIVE.
    """
    if prob_otm >= 0.90 and otm_pct >= 0.04:
        tag = "VERY_SAFE"
    elif prob_otm >= 0.85:
        tag = "SAFE"
    elif prob_otm >= 0.75:
        tag = "MODERATE"
    else:
        tag = "AGGRESSIVE"

    # Post-classification overrides based on risk/reward reality
    # If risking ₹100 to make ₹5 or less, downgrade by 1 level
    if risk_reward_ratio is not None and risk_reward_ratio < 0.05:
        _order = ["VERY_SAFE", "SAFE", "MODERATE", "AGGRESSIVE"]
        idx = _order.index(tag) if tag in _order else 3
        if idx < 3:
            tag = _order[idx + 1]

    # If max_loss > ₹50,000 and net_premium < ₹1,000, never mark SAFE or VERY_SAFE
    if (max_loss is not None and net_premium is not None
            and max_loss > 50000 and net_premium < 1000):
        if tag in ("VERY_SAFE", "SAFE"):
            tag = "MODERATE"

    return tag


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


def _sell_price(opt_data: dict) -> float:
    """Best available sell price: bid > LTP > 0. Handles holidays where bid=0."""
    bid = opt_data.get("bid", 0)
    return bid if bid > 0 else opt_data.get("premium", 0)


def _buy_price(opt_data: dict) -> float:
    """Best available buy price: ask > LTP > 0. Handles holidays where ask=0."""
    ask = opt_data.get("ask", 0)
    return ask if ask > 0 else opt_data.get("premium", 0)


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
               option_type: str = "CE", tradingsymbol: str = None) -> dict:
    """Construct a single leg dict."""
    leg = {
        "action": action,
        "strike": strike,
        "premium": round(premium, 2),
        "quantity": qty,
        "option_type": option_type,
    }
    if tradingsymbol:
        leg["tradingsymbol"] = tradingsymbol
    return leg


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
        base["allowed_strategies"] = {s.strip() for s in allowed.split(",") if s.strip()}
    # Auto-include strategies added after the user last saved settings
    base["allowed_strategies"] = base["allowed_strategies"] | set(STRATEGY_TYPES)
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


def _resolve_stock_info(symbol: str, holding: dict | None = None, kite_service=None) -> dict | None:
    """
    Resolve spot price, IV, lot size, and haircut for any stock.
    When kite_service is authenticated, uses real lot sizes and F&O eligibility.
    Tries: 1) Kite real data 2) SIMULATION_STOCKS 3) Live Yahoo price + FNO lot size lookup.
    Returns dict with ltp, iv, lotSize, haircut — or None if not F&O eligible.
    """
    kite_connected = kite_service and kite_service.is_authenticated()

    # When Kite is connected, use real F&O eligibility and lot size
    if kite_connected:
        if not market_data.has_fno_options(kite_service, symbol):
            return None  # Not F&O eligible per Kite instruments
        kite_lot = market_data.get_lot_size(kite_service, symbol)
        if kite_lot:
            # Try live price from various sources
            live_spot = live_price_service.get_live_spot(symbol)
            sim = SIMULATION_STOCKS.get(symbol)
            if not live_spot and sim:
                live_spot = sim["ltp"]
            if not live_spot and holding:
                live_spot = holding.get("ltp") or holding.get("avgPrice")
            if not live_spot:
                return None
            return {
                "ltp": live_spot,
                "iv": sim["iv"] if sim else _DEFAULT_IV,
                "lotSize": kite_lot,
                "haircut": sim["haircut"] if sim else _DEFAULT_HAIRCUT,
                "source": "kite",
            }

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


def _resolve_index_info(index_name: str, kite_service=None) -> dict:
    """Resolve live spot price for indices, falling back to hardcoded.
    When kite_service is authenticated, uses real lot size from Kite."""
    sim = SIMULATION_INDICES.get(index_name, {})
    live_spot = live_price_service.get_live_spot(index_name)

    kite_connected = kite_service and kite_service.is_authenticated()
    if kite_connected:
        kite_lot = market_data.get_lot_size(kite_service, index_name)
        lot_size = kite_lot if kite_lot else sim.get("lotSize", 25)
    else:
        lot_size = sim.get("lotSize", 25)

    return {
        "spot": live_spot if live_spot else sim.get("spot", 23000),
        "iv": sim.get("iv", 0.15),
        "lotSize": lot_size,
        "source": "kite" if kite_connected else ("yahoo" if live_spot else "simulated"),
    }


def _calculate_exit_suggestion(rec: dict) -> dict:
    """Calculate exit timing and target for an opportunity (Prompt 3)."""
    dte = rec.get("dte", 7)
    net_premium = rec.get("net_premium", rec.get("premium_income", 0))
    lot_size = rec.get("lot_size", 1)
    lots = rec.get("lots", 1)
    qty = lot_size * lots
    theta_per_day = rec.get("theta_per_day", 0)

    # Theta in rupees — theta_per_day is ALREADY multiplied by trade_qty in scanner
    # So it's the total theta for the whole position, not per-share
    theta_rupees = round(abs(theta_per_day), 2) if theta_per_day else 0

    # Exit rules — RSI trades use 30% decay target, others 50%
    custom_exit_pct = rec.get("exit_at_decay_pct")
    target_exit_pct = custom_exit_pct if custom_exit_pct else 50
    # Calculate per-share exit premium target
    # Find the sell leg's per-share premium for the exit target
    sell_premium_per_share = 0
    for leg in rec.get("legs", []):
        if (leg.get("action") or "").upper() == "SELL":
            sell_premium_per_share = leg.get("premium", 0)
            break
    decay_factor = (100 - target_exit_pct) / 100  # 50% target → 0.5 decay, 30% target → 0.7 decay
    target_exit_premium_per_share = round(sell_premium_per_share * decay_factor, 2)
    target_exit_premium_total = round(net_premium * decay_factor, 2) if net_premium > 0 else 0

    if dte <= 7:
        # Weekly: exit at 50% or Thursday EOD
        target_exit_day = max(1, dte - 1)
        reason = "Weekly option: target 50% profit or exit Thursday EOD"
        gamma_warning_dte = 1
    elif dte <= 14:
        target_exit_day = max(1, dte - 3)
        reason = "Short-term: target 50% profit or exit 3 DTE"
        gamma_warning_dte = 2
    else:
        # Monthly: exit at 50% or 5 DTE
        target_exit_day = max(1, dte - 5)
        reason = "Monthly option: target 50% profit or exit by 5 DTE"
        gamma_warning_dte = 3

    today = date.today()
    target_date = today + timedelta(days=target_exit_day)

    return {
        "theta_per_day_rupees": theta_rupees,
        "exit_suggestion": {
            "target_exit_day": target_exit_day,
            "target_exit_date": target_date.isoformat(),
            "target_exit_pct": target_exit_pct,
            "target_exit_premium": target_exit_premium_per_share,
            "target_exit_premium_total": target_exit_premium_total,
            "reason": reason,
            "gamma_warning_dte": gamma_warning_dte,
            "gamma_warning": dte <= gamma_warning_dte,
            "notes": f"Exit when premium reaches ₹{target_exit_premium_per_share:.2f}/share or by {target_date.strftime('%a %d %b')}"
        }
    }


def _enrich_recommendation(rec: dict) -> dict:
    """Enrich a recommendation with charges, risk profile, exit timing, and frontend aliases."""

    # ── 1. Compute charges (Prompt 1) ──
    legs = rec.get("legs", [])
    lot_size = rec.get("lot_size", 1)
    lots = rec.get("lots", 1)

    # Calculate gross premium: sum(premium * qty) for SELL legs - sum(premium * qty) for BUY legs
    gross_premium = 0
    for leg in legs:
        leg_total = leg.get("premium", 0) * leg.get("quantity", 0)
        if leg.get("action") == "SELL":
            gross_premium += leg_total
        elif leg.get("action") == "BUY":
            gross_premium -= leg_total
    gross_premium = round(gross_premium, 2)

    if legs and lot_size:
        charges = charges_engine.calculate(legs, lot_size, lots)
        rec["charges"] = charges
        rec["gross_premium"] = gross_premium
        rec["total_charges"] = charges["total_charges"]
        rec["net_premium"] = round(gross_premium - charges["total_charges"], 2)
        rec["charges_breakdown"] = charges["charges_breakdown"]
        rec["breakeven_adjustment"] = charges.get("effective_breakeven_adjustment", 0)

        # Apply slippage to net premium
        rec["net_credit_adjusted"] = round(rec.get("net_premium", 0) * SLIPPAGE_FACTOR, 2)
        rec["slippage_pct"] = 10

        # Recalculate annualized return using net premium
        margin = rec.get("margin_needed", 0)
        dte = rec.get("dte", 7)
        if margin > 0 and dte > 0:
            rec["true_annualized_return"] = round(
                (rec["net_premium"] / margin) * (365.0 / dte) * 100, 2
            )
        else:
            rec["true_annualized_return"] = 0
    else:
        rec["charges"] = {}
        rec["gross_premium"] = gross_premium
        rec["net_premium"] = gross_premium
        rec["total_charges"] = 0
        rec["true_annualized_return"] = round(rec.get("annualized_return", 0) * 100, 2) if rec.get("annualized_return", 0) < 1 else rec.get("annualized_return", 0)

    # ── 2. Risk profile (Prompt 2) ──
    strategy = rec.get("strategy_type", "")
    net = rec.get("net_premium", 0)
    qty = lot_size * lots

    if strategy == "PUT_CREDIT_SPREAD":
        spread_width = rec.get("spread_width", 0)
        rec["max_profit"] = round(net, 2)
        rec["max_loss"] = round((spread_width * qty) - net, 2) if spread_width else 0
        rec["max_loss_point"] = rec.get("strike", 0) - spread_width if spread_width else 0
        rec["max_profit_point"] = rec.get("strike", 0)

    elif strategy == "CASH_SECURED_PUT":
        strike = rec.get("strike", 0)
        spot = rec.get("spot", 0)
        rec["max_profit"] = round(net, 2)
        rec["max_loss"] = round((strike * qty) - net, 2)
        rec["max_loss_point"] = 0
        rec["max_profit_point"] = strike
        rec["practical_max_loss_point"] = round(strike * 0.5, 2)
        # Ensure margin_needed is set as ₹ amount (approx 15% of notional for naked put)
        if not rec.get("margin_needed"):
            rec["margin_needed"] = round(spot * lot_size * lots * 0.15, 2)

    elif strategy == "COVERED_CALL":
        strike = rec.get("strike", 0)
        avg_cost = rec.get("avg_cost", rec.get("spot", 0))
        rec["max_profit"] = round((strike - avg_cost + (net / qty if qty else 0)) * qty, 2)
        rec["max_loss"] = round((avg_cost - (net / qty if qty else 0)) * qty, 2)
        rec["max_loss_point"] = 0
        rec["max_profit_point"] = strike

    elif strategy == "COLLAR":
        call_strike = rec.get("strike", 0)
        put_strike = rec.get("put_strike", 0)
        rec["max_profit"] = round(net, 2) if net > 0 else 0
        rec["max_loss"] = round((rec.get("spot", 0) - put_strike) * qty, 2) if put_strike else 0
        rec["max_loss_point"] = put_strike
        rec["max_profit_point"] = call_strike

    elif strategy == "IRON_CONDOR":
        rec["max_profit"] = round(net, 2)
        # Downside capped by protective put
        put_width = rec.get("put_spread_width", 0)
        rec["max_loss"] = round((put_width * qty) - net, 2) if put_width else round(rec.get("max_loss", 0), 2)
        rec["max_loss_point"] = rec.get("protection_strike", 0)
        rec["max_profit_point"] = rec.get("strike", 0)  # Both sell strikes

    elif strategy == "CALENDAR_SPREAD":
        rec["max_profit"] = round(net * 0.25, 2)  # 25% target profit
        rec["max_loss"] = round(rec.get("net_debit_per_share", 0) * qty, 2)  # Net debit paid
        rec["max_loss_point"] = rec.get("strike", 0)  # ATM = max risk

    elif strategy in ("SHORT_STRANGLE", "ATM_SHORT_STRANGLE"):
        rec["max_profit"] = round(net, 2)
        # Strangle max loss is theoretically unlimited
        rec["max_loss_point"] = 0
        rec["max_profit_point"] = rec.get("strike", 0)

    else:
        rec["max_profit"] = round(max(net, 0), 2)
        rec["max_loss"] = round(rec.get("max_loss", 0), 2)

    # Risk/reward ratio
    max_loss = rec.get("max_loss", 0)
    max_profit = rec.get("max_profit", 0)
    rec["risk_reward_ratio"] = round(max_profit / max_loss, 4) if max_loss > 0 else 0
    rec["loss_as_pct_of_margin"] = round((max_loss / rec.get("margin_needed", 1)) * 100, 1) if rec.get("margin_needed") else 0

    # Re-classify safety with risk/reward awareness
    original_tag = rec.get("safety_tag", "MODERATE")
    adjusted_tag = classify_safety(
        prob_otm=rec.get("prob_otm", 0.5),
        otm_pct=abs(rec.get("strike", 0) - rec.get("spot", 0)) / rec.get("spot", 1) if rec.get("spot") else 0,
        risk_reward_ratio=rec["risk_reward_ratio"],
        max_loss=max_loss,
        net_premium=net,
    )
    rec["safety_tag"] = adjusted_tag

    # ── 3. Exit suggestion (Prompt 3) ──
    exit_data = _calculate_exit_suggestion(rec)
    rec["theta_per_day_rupees"] = exit_data["theta_per_day_rupees"]
    rec["exit_suggestion"] = exit_data["exit_suggestion"]

    # ── 3b. Confidence calculation ──
    confidence = calculate_confidence({
        "execution_quality": rec.get("execution_quality", "FAIR"),
        "net_credit_adjusted": rec.get("net_credit_adjusted", rec.get("net_premium", 0)),
        "max_loss": rec.get("max_loss", 0),
        "prob_otm": rec.get("prob_otm", 0),
        "sentiment_signal": rec.get("sentiment_signal", "YELLOW"),
    })
    rec["confidence_score"] = confidence["confidence_score"]
    rec["decision"] = confidence["decision"]
    rec["execution_quality"] = confidence["execution_quality"]
    rec["confidence_reasons"] = confidence["reasons"]

    # ── 4. Frontend aliases ──
    rec["premium"] = rec.get("net_premium", rec.get("premium_income", 0))
    rec["premium_income"] = rec.get("net_premium", rec.get("premium_income", 0))
    rec["safety"] = rec.get("safety_tag", "MODERATE")
    rec["strategy"] = rec.get("strategy_type", "")
    rec["margin"] = rec.get("margin_needed", 0)
    rec["annualized_return"] = rec.get("true_annualized_return", rec.get("annualized_return", 0))

    # Price source metadata
    if "price_source" not in rec:
        rec["price_source"] = "simulation"
    if "fetched_at" not in rec:
        rec["fetched_at"] = _now_ist()

    # Mark which fields come from Black-Scholes vs market data
    rec["bs_derived"] = []
    if rec.get("price_source") != "kite":
        rec["bs_derived"].extend(["premium", "greeks", "prob_otm"])
    else:
        rec["bs_derived"].extend(["greeks", "prob_otm"])  # Greeks always from BS

    return rec


# ─── Strategy Scanners ───────────────────────────────────────────────────────

def _scan_covered_calls(holdings: list, settings: dict, dte: int, kite_service=None) -> list:
    """
    Scan for covered call opportunities on F&O-eligible holdings.

    A covered call sells a call option against shares already held.
    Requires qty >= lotSize for the stock. Zero additional margin.
    When kite_service is connected, uses real option chain for premiums.
    """
    recs = []
    T = _time_to_expiry(dte)
    target_delta = float(settings.get("manual_target_delta_calls", 0.15))
    kite_connected = kite_service and kite_service.is_authenticated()

    for holding in holdings:
        symbol = holding.get("symbol", holding.get("tradingsymbol", ""))
        qty = holding.get("qty", holding.get("quantity", 0))

        stock_info = _resolve_stock_info(symbol, holding, kite_service=kite_service)
        if not stock_info:
            continue

        lot_size = stock_info["lotSize"]
        if qty < lot_size:
            continue

        spot = stock_info["ltp"]
        iv = stock_info["iv"]
        avg_cost = holding.get("average_price", holding.get("avgPrice", spot))

        # Determine expiry per-strategy: stocks use monthly expiry
        expiry_date = None
        if kite_connected:
            expiry_date = market_data.get_nearest_expiry(kite_service, symbol)

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

        # Try real option chain from Kite
        real_chain = None
        use_real = False
        if kite_connected and expiry_date:
            real_chain = market_data.get_option_chain_live(kite_service, symbol, expiry_date)

        if real_chain and real_chain.get("strikes"):
            # Find the strike closest to our target delta in the real chain
            best_strike_data = None
            best_delta_diff = float("inf")
            for s_data in real_chain["strikes"]:
                ce = s_data.get("CE")
                if not ce or ce.get("premium", 0) <= 0:
                    continue
                # Filter low liquidity
                if ce.get("oi", 0) < 100:
                    continue
                s_strike = s_data["strike"]
                if s_strike <= spot:
                    continue  # Only OTM calls
                s_greeks = compute_greeks(spot, s_strike, T, RISK_FREE_RATE, iv, "CE")
                delta_diff = abs(abs(s_greeks["delta"]) - target_delta)
                if delta_diff < best_delta_diff:
                    best_delta_diff = delta_diff
                    best_strike_data = (s_strike, ce, s_greeks)

            if best_strike_data:
                strike, ce_data, greeks = best_strike_data
                passed, reason, quality = execution_check(ce_data)
                if not passed:
                    continue  # Skip illiquid options
                premium = _sell_price(ce_data)
                prob_otm = greeks["prob_otm"]
                delta_val = greeks["delta"]
                theta_day = greeks["theta"]
                use_real = True
                # Update DTE from chain if available
                if real_chain.get("dte"):
                    dte = real_chain["dte"]
                    T = _time_to_expiry(dte)

        if not use_real:
            # Fall back to Black-Scholes (current behavior)
            greeks = compute_greeks(spot, strike, T, RISK_FREE_RATE, iv, "CE")
            premium = greeks["price"]
            prob_otm = greeks["prob_otm"]
            delta_val = greeks["delta"]
            theta_day = greeks["theta"]
            quality = "FAIR"

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
        total_margin = 0  # Covered call — shares are collateral

        legs = [_build_leg("SELL", strike, premium, trade_qty, "CE",
                           tradingsymbol=ce_data.get("tradingsymbol") if use_real else None)]
        fees = _fee_estimate(legs)

        # Generate alternative strikes
        alts = generate_strike_alternatives(
            spot=spot,
            option_type="CE",
            iv=iv,
            dte=dte,
            step=step,
        )

        recs.append(_enrich_recommendation({
            "id": generate_id(),
            "rank": 0,
            "symbol": symbol,
            "strategy_type": "COVERED_CALL",
            "strike": strike,
            "option_type": "CE",
            "legs": legs,
            "premium_income": round(total_premium, 2),
            "margin_needed": 0,
            "total_margin": total_margin,
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
            "lot_size": lot_size,
            "spot": spot,
            "avg_cost": round(avg_cost, 2),
            "holding_qty": qty,
            "lots_possible": lots,
            "unrealized_pnl": round((spot - avg_cost) * qty, 2),
            "source": "covered_call_from_holdings",
            "price_source": "kite" if use_real else "simulation",
            "execution_quality": quality if use_real else "FAIR",
            "fetched_at": _now_ist(),
        }))

    return recs


def _scan_cash_secured_puts(cash_balance: float, settings: dict, dte: int, kite_service=None) -> list:
    """
    Scan for cash-secured put opportunities on NIFTY and BANKNIFTY.

    Uses pledged collateral (cash_balance) to cover margin.
    Strike selected by target delta for puts.
    When kite_service is connected, uses real option chain for premiums.
    """
    recs = []
    T = _time_to_expiry(dte)
    target_delta = float(settings.get("manual_target_delta_puts", 0.20))
    kite_connected = kite_service and kite_service.is_authenticated()

    for index_name in SIMULATION_INDICES:
        idx = _resolve_index_info(index_name, kite_service=kite_service)
        spot = idx["spot"]
        iv = idx["iv"]
        lot_size = idx["lotSize"]
        step = INDEX_STRIKE_STEP.get(index_name, 50)

        # Determine expiry per-strategy: indices use weekly expiry
        expiry_date = None
        if kite_connected:
            expiry_date = market_data.get_nearest_expiry(kite_service, index_name)

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

        # Try real option chain from Kite
        real_chain = None
        use_real = False
        if kite_connected and expiry_date:
            real_chain = market_data.get_option_chain_live(kite_service, index_name, expiry_date)

        if real_chain and real_chain.get("strikes"):
            best_strike_data = None
            best_delta_diff = float("inf")
            for s_data in real_chain["strikes"]:
                pe = s_data.get("PE")
                if not pe or pe.get("premium", 0) <= 0:
                    continue
                # Filter low liquidity
                if pe.get("oi", 0) < 100:
                    continue
                s_strike = s_data["strike"]
                if s_strike >= spot:
                    continue  # Only OTM puts
                s_greeks = compute_greeks(spot, s_strike, T, RISK_FREE_RATE, iv, "PE")
                delta_diff = abs(abs(s_greeks["delta"]) - target_delta)
                if delta_diff < best_delta_diff:
                    best_delta_diff = delta_diff
                    best_strike_data = (s_strike, pe, s_greeks)

            if best_strike_data:
                strike, pe_data, greeks = best_strike_data
                passed, reason, quality = execution_check(pe_data)
                if not passed:
                    continue  # Skip illiquid options
                premium = _sell_price(pe_data)
                prob_otm = greeks["prob_otm"]
                delta_val = greeks["delta"]
                theta_day = greeks["theta"]
                use_real = True
                if real_chain.get("dte"):
                    dte = real_chain["dte"]
                    T = _time_to_expiry(dte)

        if not use_real:
            greeks = compute_greeks(spot, strike, T, RISK_FREE_RATE, iv, "PE")
            premium = greeks["price"]
            prob_otm = greeks["prob_otm"]
            delta_val = greeks["delta"]
            theta_day = greeks["theta"]
            quality = "FAIR"

        if premium < 0.5:
            continue

        # Margin for naked put — approximate as 15% of notional
        margin_per_lot = spot * lot_size * 0.15
        max_lots = max(1, int(cash_balance / margin_per_lot)) if cash_balance > 0 else 1
        lots = 1  # Default to 1 lot — let the user decide to add more
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

        legs = [_build_leg("SELL", strike, premium, trade_qty, "PE",
                           tradingsymbol=pe_data.get("tradingsymbol") if use_real else None)]
        fees = _fee_estimate(legs)

        alts = generate_strike_alternatives(
            spot=spot,
            option_type="PE",
            iv=iv,
            dte=dte,
            step=step,
        )

        recs.append(_enrich_recommendation({
            "id": generate_id(),
            "rank": 0,
            "symbol": index_name,
            "strategy_type": "CASH_SECURED_PUT",
            "strike": strike,
            "option_type": "PE",
            "legs": legs,
            "premium_income": round(total_premium, 2),
            "margin_needed": round(margin_needed, 2),
            "lot_size": lot_size,
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
            "price_source": "kite" if use_real else "simulation",
            "execution_quality": quality,
            "fetched_at": _now_ist(),
        }))

    return recs


def _scan_put_credit_spreads(cash_balance: float, settings: dict, dte: int, kite_service=None) -> list:
    """
    Scan for put credit spread (bull put spread) on indices.

    Defined-risk strategy: sell higher put, buy lower put.
    Spread width: 200-300 index points.
    Max loss capped at user's max_loss_per_trade setting.
    When kite_service is connected, uses real option chain for premiums.
    """
    recs = []
    T = _time_to_expiry(dte)
    max_loss_limit = float(settings.get("max_loss_per_trade", 10000))
    target_delta = float(settings.get("manual_target_delta_puts", 0.20))
    kite_connected = kite_service and kite_service.is_authenticated()

    for index_name in SIMULATION_INDICES:
        idx = _resolve_index_info(index_name, kite_service=kite_service)
        spot = idx["spot"]
        iv = idx["iv"]
        lot_size = idx["lotSize"]
        step = INDEX_STRIKE_STEP.get(index_name, 50)

        # Determine expiry per-strategy: indices use weekly expiry
        expiry_date = None
        if kite_connected:
            expiry_date = market_data.get_nearest_expiry(kite_service, index_name)

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

        # Try real option chain from Kite
        real_chain = None
        use_real = False
        if kite_connected and expiry_date:
            real_chain = market_data.get_option_chain_live(kite_service, index_name, expiry_date)

        if real_chain and real_chain.get("strikes"):
            # Find sell strike closest to target delta in real chain
            best_sell = None
            best_delta_diff = float("inf")
            for s_data in real_chain["strikes"]:
                pe = s_data.get("PE")
                if not pe or pe.get("premium", 0) <= 0:
                    continue
                if pe.get("oi", 0) < 100:
                    continue
                s_strike = s_data["strike"]
                if s_strike >= spot:
                    continue
                s_greeks = compute_greeks(spot, s_strike, T, RISK_FREE_RATE, iv, "PE")
                delta_diff = abs(abs(s_greeks["delta"]) - target_delta)
                if delta_diff < best_delta_diff:
                    best_delta_diff = delta_diff
                    best_sell = (s_strike, pe, s_greeks)

            if best_sell:
                sell_strike, sell_pe, sell_greeks_real = best_sell
                passed, reason, sell_quality = execution_check(sell_pe)
                if not passed:
                    continue  # Skip illiquid sell leg
                sell_premium = _sell_price(sell_pe)

                # Find buy strike (spread width below sell)
                chain_map = {s["strike"]: s for s in real_chain["strikes"]}
                for width in range(SPREAD_WIDTH_MIN, SPREAD_WIDTH_MAX + 1, step):
                    buy_strike_candidate = _round_to_step(sell_strike - width, step)
                    actual_width = sell_strike - buy_strike_candidate
                    if actual_width < SPREAD_WIDTH_MIN:
                        continue

                    buy_chain = chain_map.get(buy_strike_candidate, {}).get("PE")
                    if buy_chain and buy_chain.get("premium", 0) > 0:
                        if buy_chain.get("oi", 0) < 100:
                            continue
                        buy_premium = _buy_price(buy_chain)
                        buy_greeks = compute_greeks(spot, buy_strike_candidate, T, RISK_FREE_RATE, iv, "PE")
                        net_credit = sell_premium - buy_premium
                        if net_credit <= 0:
                            continue

                        if real_chain.get("dte"):
                            dte = real_chain["dte"]
                            T = _time_to_expiry(dte)

                        use_real = True
                        # Build the spread rec with real data
                        max_loss_per_lot = (actual_width - net_credit) * lot_size
                        if max_loss_per_lot <= 0:
                            continue

                        lots = 1  # Default to 1 lot — let the user decide to add more
                        trade_qty = lots * lot_size
                        total_credit = net_credit * trade_qty
                        total_max_loss = max_loss_per_lot * lots

                        margin_needed = total_max_loss
                        prob_otm = sell_greeks_real["prob_otm"]
                        delta_val = sell_greeks_real["delta"]
                        theta_day = sell_greeks_real["theta"] - buy_greeks["theta"]

                        otm_pct = (spot - sell_strike) / spot
                        safety_tag = classify_safety(prob_otm, otm_pct)
                        if not passes_risk_filter(safety_tag, settings["risk_profile"]):
                            break

                        ann_return = _annualized_return(total_credit, margin_needed, dte)

                        legs = [
                            _build_leg("SELL", sell_strike, sell_premium, trade_qty, "PE",
                                       tradingsymbol=sell_pe.get("tradingsymbol") if sell_pe else None),
                            _build_leg("BUY", buy_strike_candidate, buy_premium, trade_qty, "PE",
                                       tradingsymbol=buy_chain.get("tradingsymbol") if buy_chain else None),
                        ]
                        fees = _fee_estimate(legs)

                        alts = generate_strike_alternatives(
                            spot=spot, option_type="PE", iv=iv, dte=dte, step=step,
                        )

                        recs.append(_enrich_recommendation({
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
                            "price_source": "kite" if use_real else "simulation",
                            "execution_quality": sell_quality,
                            "fetched_at": _now_ist(),
                        }))
                        break  # First valid width
                if use_real:
                    continue  # Move to next index

        if not use_real:
            # Fall back to Black-Scholes (original behavior)
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

                max_loss_per_lot = (actual_width - net_credit) * lot_size
                if max_loss_per_lot <= 0:
                    continue

                lots = 1  # Default to 1 lot — let the user decide to add more
                trade_qty = lots * lot_size
                total_credit = net_credit * trade_qty
                total_max_loss = max_loss_per_lot * lots

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
                    spot=spot, option_type="PE", iv=iv, dte=dte, step=step,
                )

                recs.append(_enrich_recommendation({
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
                    "price_source": "simulation",
                    "execution_quality": "FAIR",
                    "fetched_at": _now_ist(),
                }))

                # Only take the first valid width per index
                break

    return recs


def _scan_collars(holdings: list, settings: dict, dte: int, kite_service=None) -> list:
    """
    Scan for collar opportunities on profitable stock positions.

    A collar sells an OTM call and buys an OTM put, ideally for near-zero
    net cost, locking in gains on positions with > 8% unrealized profit.
    When kite_service is connected, uses real option chain for premiums.
    """
    recs = []
    T = _time_to_expiry(dte)
    target_delta_call = float(settings.get("manual_target_delta_calls", 0.15))
    target_delta_put = float(settings.get("manual_target_delta_puts", 0.20))
    kite_connected = kite_service and kite_service.is_authenticated()

    for holding in holdings:
        symbol = holding.get("symbol", holding.get("tradingsymbol", ""))
        qty = holding.get("qty", holding.get("quantity", 0))
        avg_cost = holding.get("average_price", holding.get("avgPrice", 0))

        stock_info = _resolve_stock_info(symbol, holding, kite_service=kite_service)
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

        # Determine expiry per-strategy: stocks use monthly expiry
        expiry_date = None
        if kite_connected:
            expiry_date = market_data.get_nearest_expiry(kite_service, symbol)

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

        # Try real option chain from Kite
        real_chain = None
        use_real = False
        if kite_connected and expiry_date:
            real_chain = market_data.get_option_chain_live(kite_service, symbol, expiry_date)

        if real_chain and real_chain.get("strikes"):
            chain_map = {s["strike"]: s for s in real_chain["strikes"]}

            # Find call strike closest to target delta
            best_call = None
            best_call_delta_diff = float("inf")
            for s_data in real_chain["strikes"]:
                ce = s_data.get("CE")
                if not ce or ce.get("premium", 0) <= 0:
                    continue
                if ce.get("oi", 0) < 100:
                    continue
                s_strike = s_data["strike"]
                if s_strike <= spot:
                    continue  # OTM calls only
                s_greeks = compute_greeks(spot, s_strike, T, RISK_FREE_RATE, iv, "CE")
                delta_diff = abs(abs(s_greeks["delta"]) - target_delta_call)
                if delta_diff < best_call_delta_diff:
                    best_call_delta_diff = delta_diff
                    best_call = (s_strike, ce, s_greeks)

            # Find put strike closest to target delta
            best_put = None
            best_put_delta_diff = float("inf")
            for s_data in real_chain["strikes"]:
                pe = s_data.get("PE")
                if not pe or pe.get("premium", 0) <= 0:
                    continue
                if pe.get("oi", 0) < 100:
                    continue
                s_strike = s_data["strike"]
                if s_strike >= spot:
                    continue  # OTM puts only
                s_greeks = compute_greeks(spot, s_strike, T, RISK_FREE_RATE, iv, "PE")
                delta_diff = abs(abs(s_greeks["delta"]) - target_delta_put)
                if delta_diff < best_put_delta_diff:
                    best_put_delta_diff = delta_diff
                    best_put = (s_strike, pe, s_greeks)

            if best_call and best_put:
                call_strike, ce_data, call_greeks = best_call
                put_strike, pe_data, put_greeks = best_put
                passed_ce, reason_ce, quality_ce = execution_check(ce_data)
                passed_pe, reason_pe, quality_pe = execution_check(pe_data)
                if not passed_ce or not passed_pe:
                    continue  # Skip illiquid options
                call_premium = _sell_price(ce_data)
                put_premium = _buy_price(pe_data)
                use_real = True
                if real_chain.get("dte"):
                    dte = real_chain["dte"]
                    T = _time_to_expiry(dte)

        if not use_real:
            call_greeks = compute_greeks(spot, call_strike, T, RISK_FREE_RATE, iv, "CE")
            put_greeks = compute_greeks(spot, put_strike, T, RISK_FREE_RATE, iv, "PE")
            call_premium = call_greeks["price"]
            put_premium = put_greeks["price"]
            quality_ce = "FAIR"

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
            _build_leg("SELL", call_strike, call_premium, trade_qty, "CE",
                       tradingsymbol=ce_data.get("tradingsymbol") if use_real else None),
            _build_leg("BUY", put_strike, put_premium, trade_qty, "PE",
                       tradingsymbol=pe_data.get("tradingsymbol") if use_real else None),
        ]
        fees = _fee_estimate(legs)

        alts = generate_strike_alternatives(
            spot=spot,
            option_type="CE",
            iv=iv,
            dte=dte,
            step=step,
        )

        recs.append(_enrich_recommendation({
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
            "price_source": "kite" if use_real else "simulation",
            "execution_quality": quality_ce,
            "fetched_at": _now_ist(),
        }))

    return recs


# ─── Short Strangle Scanner (Strategy 1 from Trade Strategy doc) ──────────────

def _scan_short_strangles(cash_balance: float, settings: dict, dte: int, kite_service=None, mode="OTM") -> list:
    """
    Scan for Short Strangle opportunities on F&O stocks.

    mode="OTM" (Strategy 1): CE at spot+2%, PE at spot-2%, premium >= 2.5%/4%
    mode="ATM" (Strategy 2): CE and PE at spot, premium >= 3%/5%
    """
    if mode == "ATM":
        otm_pct = 0.0   # ATM — at spot
        min_single_pct = 3.0   # >= 3% of strike
        min_combined_pct = 5.0  # >= 5% of spot
        strategy_label = "ATM_SHORT_STRANGLE"
    else:
        otm_pct = 0.02  # 2% OTM
        min_single_pct = 2.5   # >= 2.5% of strike
        min_combined_pct = 4.0  # >= 4% of spot
        strategy_label = "SHORT_STRANGLE"
    recs = []
    kite_connected = kite_service and kite_service.is_authenticated()

    if not kite_connected:
        return recs  # Need real market data for strangles

    # Get all F&O stocks
    fno_stocks = market_data.get_fno_stock_list(kite_service)
    if not fno_stocks:
        return recs

    # Limit to reasonable number to avoid timeout
    # Prioritize stocks the user holds
    max_stocks = 30

    target_delta = float(settings.get("manual_target_delta_puts", 0.20))

    for symbol in fno_stocks[:max_stocks]:
        try:
            # Check 52-week high filter
            if market_data.is_near_52_week_high(kite_service, symbol, threshold_pct=5):
                continue

            # Get nearest monthly expiry
            expiry_date = market_data.get_nearest_expiry(kite_service, symbol)
            if not expiry_date:
                continue

            actual_dte = max(1, (expiry_date - date.today()).days)
            if actual_dte <= 0:
                continue

            # Get spot price
            try:
                spot_data = kite_service.get_ltp([f"NSE:{symbol}"])
                spot = list(spot_data.values())[0]["last_price"]
            except Exception:
                continue

            if not spot or spot <= 0:
                continue

            # Get strangle chain (2% OTM on each side)
            strangle = market_data.get_strangle_chain(kite_service, symbol, expiry_date, spot, otm_pct=otm_pct)
            if not strangle:
                continue

            ce = strangle["ce"]
            pe = strangle["pe"]
            lot_size = strangle["lot_size"]

            # Execution check on both legs
            passed_ce, reason_ce, quality_ce = execution_check(ce)
            passed_pe, reason_pe, quality_pe = execution_check(pe)
            if not passed_ce or not passed_pe:
                continue  # Skip illiquid options

            # Use bid for SELL legs
            ce_premium_val = _sell_price(ce)
            pe_premium_val = _sell_price(pe)

            # Filter: CE or PE premium >= min% of strike
            ce_pct = (ce_premium_val / ce["strike"]) * 100 if ce["strike"] else 0
            pe_pct = (pe_premium_val / pe["strike"]) * 100 if pe["strike"] else 0
            if ce_pct < min_single_pct and pe_pct < min_single_pct:
                continue

            # Filter: Combined premium >= min% of spot
            if strangle["combined_premium_pct"] < min_combined_pct:
                continue

            # Calculate greeks for both legs
            T = max(actual_dte, 1) / 365.0
            iv_estimate = 0.25  # Default IV estimate
            ce_greeks = compute_greeks(spot, ce["strike"], T, RISK_FREE_RATE, iv_estimate, "CE")
            pe_greeks = compute_greeks(spot, pe["strike"], T, RISK_FREE_RATE, iv_estimate, "PE")

            total_premium = (ce_premium_val + pe_premium_val) * lot_size
            margin_needed = spot * lot_size * 0.20  # ~20% for naked strangle

            prob_otm_combined = ce_greeks["prob_otm"] * pe_greeks["prob_otm"]  # Both expire OTM
            theta_day = (ce_greeks["theta"] + pe_greeks["theta"]) * lot_size
            net_delta = ce_greeks["delta"] + pe_greeks["delta"]

            # Safety classification
            otm_pct = min(
                (ce["strike"] - spot) / spot,
                (spot - pe["strike"]) / spot
            )
            safety_tag = classify_safety(prob_otm_combined, otm_pct)

            if not passes_risk_filter(safety_tag, settings["risk_profile"]):
                continue

            ann_return = _annualized_return(total_premium, margin_needed, actual_dte)

            legs = [
                _build_leg("SELL", ce["strike"], ce_premium_val, lot_size, "CE",
                          tradingsymbol=ce.get("tradingsymbol")),
                _build_leg("SELL", pe["strike"], pe_premium_val, lot_size, "PE",
                          tradingsymbol=pe.get("tradingsymbol")),
            ]

            # BEP points
            combined_premium_bid = ce_premium_val + pe_premium_val
            bep_upper = ce["strike"] + combined_premium_bid
            bep_lower = pe["strike"] - combined_premium_bid

            recs.append(_enrich_recommendation({
                "id": generate_id(),
                "rank": 0,
                "symbol": symbol,
                "strategy_type": strategy_label,
                "strike": ce["strike"],  # Show CE strike as primary
                "put_strike": pe["strike"],
                "option_type": "CE+PE",
                "legs": legs,
                "premium_income": round(total_premium, 2),
                "margin_needed": round(margin_needed, 2),
                "max_loss": round(margin_needed * 2, 2),  # Undefined for strangle, estimate
                "prob_otm": round(prob_otm_combined, 4),
                "delta": round(net_delta, 4),
                "annualized_return": round(ann_return, 4),
                "theta_per_day": round(theta_day, 2),
                "safety_tag": safety_tag,
                "strike_rationale": (
                    f"SHORT_STRANGLE: CE {ce['strike']} ({strangle['ce_premium_pct']:.1f}% of strike) + "
                    f"PE {pe['strike']} ({strangle['pe_premium_pct']:.1f}% of strike). "
                    f"Combined {strangle['combined_premium_pct']:.1f}% of spot. "
                    f"BEP: {bep_lower:.0f} — {bep_upper:.0f}"
                ),
                "alternatives": {},
                "fee_estimate": _fee_estimate(legs),
                "dte": actual_dte,
                "lots": 1,
                "lot_size": lot_size,
                "spot": spot,
                "bep_upper": round(bep_upper, 2),
                "bep_lower": round(bep_lower, 2),
                "combined_premium_pct": strangle["combined_premium_pct"],
                "price_source": "kite",
                "execution_quality": quality_ce,
                "fetched_at": _now_ist(),
            }))

        except Exception:
            continue  # Skip any stock that errors

    return recs


# ─── Iron Condor Scanner (Strategy 5b from Trade Strategy doc) ────────────────

def _scan_iron_condors(cash_balance: float, settings: dict, dte: int, kite_service=None) -> list:
    """
    Scan for Iron Condor opportunities on NIFTY and BANKNIFTY.

    Strategy 5b from Trade Strategy doc:
    - Sell Call at spot+3%
    - Sell Put at spot-3%
    - Buy Put at spot-6% (downside protection)
    - Hold to expiry or roll at 50% leg decay

    Max loss is capped on the downside by the protective put.
    Upside is uncapped (no protective call — this is a jade lizard variant).
    """
    recs = []
    kite_connected = kite_service and kite_service.is_authenticated()

    for index_name in SIMULATION_INDICES:
        try:
            idx = _resolve_index_info(index_name, kite_service=kite_service)
            spot = idx["spot"]
            iv = idx["iv"]
            lot_size = idx["lotSize"]

            if not spot or spot <= 0:
                continue

            # Get expiry
            expiry_date = None
            if kite_connected:
                expiry_date = market_data.get_nearest_expiry(kite_service, index_name)
            if not expiry_date:
                expiry_date = market_data._next_thursday()

            actual_dte = max(1, (expiry_date - date.today()).days)
            if actual_dte <= 0:
                continue

            T = actual_dte / 365.0

            # Strike selection: +3%, -3%, -6% from spot
            step = INDEX_STRIKE_STEP.get(index_name, 50)
            sell_call_strike = _round_to_step(spot * 1.03, step)
            sell_put_strike = _round_to_step(spot * 0.97, step)
            buy_put_strike = _round_to_step(spot * 0.94, step)

            # Try real option chain
            real_chain = None
            use_real = False
            sell_call_premium = 0
            sell_put_premium = 0
            buy_put_premium = 0

            if kite_connected and expiry_date:
                real_chain = market_data.get_option_chain_live(kite_service, index_name, expiry_date, num_strikes=20)

            if real_chain and real_chain.get("strikes"):
                chain_map = {}
                for s in real_chain["strikes"]:
                    chain_map[s["strike"]] = s

                # Find nearest strikes in chain
                def _find_nearest(target, opt_type):
                    best = None
                    best_diff = float("inf")
                    for s in real_chain["strikes"]:
                        opt = s.get(opt_type)
                        if not opt or opt.get("premium", 0) <= 0:
                            continue
                        if opt.get("oi", 0) < 100:
                            continue
                        diff = abs(s["strike"] - target)
                        if diff < best_diff:
                            best_diff = diff
                            best = (s["strike"], opt)
                    return best

                sc = _find_nearest(sell_call_strike, "CE")
                sp = _find_nearest(sell_put_strike, "PE")
                bp = _find_nearest(buy_put_strike, "PE")

                if sc and sp and bp:
                    sell_call_strike, sell_call_data = sc
                    sell_put_strike, sell_put_data = sp
                    buy_put_strike, buy_put_data = bp

                    passed_sc, reason_sc, quality_sc = execution_check(sell_call_data)
                    passed_sp, reason_sp, quality_sp = execution_check(sell_put_data)
                    passed_bp, reason_bp, quality_bp = execution_check(buy_put_data)
                    if not passed_sc or not passed_sp or not passed_bp:
                        pass  # Fall through to BS fallback
                    else:
                        sell_call_premium = _sell_price(sell_call_data)
                        sell_put_premium = _sell_price(sell_put_data)
                        buy_put_premium = _buy_price(buy_put_data)
                        use_real = True

                    # Update lot_size from chain
                    if real_chain.get("lot_size"):
                        lot_size = real_chain["lot_size"]

            if not use_real:
                # Black-Scholes fallback
                sell_call_premium = compute_greeks(spot, sell_call_strike, T, RISK_FREE_RATE, iv, "CE")["price"]
                sell_put_premium = compute_greeks(spot, sell_put_strike, T, RISK_FREE_RATE, iv, "PE")["price"]
                buy_put_premium = compute_greeks(spot, buy_put_strike, T, RISK_FREE_RATE, iv, "PE")["price"]

            net_premium_per_share = sell_call_premium + sell_put_premium - buy_put_premium
            if net_premium_per_share <= 0:
                continue

            total_premium = net_premium_per_share * lot_size

            # Max loss calculation:
            # Downside: capped at (sell_put_strike - buy_put_strike - net_premium) * lot_size
            # Upside: uncapped (no protective call) — use 2x premium as practical max
            put_spread_width = sell_put_strike - buy_put_strike
            max_loss_downside = (put_spread_width - net_premium_per_share) * lot_size
            max_loss_upside = total_premium * 3  # Practical estimate for uncapped upside

            # BEP points
            bep_upper = sell_call_strike + net_premium_per_share
            bep_lower = sell_put_strike - net_premium_per_share

            # Greeks
            sc_greeks = compute_greeks(spot, sell_call_strike, T, RISK_FREE_RATE, iv, "CE")
            sp_greeks = compute_greeks(spot, sell_put_strike, T, RISK_FREE_RATE, iv, "PE")
            bp_greeks = compute_greeks(spot, buy_put_strike, T, RISK_FREE_RATE, iv, "PE")

            net_delta = sc_greeks["delta"] + sp_greeks["delta"] - bp_greeks["delta"]
            net_theta = (sc_greeks["theta"] + sp_greeks["theta"] - bp_greeks["theta"]) * lot_size
            prob_otm = sc_greeks["prob_otm"] * sp_greeks["prob_otm"]

            # Margin: approximate as the put spread width * lot_size (defined risk on put side)
            margin_needed = put_spread_width * lot_size * 1.2  # 20% buffer

            safety_tag = classify_safety(prob_otm, 0.03)  # 3% OTM on both sides
            if not passes_risk_filter(safety_tag, settings["risk_profile"]):
                continue

            ann_return = _annualized_return(total_premium, margin_needed, actual_dte)

            legs = [
                _build_leg("SELL", sell_call_strike, sell_call_premium, lot_size, "CE",
                          tradingsymbol=sell_call_data.get("tradingsymbol") if use_real else None),
                _build_leg("SELL", sell_put_strike, sell_put_premium, lot_size, "PE",
                          tradingsymbol=sell_put_data.get("tradingsymbol") if use_real else None),
                _build_leg("BUY", buy_put_strike, buy_put_premium, lot_size, "PE",
                          tradingsymbol=buy_put_data.get("tradingsymbol") if use_real else None),
            ]

            recs.append(_enrich_recommendation({
                "id": generate_id(),
                "rank": 0,
                "symbol": index_name,
                "strategy_type": "IRON_CONDOR",
                "strike": sell_call_strike,
                "put_strike": sell_put_strike,
                "protection_strike": buy_put_strike,
                "option_type": "CE+PE",
                "legs": legs,
                "premium_income": round(total_premium, 2),
                "margin_needed": round(margin_needed, 2),
                "max_loss": round(max_loss_downside, 2),
                "max_loss_upside": round(max_loss_upside, 2),
                "prob_otm": round(prob_otm, 4),
                "delta": round(net_delta, 4),
                "annualized_return": round(ann_return, 4),
                "theta_per_day": round(net_theta, 2),
                "safety_tag": safety_tag,
                "strike_rationale": (
                    f"IRON_CONDOR: Sell {sell_call_strike}CE + Sell {sell_put_strike}PE + "
                    f"Buy {buy_put_strike}PE (protection). "
                    f"Net premium ₹{net_premium_per_share:.2f}/share. "
                    f"BEP: {bep_lower:.0f} — {bep_upper:.0f}. "
                    f"Max loss (downside): ₹{max_loss_downside:.0f} (capped by protection)"
                ),
                "alternatives": {},
                "fee_estimate": _fee_estimate(legs),
                "dte": actual_dte,
                "lots": 1,
                "lot_size": lot_size,
                "spot": spot,
                "bep_upper": round(bep_upper, 2),
                "bep_lower": round(bep_lower, 2),
                "net_premium_per_share": round(net_premium_per_share, 2),
                "put_spread_width": put_spread_width,
                "price_source": "kite" if use_real else "simulation",
                "execution_quality": quality_sc if use_real else "FAIR",
                "fetched_at": _now_ist(),
            }))

        except Exception:
            continue

    return recs


# ─── RSI-Based Option Selling (Strategy 6B from Trade Strategy doc) ───────────

def _scan_rsi_option_sells(cash_balance: float, settings: dict, dte: int, kite_service=None) -> list:
    """
    Scan for RSI-based option selling opportunities.

    Strategy 6B from Trade Strategy doc:
    - RSI > 70 (overbought) → Sell Call (expect reversal down)
    - RSI < 30 (oversold) → Sell Put (expect reversal up)
    - Exit at 30% premium decay (not 50% like other strategies)
    """
    recs = []
    kite_connected = kite_service and kite_service.is_authenticated()

    if not kite_connected:
        return recs

    # Scan F&O stocks + indices for RSI extremes
    symbols_to_scan = list(SIMULATION_INDICES.keys())
    fno_stocks = market_data.get_fno_stock_list(kite_service)
    symbols_to_scan.extend(fno_stocks[:20])  # Top 20 stocks

    for symbol in symbols_to_scan:
        try:
            rsi = market_data.calculate_rsi(kite_service, symbol)
            signal = market_data.get_rsi_signal(rsi)

            if not signal or signal["action"] is None:
                continue  # RSI in neutral range, skip

            opt_type = signal["option_type"]  # CE or PE

            # Get expiry
            expiry_date = market_data.get_nearest_expiry(kite_service, symbol)
            if not expiry_date:
                continue
            actual_dte = max(1, (expiry_date - date.today()).days)
            if actual_dte <= 0:
                continue

            # Get spot
            try:
                spot_key = f"NSE:{symbol}"
                if symbol == "NIFTY":
                    spot_key = "NSE:NIFTY 50"
                elif symbol in ("BANKNIFTY", "NIFTYBANK"):
                    spot_key = "NSE:NIFTY BANK"
                spot_data = kite_service.get_ltp([spot_key])
                spot = list(spot_data.values())[0]["last_price"]
            except Exception:
                continue
            if not spot:
                continue

            # Get option chain
            chain = market_data.get_option_chain_live(kite_service, symbol, expiry_date, num_strikes=10)
            if not chain or not chain.get("strikes"):
                continue

            lot_size = chain.get("lot_size", 1)

            # Find 3% OTM strike for the direction
            if opt_type == "CE":
                target_strike = spot * 1.03
            else:
                target_strike = spot * 0.97

            # Find best matching strike in chain
            best = None
            for s in chain["strikes"]:
                opt = s.get(opt_type)
                if not opt or opt.get("premium", 0) <= 0 or opt.get("oi", 0) < 100:
                    continue
                if opt_type == "CE" and s["strike"] >= target_strike:
                    if best is None or s["strike"] < best[0]:
                        best = (s["strike"], opt)
                elif opt_type == "PE" and s["strike"] <= target_strike:
                    if best is None or s["strike"] > best[0]:
                        best = (s["strike"], opt)

            if not best:
                continue

            strike, opt_data = best
            passed, reason, quality = execution_check(opt_data)
            if not passed:
                continue  # Skip illiquid options
            premium = _sell_price(opt_data)

            if premium <= 0:
                continue

            total_premium = premium * lot_size
            T = actual_dte / 365.0
            iv_est = 0.25

            greeks = compute_greeks(spot, strike, T, RISK_FREE_RATE, iv_est, opt_type)
            margin_needed = spot * lot_size * 0.15

            otm_pct = abs(strike - spot) / spot
            safety_tag = classify_safety(greeks["prob_otm"], otm_pct)

            if not passes_risk_filter(safety_tag, settings["risk_profile"]):
                continue

            ann_return = _annualized_return(total_premium, margin_needed, actual_dte)

            legs = [
                _build_leg("SELL", strike, premium, lot_size, opt_type,
                          tradingsymbol=opt_data.get("tradingsymbol")),
            ]

            recs.append(_enrich_recommendation({
                "id": generate_id(),
                "rank": 0,
                "symbol": symbol,
                "strategy_type": "RSI_OPTION_SELL",
                "strike": strike,
                "option_type": opt_type,
                "legs": legs,
                "premium_income": round(total_premium, 2),
                "margin_needed": round(margin_needed, 2),
                "max_loss": round(strike * lot_size, 2) if opt_type == "PE" else round(total_premium * 3, 2),
                "prob_otm": round(greeks["prob_otm"], 4),
                "delta": round(greeks["delta"], 4),
                "annualized_return": round(ann_return, 4),
                "theta_per_day": round(greeks["theta"] * lot_size, 2),
                "safety_tag": safety_tag,
                "strike_rationale": (
                    f"RSI_SELL: {signal['label']}. "
                    f"Sell {opt_type} at {strike} ({otm_pct*100:.1f}% OTM). "
                    f"Exit at 30% premium decay (₹{premium*0.7:.2f}/share)."
                ),
                "alternatives": {},
                "fee_estimate": _fee_estimate(legs),
                "dte": actual_dte,
                "lots": 1,
                "lot_size": lot_size,
                "spot": spot,
                "rsi": rsi,
                "rsi_signal": signal["signal"],
                "exit_at_decay_pct": 30,  # Strategy 6B: exit at 30% decay (not 50%)
                "price_source": "kite",
                "execution_quality": quality,
                "fetched_at": _now_ist(),
            }))

        except Exception:
            continue

    return recs


# ─── Calendar Spread Scanner (Strategy 4 from Trade Strategy doc) ─────────────

def _scan_calendar_spreads(cash_balance: float, settings: dict, dte: int, kite_service=None) -> list:
    """
    Scan for Calendar Spread opportunities on F&O stocks.

    Strategy 4 from Trade Strategy doc:
    - Sell current month's highest-premium option (CE side if call premium > put)
    - Buy same strike, next month option
    - Net cost = next_month_premium - current_month_premium (debit spread)
    - Exit A: at 25% profit on total position
    - Exit B: when current month decays 40%, roll
    """
    recs = []
    kite_connected = kite_service and kite_service.is_authenticated()

    if not kite_connected:
        return recs

    # Scan F&O stocks
    fno_stocks = market_data.get_fno_stock_list(kite_service)
    if not fno_stocks:
        return recs

    for symbol in fno_stocks[:20]:
        try:
            # Get available expiries — need at least 2
            expiries = market_data.get_available_expiries(kite_service, symbol)
            if len(expiries) < 2:
                continue

            near_expiry = expiries[0]  # Current month
            far_expiry = expiries[1]   # Next month

            near_dte = max(1, (near_expiry - date.today()).days)
            far_dte = max(1, (far_expiry - date.today()).days)

            if near_dte <= 0:
                continue

            # Get spot
            try:
                spot_data = kite_service.get_ltp([f"NSE:{symbol}"])
                spot = list(spot_data.values())[0]["last_price"]
            except Exception:
                continue
            if not spot:
                continue

            # Get option chains for both expiries
            near_chain = market_data.get_option_chain_live(kite_service, symbol, near_expiry, num_strikes=10)
            far_chain = market_data.get_option_chain_live(kite_service, symbol, far_expiry, num_strikes=10)

            if not near_chain or not far_chain:
                continue

            lot_size = near_chain.get("lot_size", 1)

            # Find ATM strike with highest premium in near month
            # Check which side (CE or PE) has higher premium
            best_near = None
            for s in near_chain["strikes"]:
                for opt_type in ("CE", "PE"):
                    opt = s.get(opt_type)
                    if not opt or opt.get("premium", 0) <= 0 or opt.get("oi", 0) < 100:
                        continue
                    if best_near is None or opt["premium"] > best_near[2]:
                        best_near = (s["strike"], opt_type, opt["premium"], opt)

            if not best_near:
                continue

            strike, opt_type, near_premium_raw, near_opt = best_near
            passed_near, reason_near, quality_near = execution_check(near_opt)
            if not passed_near:
                continue  # Skip illiquid options
            near_premium = _sell_price(near_opt)

            # Find same strike in far month
            far_opt = None
            for s in far_chain["strikes"]:
                if s["strike"] == strike:
                    far_opt = s.get(opt_type)
                    break

            if not far_opt or far_opt.get("premium", 0) <= 0:
                continue

            far_premium = _buy_price(far_opt)

            # Calendar spread: sell near, buy far — net debit
            net_debit_per_share = far_premium - near_premium
            if net_debit_per_share <= 0:
                # Credit calendar — unusual but possible
                continue

            total_debit = net_debit_per_share * lot_size
            # Max profit ≈ near premium collected (when near expires worthless and far retains value)
            max_profit_estimate = near_premium * lot_size
            # Max loss = net debit paid
            max_loss = total_debit

            # Premium filter from doc: option premium >= 3% of strike
            if near_premium / strike < 0.03:
                continue

            # 52-week high check
            if market_data.is_near_52_week_high(kite_service, symbol, threshold_pct=5):
                continue

            T_near = near_dte / 365.0
            iv_est = 0.25
            greeks = compute_greeks(spot, strike, T_near, RISK_FREE_RATE, iv_est, opt_type)

            # Margin ≈ debit paid (defined risk)
            margin_needed = total_debit * 1.2  # 20% buffer

            ann_return = _annualized_return(max_profit_estimate * 0.25, margin_needed, near_dte)  # 25% target

            safety_tag = classify_safety(greeks["prob_otm"], abs(strike - spot) / spot)
            if not passes_risk_filter(safety_tag, settings["risk_profile"]):
                continue

            legs = [
                _build_leg("SELL", strike, near_premium, lot_size, opt_type,
                          tradingsymbol=near_opt.get("tradingsymbol")),
                _build_leg("BUY", strike, far_premium, lot_size, opt_type,
                          tradingsymbol=far_opt.get("tradingsymbol")),
            ]

            near_display = near_expiry.strftime("%d %b").upper()
            far_display = far_expiry.strftime("%d %b").upper()

            recs.append(_enrich_recommendation({
                "id": generate_id(),
                "rank": 0,
                "symbol": symbol,
                "strategy_type": "CALENDAR_SPREAD",
                "strike": strike,
                "option_type": opt_type,
                "legs": legs,
                "premium_income": round(max_profit_estimate, 2),  # Potential profit
                "margin_needed": round(margin_needed, 2),
                "max_loss": round(max_loss, 2),
                "prob_otm": round(greeks["prob_otm"], 4),
                "delta": round(greeks["delta"], 4),
                "annualized_return": round(ann_return, 4),
                "theta_per_day": round(greeks["theta"] * lot_size, 2),
                "safety_tag": safety_tag,
                "strike_rationale": (
                    f"CALENDAR_SPREAD: Sell {near_display} {strike}{opt_type} @₹{near_premium:.2f}, "
                    f"Buy {far_display} {strike}{opt_type} @₹{far_premium:.2f}. "
                    f"Net debit ₹{net_debit_per_share:.2f}/share. "
                    f"Exit at 25% profit or roll at 40% near-month decay."
                ),
                "alternatives": {},
                "fee_estimate": _fee_estimate(legs),
                "dte": near_dte,
                "far_dte": far_dte,
                "lots": 1,
                "lot_size": lot_size,
                "spot": spot,
                "near_expiry": near_display,
                "far_expiry": far_display,
                "net_debit_per_share": round(net_debit_per_share, 2),
                "exit_at_profit_pct": 25,  # Strategy 4: exit at 25% profit
                "exit_at_decay_pct": 40,   # Strategy 4: roll at 40% near decay
                "price_source": "kite",
                "execution_quality": quality_near,
                "fetched_at": _now_ist(),
            }))

        except Exception:
            continue

    return recs


# ─── Ranking ──────────────────────────────────────────────────────────────────

def rank_recommendations(recs: list) -> list:
    """
    Rank recommendations by safety first, then by risk-adjusted return.
    When sentiment is RED, risk-adjusted return is used (penalizes risky trades).
    Otherwise uses raw annualized return.
    """
    sorted_recs = sorted(
        recs,
        key=lambda r: (
            SAFETY_TAG_ORDER.get(r.get("safety_tag", "MODERATE"), 99),
            -(r.get("risk_adjusted_return") or r.get("annualized_return", 0)),
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
    kite_service=None,
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
        kite_service: Optional KiteService instance. When authenticated,
                      enables real option chains and live lot sizes.

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

    # Fetch VIX for market condition awareness (Prompt 4)
    vix_value = vix_service.get_india_vix(kite_service)
    vix_signal = vix_service.get_vix_signal(vix_value)

    # Adjust delta targets based on VIX
    if vix_value:
        base_delta_puts = float(resolved.get("manual_target_delta_puts", 0.20))
        base_delta_calls = float(resolved.get("manual_target_delta_calls", 0.15))
        resolved["manual_target_delta_puts"] = vix_service.get_vix_adjusted_delta_target(vix_value, base_delta_puts)
        resolved["manual_target_delta_calls"] = vix_service.get_vix_adjusted_delta_target(vix_value, base_delta_calls)

    all_recs = []
    kite_connected = kite_service and kite_service.is_authenticated()

    # Scan across all expiries within 30 days
    # For each index/stock, get all available expiries and scan each
    index_symbols = list(SIMULATION_INDICES.keys())
    scanned_expiries = {}  # cache: symbol → [expiry_dates]

    def _get_expiries_for(symbol):
        if symbol not in scanned_expiries:
            if kite_connected:
                expiries = market_data.get_expiries_within_days(kite_service, symbol, max_days=30)
            else:
                expiries = [market_data.get_nearest_expiry(kite_service, symbol)]
            scanned_expiries[symbol] = expiries if expiries else [None]
        return scanned_expiries[symbol]

    if "COVERED_CALL" in allowed:
        # Covered calls: scan each stock holding across all its expiries
        for holding in holdings:
            sym = holding.get("symbol", holding.get("tradingsymbol", "")).upper()
            if not sym:
                continue
            for exp in _get_expiries_for(sym):
                exp_dte = max(1, (exp - date.today()).days) if exp else dte
                all_recs.extend(_scan_covered_calls([holding], resolved, exp_dte, kite_service=kite_service))

    if "CASH_SECURED_PUT" in allowed:
        for idx in index_symbols:
            for exp in _get_expiries_for(idx):
                exp_dte = max(1, (exp - date.today()).days) if exp else dte
                all_recs.extend(_scan_cash_secured_puts(cash_balance, resolved, exp_dte, kite_service=kite_service))

    if "PUT_CREDIT_SPREAD" in allowed:
        for idx in index_symbols:
            for exp in _get_expiries_for(idx):
                exp_dte = max(1, (exp - date.today()).days) if exp else dte
                all_recs.extend(_scan_put_credit_spreads(cash_balance, resolved, exp_dte, kite_service=kite_service))

    if "COLLAR" in allowed:
        for holding in holdings:
            sym = holding.get("symbol", holding.get("tradingsymbol", "")).upper()
            if not sym:
                continue
            for exp in _get_expiries_for(sym):
                exp_dte = max(1, (exp - date.today()).days) if exp else dte
                all_recs.extend(_scan_collars([holding], resolved, exp_dte, kite_service=kite_service))

    if "SHORT_STRANGLE" in allowed:
        all_recs.extend(_scan_short_strangles(cash_balance, resolved, dte, kite_service=kite_service, mode="OTM"))
        all_recs.extend(_scan_short_strangles(cash_balance, resolved, dte, kite_service=kite_service, mode="ATM"))

    if "IRON_CONDOR" in allowed:
        for idx in index_symbols:
            for exp in _get_expiries_for(idx):
                exp_dte = max(1, (exp - date.today()).days) if exp else dte
                all_recs.extend(_scan_iron_condors(cash_balance, resolved, exp_dte, kite_service=kite_service))

    if "RSI_OPTION_SELL" in allowed:
        all_recs.extend(_scan_rsi_option_sells(cash_balance, resolved, dte, kite_service=kite_service))

    if "CALENDAR_SPREAD" in allowed:
        all_recs.extend(_scan_calendar_spreads(cash_balance, resolved, dte, kite_service=kite_service))

    # Filter out negative premium strategies
    all_recs = [r for r in all_recs if r.get("premium_income", 0) > 0]

    # Filter out already expired options (DTE <= 0)
    all_recs = [r for r in all_recs if r.get("dte", 0) > 0]

    # Don't filter by price_source — let confidence/decision handle quality
    # Simulation-based recs get lower confidence naturally via execution_quality

    # Compute per-strategy expiry and add expiry info to recs and legs
    kite_connected = kite_service and kite_service.is_authenticated()
    for rec in all_recs:
        symbol = rec.get("symbol", "")
        strategy_type = rec.get("strategy_type", "")

        # Each strategy determines its own expiry based on symbol type
        if kite_connected:
            expiry_date = market_data.get_nearest_expiry(kite_service, symbol)
        else:
            # Fallback: use market_data fallback expiries (weekly for indices, monthly for stocks)
            fallback_expiries = market_data._fallback_expiries(symbol)
            expiry_date = fallback_expiries[0] if fallback_expiries else None

        if expiry_date:
            expiry_str = expiry_date.strftime("%d %b").upper()  # e.g. "03 APR"
            expiry_iso = expiry_date.isoformat()
        else:
            from kite_service import KiteService
            expiry_date = KiteService._next_thursday()
            expiry_str = expiry_date.strftime("%d %b").upper()
            expiry_iso = expiry_date.isoformat()

        rec["expiry_date"] = expiry_iso
        rec["expiry_display"] = expiry_str
        if rec.get("legs"):
            for leg in rec["legs"]:
                leg["expiry_date"] = expiry_iso
                leg["expiry_display"] = expiry_str
                # Use tradingsymbol from Kite if available (e.g. BANKNIFTY2633051800PE)
                # Only construct names as fallback for simulation mode
                if leg.get("tradingsymbol"):
                    leg["instrument"] = leg["tradingsymbol"]
                else:
                    leg["instrument"] = f"{rec['symbol']} {expiry_str} {int(leg['strike'])} {leg['option_type']}"

    # Deduplicate: per symbol + strategy + expiry, keep best annualized return
    seen = {}
    deduped = []
    for rec in all_recs:
        key = f"{rec['symbol']}_{rec['strategy_type']}_{rec.get('expiry_date','')}"
        if key in seen:
            if rec.get("annualized_return", 0) > seen[key].get("annualized_return", 0):
                deduped = [r for r in deduped if f"{r['symbol']}_{r['strategy_type']}_{r.get('expiry_date','')}" != key]
                deduped.append(rec)
                seen[key] = rec
        else:
            deduped.append(rec)
            seen[key] = rec
    all_recs = deduped

    # Fetch sentiment for risk adjustment
    try:
        import sentiment_engine
        sentiment = sentiment_engine.get_sentiment(kite_service)
        sentiment_signal = sentiment.get("signal", "YELLOW") if sentiment else "YELLOW"
        sentiment_score = sentiment.get("score", 50) if sentiment else 50
    except Exception:
        sentiment_signal = "YELLOW"
        sentiment_score = 50

    # Risk adjustment factors
    RISK_FACTOR = {"GREEN": 1.0, "YELLOW": 0.7, "RED": 0.4}
    risk_factor = RISK_FACTOR.get(sentiment_signal, 0.7)

    # Sentiment context notes per signal
    SENTIMENT_NOTES = {
        "GREEN": "Market conditions favourable — standard position sizing",
        "YELLOW": "Mixed signals — proceed with caution, consider smaller size",
        "RED": "Elevated risk — premiums are rich but volatility high. Use minimum lots.",
    }

    # Add VIX + sentiment info to each recommendation
    for rec in all_recs:
        rec["vix_at_scan"] = vix_value
        rec["vix_adjusted"] = bool(vix_value)
        rec["vix_signal"] = vix_signal
        rec["sentiment_signal"] = sentiment_signal
        rec["sentiment_score"] = sentiment_score

        # Risk-adjusted annualized return
        ann_return = rec.get("annualized_return", 0)
        rec["risk_adjusted_return"] = round(ann_return * risk_factor, 2)
        rec["risk_factor"] = risk_factor
        rec["sentiment_note"] = SENTIMENT_NOTES.get(sentiment_signal, "")

    # Add market status to each recommendation
    from execution_filter import get_market_status
    market_status = get_market_status()
    for rec in all_recs:
        rec["market_status"] = market_status

    # Fetch real margins from Kite for sell legs (if connected)
    if kite_connected:
        for rec in all_recs:
            legs = rec.get("legs", [])
            for leg in legs:
                ts = leg.get("tradingsymbol")
                action = (leg.get("action") or "").upper()
                if ts and action == "SELL":
                    real_margin = kite_service.get_order_margin(
                        ts, "SELL", leg.get("quantity", 1)
                    )
                    if real_margin:
                        rec["margin_needed"] = real_margin
                        rec["margin_source"] = "kite"
                    break  # Only need margin for the first sell leg

    # Capital utilization + portfolio delta (Prompt 6)
    margin_data = portfolio_risk.get_available_margin(kite_service)
    port_delta = portfolio_risk.get_portfolio_delta(kite_service)
    if margin_data["available"] > 0:
        all_recs = portfolio_risk.enrich_with_capital_utilization(all_recs, margin_data["available"])
    all_recs = portfolio_risk.enrich_with_delta_impact(all_recs, port_delta)

    # Summary: total margin required across all recommendations
    total_margin_required = sum(r.get("margin_needed", 0) for r in all_recs)
    for rec in all_recs:
        rec["total_margin_required"] = round(total_margin_required, 2)

    ranked = rank_recommendations(all_recs)
    return ranked
