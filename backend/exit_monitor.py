"""
Exit monitor — evaluates open options positions against exit rules.
Returns alerts: EXIT_NOW, REVIEW, or HOLD for each position.
"""

import logging
from datetime import datetime, date
from charges_engine import charges_engine
import vix_service

logger = logging.getLogger(__name__)


def check_positions(kite_service) -> list:
    """
    Evaluate all open options positions against exit rules.

    Returns list of position alerts, each with:
    - symbol, strategy_type, expiry
    - entry_premium, current_premium
    - current_pnl_gross, current_pnl_net (after exit charges)
    - alert_level: EXIT_NOW / REVIEW / HOLD
    - alert_rule, alert_message, suggested_action
    - exit_charges_estimate
    """
    if not kite_service or not kite_service.is_authenticated():
        return []

    try:
        positions = kite_service.get_positions()
    except Exception as e:
        logger.debug("Failed to fetch positions: %s", e)
        return []

    net_positions = positions.get("net", []) if isinstance(positions, dict) else []
    if not net_positions:
        return []

    # Get VIX for spike detection
    current_vix = vix_service.get_india_vix(kite_service)

    alerts = []
    today = date.today()

    for pos in net_positions:
        qty = pos.get("quantity", 0)
        if qty == 0:
            continue

        inst_type = pos.get("instrument_type", "")
        if inst_type not in ("CE", "PE"):
            continue  # Only monitor options

        symbol = pos.get("tradingsymbol", "")
        exchange = pos.get("exchange", "NFO")
        product = pos.get("product", "")
        buy_price = pos.get("average_price", 0)
        current_price = pos.get("last_price", 0)
        pnl = pos.get("pnl", 0) or pos.get("unrealised", 0)
        expiry = pos.get("expiry", "")

        # Determine if we're short (seller) or long (buyer)
        is_short = qty < 0
        abs_qty = abs(qty)

        # Calculate DTE
        dte = 999
        if expiry:
            try:
                if isinstance(expiry, str):
                    exp_date = datetime.strptime(expiry, "%Y-%m-%d").date()
                else:
                    exp_date = expiry
                dte = max(0, (exp_date - today).days)
            except Exception:
                pass

        # Estimate exit charges
        exit_legs = [{"action": "BUY" if is_short else "SELL", "premium": current_price, "strike": 0, "option_type": inst_type}]
        exit_charges = charges_engine.calculate(exit_legs, abs_qty, 1)
        exit_cost = exit_charges["total_charges"]

        # Net P&L after exit charges
        pnl_net = round(pnl - exit_cost, 2)

        # Build base alert
        alert = {
            "symbol": symbol,
            "instrument_type": inst_type,
            "quantity": qty,
            "is_short": is_short,
            "expiry": str(expiry),
            "dte": dte,
            "entry_premium": round(buy_price, 2),
            "current_premium": round(current_price, 2),
            "current_pnl_gross": round(pnl, 2),
            "current_pnl_net": pnl_net,
            "exit_charges_estimate": round(exit_cost, 2),
            "alert_level": "HOLD",
            "alert_rule": None,
            "alert_message": "Position on track",
            "suggested_action": "Hold",
        }

        # ── Exit Rules (for short/sold positions) ──
        if is_short and buy_price > 0:
            # RULE 1: 50% profit target
            # For sellers: profit when current price drops below entry
            profit_pct = (buy_price - current_price) / buy_price if buy_price else 0
            if profit_pct >= 0.5:
                alert["alert_level"] = "EXIT_NOW"
                alert["alert_rule"] = "50_PCT_PROFIT"
                alert["alert_message"] = f"50% profit reached ({profit_pct*100:.0f}% decay). Book profit."
                alert["suggested_action"] = f"Buy back at ₹{current_price:.2f} to close"

            # RULE 2: Gamma risk (DTE <= 1)
            elif dte <= 1:
                alert["alert_level"] = "EXIT_NOW"
                alert["alert_rule"] = "GAMMA_RISK"
                alert["alert_message"] = f"Expiry in {dte} day(s). High gamma risk."
                alert["suggested_action"] = "Exit to avoid expiry assignment risk"

            # RULE 3: Stop loss (premium doubled = loss)
            elif current_price >= buy_price * 2.0:
                alert["alert_level"] = "EXIT_NOW"
                alert["alert_rule"] = "STOP_LOSS"
                alert["alert_message"] = f"Premium doubled ({current_price:.2f} vs entry {buy_price:.2f}). Stop loss triggered."
                alert["suggested_action"] = f"Buy back at ₹{current_price:.2f} to limit loss"

            # RULE 4: DTE <= 2, not confidently OTM
            elif dte <= 2:
                alert["alert_level"] = "REVIEW"
                alert["alert_rule"] = "NEAR_EXPIRY"
                alert["alert_message"] = f"Only {dte} day(s) to expiry. Review position."
                alert["suggested_action"] = "Consider closing to avoid expiry risk"

            # RULE 5: Profit building but not at target
            elif profit_pct >= 0.3:
                alert["alert_level"] = "REVIEW"
                alert["alert_rule"] = "PARTIAL_PROFIT"
                alert["alert_message"] = f"{profit_pct*100:.0f}% profit. Approaching exit target."
                alert["suggested_action"] = "Consider booking partial or full profit"

            else:
                alert["alert_level"] = "HOLD"
                alert["alert_message"] = f"Position healthy. {profit_pct*100:.0f}% profit, {dte} DTE remaining."
                alert["suggested_action"] = f"Hold. Target exit at 50% profit (₹{buy_price*0.5:.2f})"

        # ── Rules for long/bought positions ──
        elif not is_short and buy_price > 0:
            loss_pct = (buy_price - current_price) / buy_price if buy_price else 0
            if loss_pct >= 0.5:
                alert["alert_level"] = "REVIEW"
                alert["alert_rule"] = "LONG_DECAY"
                alert["alert_message"] = f"Option lost {loss_pct*100:.0f}% of value. Time decay eroding."
                alert["suggested_action"] = "Review thesis or exit to recover remaining value"
            elif dte <= 2:
                alert["alert_level"] = "EXIT_NOW"
                alert["alert_rule"] = "LONG_NEAR_EXPIRY"
                alert["alert_message"] = f"Long option expiring in {dte} day(s). Rapid decay."
                alert["suggested_action"] = "Exit to salvage remaining premium"

        alerts.append(alert)

    # Sort: EXIT_NOW first, then REVIEW, then HOLD
    level_order = {"EXIT_NOW": 0, "REVIEW": 1, "HOLD": 2}
    alerts.sort(key=lambda a: level_order.get(a["alert_level"], 3))

    return alerts
