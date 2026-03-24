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
        qty = holding.get("quantity", 0)

        stock_info = SIMULATION_STOCKS.get(symbol)
        if not stock_info:
            continue

        lot_size = stock_info["lotSize"]
        if qty < lot_size:
            continue

        spot = stock_info["ltp"]
        iv = stock_info["iv"]
        avg_cost = holding.get("average_price", spot)

        # Select strike via strike_selector
        strike = select_strike_price(
            spot=spot,
            option_type="CE",
            target_delta=target_delta,
            iv=iv,
            dte=dte,
            step=STOCK_STRIKE_STEP,
        )
        if strike <= spot:
            # Ensure the call strike is OTM
            strike = _round_to_step(spot * (1 + 0.02), STOCK_STRIKE_STEP)

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
            step=STOCK_STRIKE_STEP,
        )

        recs.append({
            "id": generate_id(),
            "rank": 0,
            "symbol": symbol,
            "strategy_type": "COVERED_CALL",
            "strike": strike,
            "option_type": "CE",
            "legs": legs,
            "premium_income": round(total_premium, 2),
            "margin_needed": 0,
            "max_loss": round((spot - avg_cost) * trade_qty, 2),  # downside risk from stock
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
        })

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

    for index_name, index_info in SIMULATION_INDICES.items():
        spot = index_info["spot"]
        iv = index_info["iv"]
        lot_size = index_info["lotSize"]
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

        recs.append({
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
        })

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

    for index_name, index_info in SIMULATION_INDICES.items():
        spot = index_info["spot"]
        iv = index_info["iv"]
        lot_size = index_info["lotSize"]
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

            recs.append({
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
            })

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
        qty = holding.get("quantity", 0)
        avg_cost = holding.get("average_price", 0)

        stock_info = SIMULATION_STOCKS.get(symbol)
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

        # Sell OTM call
        call_strike = select_strike_price(
            spot=spot,
            option_type="CE",
            target_delta=target_delta_call,
            iv=iv,
            dte=dte,
            step=STOCK_STRIKE_STEP,
        )
        if call_strike <= spot:
            call_strike = _round_to_step(spot * (1 + 0.03), STOCK_STRIKE_STEP)

        # Buy OTM put
        put_strike = select_strike_price(
            spot=spot,
            option_type="PE",
            target_delta=target_delta_put,
            iv=iv,
            dte=dte,
            step=STOCK_STRIKE_STEP,
        )
        if put_strike >= spot:
            put_strike = _round_to_step(spot * (1 - 0.03), STOCK_STRIKE_STEP)

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
            step=STOCK_STRIKE_STEP,
        )

        recs.append({
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
        })

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
