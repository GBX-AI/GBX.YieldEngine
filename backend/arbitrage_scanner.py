"""
Arbitrage Scanner for Yield Engine v3.

Scans for three arbitrage types:
  1. Cash-Futures Arbitrage — spot vs futures basis exceeding carry cost
  2. Put-Call Parity Arbitrage — violations of C - P = S - K*e^(-rT)
  3. Calendar Spread Arbitrage — near vs far month time-value mispricing

Operates in simulation mode when live market data is unavailable,
generating realistic sample opportunities from SIMULATION_INDICES.
"""

import math
import random
from datetime import datetime, timedelta
from typing import Any

from models import SIMULATION_STOCKS, SIMULATION_INDICES, generate_id
from black_scholes import option_price, RISK_FREE_RATE
from fee_calculator import calculate_trade_fees

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TRANSACTION_COST_BPS = 15  # 15 bps round-trip (7.5 each way)
TRANSACTION_COST_PCT = TRANSACTION_COST_BPS / 10_000

ARB_TYPES = {
    "CASH_FUTURES_ARB": "Cash-Futures Arbitrage",
    "PUT_CALL_PARITY_ARB": "Put-Call Parity Arbitrage",
    "CALENDAR_SPREAD_ARB": "Calendar Spread Arbitrage",
}

# Margin requirements as fraction of notional
MARGIN_CASH_FUTURES = 0.20  # ~20% combined margin for futures + cash leg
MARGIN_PCP = 0.15           # ~15% for synthetic conversion
MARGIN_CALENDAR = 0.12      # ~12% for calendar spread

# Minimum annualized return threshold to surface an opportunity (after costs)
MIN_ANNUALIZED_RETURN = 0.005  # 0.5%


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------

def scan_arbitrage(
    market_data: dict[str, Any] | None = None,
    simulation: bool = True,
) -> list[dict[str, Any]]:
    """
    Main entry point. Scan for arbitrage opportunities across all types.

    Args:
        market_data: Live market data dict keyed by symbol. When None and
                     simulation=True, synthetic data is generated from
                     SIMULATION_INDICES / SIMULATION_STOCKS.
        simulation:  If True and market_data is None, run in simulation mode.

    Returns:
        List of opportunity dicts sorted by annualized_return descending.
    """
    opportunities: list[dict[str, Any]] = []

    if market_data is None and simulation:
        opportunities.extend(_simulate_cash_futures_arbs())
        opportunities.extend(_simulate_put_call_parity_arbs())
        opportunities.extend(_simulate_calendar_spread_arbs())
    elif market_data is not None:
        opportunities.extend(_scan_cash_futures(market_data))
        opportunities.extend(_scan_put_call_parity(market_data))
        opportunities.extend(_scan_calendar_spread(market_data))

    # Sort by annualized return, best first
    opportunities.sort(key=lambda o: o["annualized_return"], reverse=True)
    return opportunities


# ---------------------------------------------------------------------------
# Live scanners (require real market data)
# ---------------------------------------------------------------------------

def _scan_cash_futures(market_data: dict) -> list[dict]:
    """
    Cash-Futures Arbitrage: compare spot vs near-month futures.

    Profitable when:
        annualized_basis > risk_free_rate + transaction_costs

    annualized_basis = (futures - spot) / spot * (365 / days_to_expiry)
    """
    opps: list[dict] = []

    for symbol, data in market_data.items():
        spot = data.get("spot") or data.get("ltp")
        futures_price = data.get("futures_price")
        days_to_expiry = data.get("days_to_expiry")
        lot_size = data.get("lotSize", data.get("lot_size", 1))

        if not all([spot, futures_price, days_to_expiry]):
            continue
        if days_to_expiry <= 0:
            continue

        basis = futures_price - spot
        annualized_basis = (basis / spot) * (365 / days_to_expiry)
        hurdle = RISK_FREE_RATE + TRANSACTION_COST_PCT

        if annualized_basis <= hurdle:
            continue

        # Calculate concrete P&L for one lot
        notional = spot * lot_size
        profit_per_lot = basis * lot_size
        carry_cost = notional * RISK_FREE_RATE * (days_to_expiry / 365)
        margin_needed = notional * MARGIN_CASH_FUTURES

        fee_legs = [
            {"action": "BUY", "premium": spot, "quantity": lot_size},
            {"action": "SELL", "premium": futures_price, "quantity": lot_size},
        ]
        fees = calculate_trade_fees(fee_legs)
        expected_profit = profit_per_lot - carry_cost - fees["total"]

        if expected_profit <= 0:
            continue

        net_annualized = (expected_profit / margin_needed) * (365 / days_to_expiry)

        opps.append(_build_opportunity(
            arb_type="CASH_FUTURES_ARB",
            symbol=symbol,
            legs=[
                {
                    "instrument": f"{symbol}-SPOT",
                    "action": "BUY",
                    "quantity": lot_size,
                    "price": round(spot, 2),
                    "type": "EQ",
                },
                {
                    "instrument": f"{symbol}-FUT",
                    "action": "SELL",
                    "quantity": lot_size,
                    "price": round(futures_price, 2),
                    "type": "FUT",
                    "expiry_days": days_to_expiry,
                },
            ],
            annualized_return=round(net_annualized, 4),
            margin_needed=round(margin_needed, 2),
            holding_days=days_to_expiry,
            entry_cost=round(notional, 2),
            expected_profit=round(expected_profit, 2),
            fee_estimate=round(fees["total"], 2),
            risk_free=True,
            basis_bps=round(annualized_basis * 10_000, 1),
        ))

    return opps


