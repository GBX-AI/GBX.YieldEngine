"""
Portfolio risk engine — capital utilization and delta awareness.
Enriches scanner recommendations with margin context and delta impact.
"""

import logging

logger = logging.getLogger(__name__)


def get_available_margin(kite_service) -> dict:
    """Get available margin from Kite. Returns {available, used, total}."""
    if not kite_service or not kite_service.is_authenticated():
        return {"available": 0, "used": 0, "total": 0}

    try:
        margins = kite_service.get_margins()
        eq = margins.get("equity", {})
        available = eq.get("available", {})
        utilised = eq.get("utilised", {})

        total_available = available.get("live_balance", 0) + available.get("collateral", 0)
        total_used = utilised.get("exposure", 0) + utilised.get("span", 0)

        return {
            "available": round(total_available - total_used, 2),
            "used": round(total_used, 2),
            "total": round(total_available, 2),
        }
    except Exception as e:
        logger.debug("Failed to get margins: %s", e)
        return {"available": 0, "used": 0, "total": 0}


def get_portfolio_delta(kite_service) -> float:
    """
    Calculate net portfolio delta from open positions.
    Positive = net long, Negative = net short.
    """
    if not kite_service or not kite_service.is_authenticated():
        return 0.0

    try:
        positions = kite_service.get_positions()
        net_positions = positions.get("net", []) if isinstance(positions, dict) else []

        total_delta = 0.0
        for pos in net_positions:
            qty = pos.get("quantity", 0)
            if qty == 0:
                continue

            # For futures/stocks, delta ≈ 1.0 per share
            inst_type = pos.get("instrument_type", "")
            if inst_type in ("FUT", "EQ", ""):
                total_delta += qty  # 1 delta per share
            elif inst_type == "CE":
                # Approximate: ATM call delta ≈ 0.5, scale by moneyness
                total_delta += qty * 0.5
            elif inst_type == "PE":
                total_delta += qty * (-0.5)

        return round(total_delta, 2)
    except Exception as e:
        logger.debug("Failed to get positions for delta: %s", e)
        return 0.0


def enrich_with_capital_utilization(recs, available_margin):
    """Add margin percentage and warning flags to recommendations."""
    cumulative = 0
    for rec in recs:
        margin = rec.get("margin_needed", 0)
        cumulative += margin

        if available_margin > 0:
            pct = round((margin / available_margin) * 100, 1)
        else:
            pct = 0

        rec["margin_pct_of_available"] = pct
        rec["capital_warning"] = pct > 30
        rec["cumulative_margin_if_all_taken"] = round(cumulative, 2)

    return recs


def enrich_with_delta_impact(recs, portfolio_delta):
    """Tag each recommendation with its delta impact on the portfolio."""
    for rec in recs:
        trade_delta = rec.get("delta", 0) * rec.get("lots", 1)
        new_delta = portfolio_delta + trade_delta

        if abs(new_delta) < abs(portfolio_delta):
            impact = "REDUCES_DELTA"
        elif abs(trade_delta) < 0.01:
            impact = "NEUTRAL"
        else:
            impact = "ADDS_DELTA"

        rec["trade_delta"] = round(trade_delta, 4)
        rec["delta_impact"] = impact
        rec["portfolio_delta_after"] = round(new_delta, 4)

    return recs


def get_portfolio_risk_summary(recs, available_margin, portfolio_delta):
    """Top-level portfolio risk summary for the scan response."""
    cumulative_margin = sum(r.get("margin_needed", 0) for r in recs)
    cumulative_pct = round((cumulative_margin / available_margin) * 100, 1) if available_margin > 0 else 0

    if portfolio_delta > 5:
        bias = "LONG_HEAVY"
    elif portfolio_delta < -5:
        bias = "SHORT_HEAVY"
    else:
        bias = "BALANCED"

    return {
        "available_margin": round(available_margin, 2),
        "used_margin": round(sum(r.get("margin_needed", 0) for r in recs), 2),
        "portfolio_delta": portfolio_delta,
        "delta_bias": bias,
        "cumulative_margin_if_all_taken": round(cumulative_margin, 2),
        "cumulative_pct_of_available": cumulative_pct,
        "over_deployment_warning": cumulative_pct > 60,
    }