def _scan_put_call_parity(market_data: dict) -> list[dict]:
    """
    Put-Call Parity Arbitrage: C - P should equal S - K*e^(-rT).

    For each (symbol, strike, expiry), check:
        deviation = |call_price - put_price - (spot - strike * e^(-r*T))|
    Profitable if deviation > transaction_costs.
    """
    opps: list[dict] = []

    for symbol, data in market_data.items():
        spot = data.get("spot") or data.get("ltp")
        option_chain = data.get("option_chain", [])
        lot_size = data.get("lotSize", data.get("lot_size", 1))

        if not spot or not option_chain:
            continue

        for opt in option_chain:
            strike = opt.get("strike")
            call_px = opt.get("call_price")
            put_px = opt.get("put_price")
            days_to_expiry = opt.get("days_to_expiry")

            if not all([strike, call_px, put_px, days_to_expiry]):
                continue
            if days_to_expiry <= 0:
                continue

            T = days_to_expiry / 365
            pv_strike = strike * math.exp(-RISK_FREE_RATE * T)
            theoretical_diff = spot - pv_strike  # C - P should equal this
            actual_diff = call_px - put_px
            deviation = actual_diff - theoretical_diff

            abs_deviation = abs(deviation)
            cost_threshold = spot * TRANSACTION_COST_PCT

            if abs_deviation <= cost_threshold:
                continue

            # Determine direction: which side is cheap?
            if deviation > 0:
                # Actual C-P too high -> sell call, buy put, buy stock
                action_call, action_put, action_stock = "SELL", "BUY", "BUY"
                direction = "Reverse Conversion"
            else:
                # Actual C-P too low -> buy call, sell put, sell stock
                action_call, action_put, action_stock = "BUY", "SELL", "SELL"
                direction = "Conversion"

            notional = spot * lot_size
            margin_needed = notional * MARGIN_PCP
            gross_profit = abs_deviation * lot_size

            fee_legs = [
                {"action": action_call, "premium": call_px, "quantity": lot_size},
                {"action": action_put, "premium": put_px, "quantity": lot_size},
                {"action": action_stock, "premium": spot, "quantity": lot_size},
            ]
            fees = calculate_trade_fees(fee_legs)
            expected_profit = gross_profit - fees["total"]

            if expected_profit <= 0:
                continue

            net_annualized = (expected_profit / margin_needed) * (365 / days_to_expiry)

            opps.append(_build_opportunity(
                arb_type="PUT_CALL_PARITY_ARB",
                symbol=symbol,
                legs=[
                    {
                        "instrument": f"{symbol}-{strike}CE",
                        "action": action_call,
                        "quantity": lot_size,
                        "price": round(call_px, 2),
                        "type": "CE",
                        "strike": strike,
                        "expiry_days": days_to_expiry,
                    },
                    {
                        "instrument": f"{symbol}-{strike}PE",
                        "action": action_put,
                        "quantity": lot_size,
                        "price": round(put_px, 2),
                        "type": "PE",
                        "strike": strike,
                        "expiry_days": days_to_expiry,
                    },
                    {
                        "instrument": f"{symbol}-SPOT",
                        "action": action_stock,
                        "quantity": lot_size,
                        "price": round(spot, 2),
                        "type": "EQ",
                    },
                ],
                annualized_return=round(net_annualized, 4),
                margin_needed=round(margin_needed, 2),
                holding_days=days_to_expiry,
                entry_cost=round(notional, 2),
                expected_profit=round(expected_profit, 2),
                fee_estimate=round(fees["total"], 2),
                risk_free=True,
                direction=direction,
                deviation_pts=round(abs_deviation, 2),
            ))

    return opps


def _scan_calendar_spread(market_data: dict) -> list[dict]:
    """
    Calendar Spread Arbitrage: near vs far month on same strike.

    Buy the cheaper expiry, sell the more expensive one.
    Profitable if time-value difference exceeds carry cost.
    """
    opps: list[dict] = []

    for symbol, data in market_data.items():
        spot = data.get("spot") or data.get("ltp")
        calendar_pairs = data.get("calendar_pairs", [])
        lot_size = data.get("lotSize", data.get("lot_size", 1))

        if not spot or not calendar_pairs:
            continue

        for pair in calendar_pairs:
            strike = pair.get("strike")
            opt_type = pair.get("type", "CE")
            near_price = pair.get("near_price")
            far_price = pair.get("far_price")
            near_dte = pair.get("near_dte")
            far_dte = pair.get("far_dte")
            iv_near = pair.get("iv_near")
            iv_far = pair.get("iv_far")

            if not all([strike, near_price, far_price, near_dte, far_dte]):
                continue
            if near_dte <= 0 or far_dte <= near_dte:
                continue

            time_value_diff = far_price - near_price
            holding_days = far_dte - near_dte
            carry_cost = spot * RISK_FREE_RATE * (holding_days / 365) * MARGIN_CALENDAR

            if time_value_diff <= 0:
                continue

            notional = spot * lot_size
            margin_needed = notional * MARGIN_CALENDAR
            gross_profit = time_value_diff * lot_size

            fee_legs = [
                {"action": "BUY", "premium": near_price, "quantity": lot_size},
                {"action": "SELL", "premium": far_price, "quantity": lot_size},
            ]
            fees = calculate_trade_fees(fee_legs)
            expected_profit = gross_profit - carry_cost - fees["total"]

            if expected_profit <= 0:
                continue

            net_annualized = (expected_profit / margin_needed) * (365 / holding_days)

            if net_annualized < MIN_ANNUALIZED_RETURN:
                continue

            opps.append(_build_opportunity(
                arb_type="CALENDAR_SPREAD_ARB",
                symbol=symbol,
                legs=[
                    {
                        "instrument": f"{symbol}-{strike}{opt_type}-NEAR",
                        "action": "SELL",
                        "quantity": lot_size,
                        "price": round(near_price, 2),
                        "type": opt_type,
                        "strike": strike,
                        "expiry_days": near_dte,
                    },
                    {
                        "instrument": f"{symbol}-{strike}{opt_type}-FAR",
                        "action": "BUY",
                        "quantity": lot_size,
                        "price": round(far_price, 2),
                        "type": opt_type,
                        "strike": strike,
                        "expiry_days": far_dte,
                    },
                ],
                annualized_return=round(net_annualized, 4),
                margin_needed=round(margin_needed, 2),
                holding_days=holding_days,
                entry_cost=round(abs(far_price - near_price) * lot_size, 2),
                expected_profit=round(expected_profit, 2),
                fee_estimate=round(fees["total"], 2),
                risk_free=False,
                iv_near=iv_near,
                iv_far=iv_far,
            ))

    return opps


# ---------------------------------------------------------------------------
# Simulation generators
# ---------------------------------------------------------------------------

def _simulate_cash_futures_arbs() -> list[dict]:
    """Generate realistic cash-futures arbitrage opportunities from SIMULATION_INDICES."""
    opps: list[dict] = []
    now = datetime.utcnow()

    for symbol, info in SIMULATION_INDICES.items():
        spot = info["spot"]
        lot_size = info["lotSize"]

        # Simulate near-month futures (typically 5-30 DTE)
        for dte_bucket in [7, 14, 21, 28]:
            # Futures trade at a premium reflecting cost-of-carry + sentiment
            # Inject slight mispricing to create opportunities
            fair_basis = spot * RISK_FREE_RATE * (dte_bucket / 365)
            # Random premium above fair value (40-120 bps annualized above carry)
            excess_bps = random.uniform(0.004, 0.012)
            excess_premium = spot * excess_bps * (dte_bucket / 365)
            futures_price = spot + fair_basis + excess_premium

            # Add realistic noise
            futures_price = round(futures_price + random.uniform(-2, 2), 2)

            basis = futures_price - spot
            annualized_basis = (basis / spot) * (365 / dte_bucket)
            hurdle = RISK_FREE_RATE + TRANSACTION_COST_PCT

            if annualized_basis <= hurdle:
                continue

            notional = spot * lot_size
            profit_per_lot = basis * lot_size
            carry_cost = notional * RISK_FREE_RATE * (dte_bucket / 365)
            margin_needed = notional * MARGIN_CASH_FUTURES

            fee_legs = [
                {"action": "BUY", "premium": spot, "quantity": lot_size},
                {"action": "SELL", "premium": futures_price, "quantity": lot_size},
            ]
            fees = calculate_trade_fees(fee_legs)
            expected_profit = profit_per_lot - carry_cost - fees["total"]

            if expected_profit <= 0:
                continue

            net_annualized = (expected_profit / margin_needed) * (365 / dte_bucket)

            expiry_date = (now + timedelta(days=dte_bucket)).strftime("%Y-%m-%d")

            opps.append(_build_opportunity(
                arb_type="CASH_FUTURES_ARB",
                symbol=symbol,
                legs=[
                    {
                        "instrument": f"{symbol}-SPOT",
                        "action": "BUY",
                        "quantity": lot_size,
                        "price": round(spot, 2),
                        "type": "EQ",
                    },
                    {
                        "instrument": f"{symbol}-FUT-{expiry_date}",
                        "action": "SELL",
                        "quantity": lot_size,
                        "price": round(futures_price, 2),
                        "type": "FUT",
                        "expiry_days": dte_bucket,
                        "expiry_date": expiry_date,
                    },
                ],
                annualized_return=round(net_annualized, 4),
                margin_needed=round(margin_needed, 2),
                holding_days=dte_bucket,
                entry_cost=round(notional, 2),
                expected_profit=round(expected_profit, 2),
                fee_estimate=round(fees["total"], 2),
                risk_free=True,
                basis_bps=round(annualized_basis * 10_000, 1),
            ))

    return opps


def _simulate_put_call_parity_arbs() -> list[dict]:
    """Generate realistic put-call parity violations from SIMULATION_INDICES."""
    opps: list[dict] = []
    now = datetime.utcnow()

    for symbol, info in SIMULATION_INDICES.items():
        spot = info["spot"]
        lot_size = info["lotSize"]
        iv = info["iv"]

        # Check strikes around ATM
        strike_step = 50 if symbol == "NIFTY" else 100
        atm_strike = round(spot / strike_step) * strike_step

        for offset in [-2, -1, 0, 1, 2]:
            strike = atm_strike + offset * strike_step

            for dte in [7, 14, 21]:
                T = dte / 365

                # Theoretical prices
                theo_call = option_price(spot, strike, T, RISK_FREE_RATE, iv, "CE")
                theo_put = option_price(spot, strike, T, RISK_FREE_RATE, iv, "PE")

                # Inject mispricing: skew one side by 1-5 points
                skew = random.uniform(1.5, 5.0) * random.choice([-1, 1])
                sim_call = max(0.5, theo_call + skew * 0.6)
                sim_put = max(0.5, theo_put - skew * 0.4)

                pv_strike = strike * math.exp(-RISK_FREE_RATE * T)
                theoretical_diff = spot - pv_strike
                actual_diff = sim_call - sim_put
                deviation = actual_diff - theoretical_diff
                abs_deviation = abs(deviation)

                cost_threshold = spot * TRANSACTION_COST_PCT
                if abs_deviation <= cost_threshold:
                    continue

                if deviation > 0:
                    action_call, action_put, action_stock = "SELL", "BUY", "BUY"
                    direction = "Reverse Conversion"
                else:
                    action_call, action_put, action_stock = "BUY", "SELL", "SELL"
                    direction = "Conversion"

                notional = spot * lot_size
                margin_needed = notional * MARGIN_PCP
                gross_profit = abs_deviation * lot_size

                fee_legs = [
                    {"action": action_call, "premium": sim_call, "quantity": lot_size},
                    {"action": action_put, "premium": sim_put, "quantity": lot_size},
                    {"action": action_stock, "premium": spot, "quantity": lot_size},
                ]
                fees = calculate_trade_fees(fee_legs)
                expected_profit = gross_profit - fees["total"]

                if expected_profit <= 0:
                    continue

                net_annualized = (expected_profit / margin_needed) * (365 / dte)
                if net_annualized < MIN_ANNUALIZED_RETURN:
                    continue

                expiry_date = (now + timedelta(days=dte)).strftime("%Y-%m-%d")

                opps.append(_build_opportunity(
                    arb_type="PUT_CALL_PARITY_ARB",
                    symbol=symbol,
                    legs=[
                        {
                            "instrument": f"{symbol}-{strike}CE",
                            "action": action_call,
                            "quantity": lot_size,
                            "price": round(sim_call, 2),
                            "type": "CE",
                            "strike": strike,
                            "expiry_days": dte,
                            "expiry_date": expiry_date,
                        },
                        {
                            "instrument": f"{symbol}-{strike}PE",
                            "action": action_put,
                            "quantity": lot_size,
                            "price": round(sim_put, 2),
                            "type": "PE",
                            "strike": strike,
                            "expiry_days": dte,
                            "expiry_date": expiry_date,
                        },
                        {
                            "instrument": f"{symbol}-SPOT",
                            "action": action_stock,
                            "quantity": lot_size,
                            "price": round(spot, 2),
                            "type": "EQ",
                        },
                    ],
                    annualized_return=round(net_annualized, 4),
                    margin_needed=round(margin_needed, 2),
                    holding_days=dte,
                    entry_cost=round(notional, 2),
                    expected_profit=round(expected_profit, 2),
                    fee_estimate=round(fees["total"], 2),
                    risk_free=True,
                    direction=direction,
                    deviation_pts=round(abs_deviation, 2),
                ))

    return opps


def _simulate_calendar_spread_arbs() -> list[dict]:
    """Generate realistic calendar spread opportunities from SIMULATION_INDICES."""
    opps: list[dict] = []
    now = datetime.utcnow()

    for symbol, info in SIMULATION_INDICES.items():
        spot = info["spot"]
        lot_size = info["lotSize"]
        iv = info["iv"]

        strike_step = 50 if symbol == "NIFTY" else 100
        atm_strike = round(spot / strike_step) * strike_step

        for offset in [-1, 0, 1]:
            strike = atm_strike + offset * strike_step

            for opt_type in ["CE", "PE"]:
                near_dte = 7
                far_dte = 28

                T_near = near_dte / 365
                T_far = far_dte / 365

                near_price = option_price(spot, strike, T_near, RISK_FREE_RATE, iv, opt_type)
                far_price = option_price(spot, strike, T_far, RISK_FREE_RATE, iv, opt_type)

                # Inject term-structure mispricing: slightly inflate far or deflate near
                iv_near = iv + random.uniform(-0.02, 0.01)
                iv_far = iv + random.uniform(-0.01, 0.03)
                near_price = option_price(spot, strike, T_near, RISK_FREE_RATE, iv_near, opt_type)
                far_price = option_price(spot, strike, T_far, RISK_FREE_RATE, iv_far, opt_type)

                time_value_diff = far_price - near_price
                if time_value_diff <= 0:
                    continue

                holding_days = far_dte - near_dte
                notional = spot * lot_size
                margin_needed = notional * MARGIN_CALENDAR
                carry_cost = notional * RISK_FREE_RATE * (holding_days / 365) * MARGIN_CALENDAR
                gross_profit = time_value_diff * lot_size

                fee_legs = [
                    {"action": "BUY", "premium": near_price, "quantity": lot_size},
                    {"action": "SELL", "premium": far_price, "quantity": lot_size},
                ]
                fees = calculate_trade_fees(fee_legs)
                expected_profit = gross_profit - carry_cost - fees["total"]

                if expected_profit <= 0:
                    continue

                net_annualized = (expected_profit / margin_needed) * (365 / holding_days)
                if net_annualized < MIN_ANNUALIZED_RETURN:
                    continue

                near_expiry = (now + timedelta(days=near_dte)).strftime("%Y-%m-%d")
                far_expiry = (now + timedelta(days=far_dte)).strftime("%Y-%m-%d")

                opps.append(_build_opportunity(
                    arb_type="CALENDAR_SPREAD_ARB",
                    symbol=symbol,
                    legs=[
                        {
                            "instrument": f"{symbol}-{strike}{opt_type}-{near_expiry}",
                            "action": "SELL",
                            "quantity": lot_size,
                            "price": round(near_price, 2),
                            "type": opt_type,
                            "strike": strike,
                            "expiry_days": near_dte,
                            "expiry_date": near_expiry,
                        },
                        {
                            "instrument": f"{symbol}-{strike}{opt_type}-{far_expiry}",
                            "action": "BUY",
                            "quantity": lot_size,
                            "price": round(far_price, 2),
                            "type": opt_type,
                            "strike": strike,
                            "expiry_days": far_dte,
                            "expiry_date": far_expiry,
                        },
                    ],
                    annualized_return=round(net_annualized, 4),
                    margin_needed=round(margin_needed, 2),
                    holding_days=holding_days,
                    entry_cost=round(abs(far_price - near_price) * lot_size, 2),
                    expected_profit=round(expected_profit, 2),
                    fee_estimate=round(fees["total"], 2),
                    risk_free=False,
                    iv_near=round(iv_near, 4),
                    iv_far=round(iv_far, 4),
                ))

    return opps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_opportunity(
    arb_type: str,
    symbol: str,
    legs: list[dict],
    annualized_return: float,
    margin_needed: float,
    holding_days: int,
    entry_cost: float,
    expected_profit: float,
    fee_estimate: float,
    risk_free: bool,
    **extra,
) -> dict[str, Any]:
    """Construct a standardised opportunity dict."""
    opp = {
        "id": generate_id(),
        "type": arb_type,
        "type_label": ARB_TYPES.get(arb_type, arb_type),
        "symbol": symbol,
        "legs": legs,
        "annualized_return": annualized_return,
        "margin_needed": margin_needed,
        "risk_free": risk_free,
        "holding_days": holding_days,
        "entry_cost": entry_cost,
        "expected_profit": expected_profit,
        "fee_estimate": fee_estimate,
        "scanned_at": datetime.utcnow().isoformat(),
    }
    opp.update(extra)
    return opp


def filter_opportunities(
    opportunities: list[dict],
    *,
    min_return: float | None = None,
    max_holding_days: int | None = None,
    arb_type: str | None = None,
    risk_free_only: bool = False,
    symbol: str | None = None,
) -> list[dict]:
    """
    Filter a list of scanned opportunities by user criteria.

    Args:
        min_return:        Minimum annualized return (e.g. 0.08 for 8%).
        max_holding_days:  Maximum acceptable holding period.
        arb_type:          One of CASH_FUTURES_ARB, PUT_CALL_PARITY_ARB, CALENDAR_SPREAD_ARB.
        risk_free_only:    If True, only return risk-free opportunities.
        symbol:            Filter by underlying symbol.

    Returns:
        Filtered list, still sorted by annualized_return descending.
    """
    filtered = opportunities

    if min_return is not None:
        filtered = [o for o in filtered if o["annualized_return"] >= min_return]

    if max_holding_days is not None:
        filtered = [o for o in filtered if o["holding_days"] <= max_holding_days]

    if arb_type is not None:
        filtered = [o for o in filtered if o["type"] == arb_type]

    if risk_free_only:
        filtered = [o for o in filtered if o.get("risk_free")]

    if symbol is not None:
        filtered = [o for o in filtered if o["symbol"] == symbol.upper()]

    return filtered


def summarize_opportunities(opportunities: list[dict]) -> dict[str, Any]:
    """
    Return an aggregate summary of scanned opportunities.

    Useful for dashboard display or logging.
    """
    if not opportunities:
        return {
            "total": 0,
            "by_type": {},
            "best_return": None,
            "total_margin_needed": 0,
            "total_expected_profit": 0,
        }

    by_type: dict[str, int] = {}
    for o in opportunities:
        by_type[o["type"]] = by_type.get(o["type"], 0) + 1

    return {
        "total": len(opportunities),
        "by_type": by_type,
        "best_return": max(o["annualized_return"] for o in opportunities),
        "avg_return": round(
            sum(o["annualized_return"] for o in opportunities) / len(opportunities), 4
        ),
        "risk_free_count": sum(1 for o in opportunities if o.get("risk_free")),
        "total_margin_needed": round(
            sum(o["margin_needed"] for o in opportunities), 2
        ),
        "total_expected_profit": round(
            sum(o["expected_profit"] for o in opportunities), 2
        ),
    }
