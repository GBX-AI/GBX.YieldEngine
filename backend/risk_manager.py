"""
Position Monitoring & Adjustments — Yield Engine v3, Section 4H.

The most critical backend module. Monitors all open positions on a 5-minute
cycle during market hours and generates risk alerts with actionable adjustment
options.

Checks performed per position:
  - 2x Stop-Loss (premium doubling)
  - Delta Breach (directional risk)
  - Underlying Drop (intraday crash)
  - Expiry Day ITM (assignment risk)
  - Daily Loss Limit (circuit breaker)
  - Margin Squeeze (margin utilization)
  - Exercise STT Warning (tax trap on expiry)

Adjustment options:
  - EXIT NOW: immediate close
  - ROLL DOWN+OUT: roll to next expiry + lower strike
  - CONVERT TO SPREAD: buy protective leg
  - DO NOTHING: probabilistic scenario analysis
"""

import json
import logging
import math
from datetime import datetime, date, timedelta

from models import (
    get_db,
    generate_id,
    now_iso,
    get_setting,
    SIMULATION_STOCKS,
    SIMULATION_INDICES,
    SAFETY_HARD_CAPS,
)
from black_scholes import compute_greeks, RISK_FREE_RATE
from fee_calculator import calculate_fees, estimate_slippage

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

ALERT_SEVERITY_URGENT = "URGENT"
ALERT_SEVERITY_WARNING = "WARNING"
ALERT_SEVERITY_INFO = "INFO"

ADJUSTMENT_EXIT = "EXIT_NOW"
ADJUSTMENT_ROLL = "ROLL_DOWN_OUT"
ADJUSTMENT_SPREAD = "CONVERT_TO_SPREAD"
ADJUSTMENT_NOTHING = "DO_NOTHING"

# Market hours (IST)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30

# Exercise STT warning window — after 2 PM on expiry day
EXERCISE_STT_WARNING_HOUR = 14

# Margin squeeze threshold
MARGIN_SQUEEZE_THRESHOLD = 0.80

# Index strike step sizes (mirrored from strategy_engine)
INDEX_STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100}
STOCK_STRIKE_STEP = 50

# Probability buckets for DO NOTHING scenario analysis
SCENARIO_BEST_PROB = 0.60
SCENARIO_BASE_PROB = 0.30
SCENARIO_WORST_PROB = 0.10


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _is_market_hours(now=None):
    """Check if current time is within Indian market hours (9:15–15:30 IST)."""
    if now is None:
        now = datetime.utcnow() + timedelta(hours=5, minutes=30)  # UTC → IST
    market_open = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0)
    market_close = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0)
    return market_open <= now <= market_close


def _is_weekday(d=None):
    """Check if date is a trading day (Mon–Fri)."""
    if d is None:
        d = datetime.utcnow() + timedelta(hours=5, minutes=30)
    return d.weekday() < 5


def _parse_legs(legs_str):
    """Parse JSON legs string from positions/trades table."""
    if isinstance(legs_str, str):
        return json.loads(legs_str)
    return legs_str if legs_str else []


def _get_spot_price(symbol):
    """
    Get current spot/underlying price for a symbol.
    Falls back to simulation data when Kite is not connected.
    """
    if symbol in SIMULATION_INDICES:
        return SIMULATION_INDICES[symbol]["spot"]
    if symbol in SIMULATION_STOCKS:
        return SIMULATION_STOCKS[symbol]["ltp"]
    # Attempt to find the base symbol (strip option suffixes)
    base = symbol.split()[0] if " " in symbol else symbol
    if base in SIMULATION_STOCKS:
        return SIMULATION_STOCKS[base]["ltp"]
    if base in SIMULATION_INDICES:
        return SIMULATION_INDICES[base]["spot"]
    return None


def _get_iv(symbol):
    """Get implied volatility for a symbol from simulation data."""
    if symbol in SIMULATION_INDICES:
        return SIMULATION_INDICES[symbol]["iv"]
    if symbol in SIMULATION_STOCKS:
        return SIMULATION_STOCKS[symbol]["iv"]
    base = symbol.split()[0] if " " in symbol else symbol
    if base in SIMULATION_STOCKS:
        return SIMULATION_STOCKS[base]["iv"]
    if base in SIMULATION_INDICES:
        return SIMULATION_INDICES[base]["iv"]
    return 0.25  # conservative default


def _get_lot_size(symbol):
    """Get lot size for a symbol."""
    if symbol in SIMULATION_INDICES:
        return SIMULATION_INDICES[symbol]["lotSize"]
    if symbol in SIMULATION_STOCKS:
        return SIMULATION_STOCKS[symbol]["lotSize"]
    base = symbol.split()[0] if " " in symbol else symbol
    if base in SIMULATION_STOCKS:
        return SIMULATION_STOCKS[base]["lotSize"]
    if base in SIMULATION_INDICES:
        return SIMULATION_INDICES[base]["lotSize"]
    return 1


def _is_index(symbol):
    """Check if symbol is an index."""
    base = symbol.split()[0] if " " in symbol else symbol
    return base in SIMULATION_INDICES


def _dte_years(expiry_date_str):
    """Convert expiry date string to time-to-expiry in years."""
    if not expiry_date_str:
        return 0.0
    try:
        expiry = datetime.fromisoformat(expiry_date_str).date()
    except (ValueError, TypeError):
        try:
            expiry = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return 0.0
    today = (datetime.utcnow() + timedelta(hours=5, minutes=30)).date()
    days = (expiry - today).days
    return max(days, 0) / 365.0


def _is_expiry_today(expiry_date_str):
    """Check if expiry is today."""
    if not expiry_date_str:
        return False
    try:
        expiry = datetime.fromisoformat(expiry_date_str).date()
    except (ValueError, TypeError):
        try:
            expiry = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return False
    today = (datetime.utcnow() + timedelta(hours=5, minutes=30)).date()
    return expiry == today


def _create_alert(alert_type, severity, title, message, position_id=None, data=None):
    """Insert a risk alert into the notifications table."""
    conn = get_db()
    alert_id = generate_id()
    try:
        conn.execute(
            """INSERT INTO notifications (id, type, title, message, severity, action_url, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                alert_id,
                alert_type,
                title,
                message,
                severity,
                f"/positions/{position_id}" if position_id else None,
                now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    alert = {
        "id": alert_id,
        "type": alert_type,
        "severity": severity,
        "title": title,
        "message": message,
        "position_id": position_id,
        "data": data or {},
        "created_at": now_iso(),
    }
    logger.info("RISK ALERT [%s] %s: %s — %s", severity, alert_type, title, message)
    return alert


def _get_active_positions():
    """Fetch all ACTIVE positions from the database."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM positions WHERE status = 'ACTIVE'"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _get_position(position_id):
    """Fetch a single position by ID."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM positions WHERE id = ?", (position_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get_total_margin_used():
    """Sum margin_blocked across all active positions."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(margin_blocked), 0) AS total FROM positions WHERE status = 'ACTIVE'"
        ).fetchone()
        return row["total"] if row else 0
    finally:
        conn.close()


def _get_available_margin():
    """
    Estimate available margin. In simulation mode, use a default capital base.
    In live mode, this would come from Kite margins API.
    """
    total_used = _get_total_margin_used()
    # Default capital assumption for simulation
    total_capital = float(get_setting("total_capital") or "1000000")
    return total_capital - total_used, total_capital


def _compute_leg_greeks(leg, spot, T):
    """Compute Greeks for a single option leg."""
    strike = leg.get("strike", 0)
    option_type = leg.get("option_type", "PE")
    iv = leg.get("iv") or _get_iv(leg.get("symbol", ""))
    if not strike or not spot or T <= 0:
        return {"delta": 0, "gamma": 0, "theta": 0, "vega": 0, "price": 0, "prob_otm": 0.5}
    greeks = compute_greeks(spot, strike, T, RISK_FREE_RATE, iv, option_type)
    return greeks


# ─── 1. Position Monitoring ──────────────────────────────────────────────────

def monitor_positions():
    """
    Main monitoring loop — intended to run every 5 minutes during market hours.
    Checks every ACTIVE position against all risk thresholds and generates
    alerts with appropriate severity levels.

    Returns:
        dict with 'alerts' list and 'circuit_breaker_triggered' bool
    """
    if not _is_weekday() or not _is_market_hours():
        return {"alerts": [], "circuit_breaker_triggered": False, "skipped": True,
                "reason": "Outside market hours"}

    # Load settings
    stop_loss_multiplier = float(get_setting("stop_loss_multiplier") or "2.0")
    delta_alert_threshold = float(get_setting("delta_alert_threshold") or "0.50")
    daily_loss_limit = float(get_setting("daily_loss_limit") or "25000")
    circuit_breaker_enabled = (get_setting("circuit_breaker_enabled") or "false").lower() == "true"
    auto_stop_loss_enabled = (get_setting("auto_stop_loss_enabled") or "false").lower() == "true"
    intraday_drop_alert_pct = float(get_setting("intraday_drop_alert_pct") or "1.5")

    positions = _get_active_positions()
    if not positions:
        return {"alerts": [], "circuit_breaker_triggered": False, "positions_checked": 0}

    alerts = []
    total_unrealized_loss = 0.0
    circuit_breaker_triggered = False

    now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)

    for pos in positions:
        pos_id = pos["id"]
        symbol = pos["symbol"]
        entry_premium = pos.get("entry_premium", 0)
        current_premium = pos.get("current_premium", entry_premium)
        unrealized_pnl = pos.get("unrealized_pnl", 0)
        expiry_date = pos.get("expiry_date")
        legs = _parse_legs(pos.get("legs", "[]"))
        strategy_type = pos.get("strategy_type", "")

        spot = _get_spot_price(symbol)
        T = _dte_years(expiry_date)

        # Track losses for daily limit check
        if unrealized_pnl and unrealized_pnl < 0:
            total_unrealized_loss += abs(unrealized_pnl)

        # ── Check 1: 2x Stop-Loss ────────────────────────────────────
        if current_premium and entry_premium and entry_premium > 0:
            premium_ratio = current_premium / entry_premium
            if premium_ratio >= stop_loss_multiplier:
                loss_amount = (current_premium - entry_premium) * _get_lot_size(symbol)
                alert = _create_alert(
                    alert_type="STOP_LOSS",
                    severity=ALERT_SEVERITY_URGENT,
                    title=f"Stop-Loss Hit: {symbol}",
                    message=(
                        f"Premium has risen {premium_ratio:.1f}x from ₹{entry_premium:.2f} "
                        f"to ₹{current_premium:.2f}. Unrealized loss: ₹{loss_amount:,.0f}. "
                        f"{'Auto-closing position.' if auto_stop_loss_enabled else 'Manual action required.'}"
                    ),
                    position_id=pos_id,
                    data={
                        "entry_premium": entry_premium,
                        "current_premium": current_premium,
                        "multiplier": premium_ratio,
                        "loss_amount": loss_amount,
                        "auto_close": auto_stop_loss_enabled,
                    },
                )
                alerts.append(alert)

                if auto_stop_loss_enabled:
                    try:
                        execute_adjustment(pos_id, ADJUSTMENT_EXIT, {
                            "reason": "auto_stop_loss",
                            "trigger_multiplier": premium_ratio,
                        })
                        logger.info("Auto stop-loss executed for position %s", pos_id)
                    except Exception as e:
                        logger.error("Auto stop-loss FAILED for %s: %s", pos_id, e)

        # ── Check 2: Delta Breach ─────────────────────────────────────
        if spot and T > 0 and legs:
            position_delta = 0.0
            for leg in legs:
                greeks = _compute_leg_greeks(leg, spot, T)
                leg_delta = greeks["delta"]
                qty = leg.get("quantity", _get_lot_size(symbol))
                action = leg.get("action", "SELL")
                # Short positions have inverted delta
                if action == "SELL":
                    leg_delta = -leg_delta
                position_delta += leg_delta * qty

            # Normalize to per-lot delta
            lot_size = _get_lot_size(symbol)
            normalized_delta = position_delta / lot_size if lot_size else position_delta

            if abs(normalized_delta) > delta_alert_threshold:
                alert = _create_alert(
                    alert_type="DELTA_BREACH",
                    severity=ALERT_SEVERITY_WARNING,
                    title=f"Delta Breach: {symbol}",
                    message=(
                        f"Position delta {normalized_delta:+.3f} exceeds threshold "
                        f"±{delta_alert_threshold:.2f}. Consider adjusting or hedging."
                    ),
                    position_id=pos_id,
                    data={
                        "position_delta": normalized_delta,
                        "threshold": delta_alert_threshold,
                        "adjustment_options": [ADJUSTMENT_ROLL, ADJUSTMENT_SPREAD, ADJUSTMENT_EXIT],
                    },
                )
                alerts.append(alert)

        # ── Check 3: Underlying Drop ─────────────────────────────────
        if spot:
            # In simulation mode, estimate intraday change from stored reference
            # In live mode, this would compare to day's open price
            reference_price = spot  # placeholder — live mode gets open price from Kite
            # Check if any leg's strike is being approached
            for leg in legs:
                strike = leg.get("strike", 0)
                option_type = leg.get("option_type", "PE")
                if option_type in ("PE", "PUT", "P") and strike and spot > 0:
                    drop_pct = ((strike - spot) / spot) * 100 if spot < strike else 0
                    # Also check if spot dropped significantly from a recent high
                    otm_pct = ((spot - strike) / spot) * 100 if spot > strike else 0
                    if otm_pct < intraday_drop_alert_pct and otm_pct >= 0:
                        alert = _create_alert(
                            alert_type="UNDERLYING_DROP",
                            severity=ALERT_SEVERITY_WARNING,
                            title=f"Underlying Near Strike: {symbol}",
                            message=(
                                f"Spot ₹{spot:,.0f} is only {otm_pct:.1f}% away from "
                                f"strike ₹{strike:,.0f}. Position at risk of going ITM."
                            ),
                            position_id=pos_id,
                            data={
                                "spot": spot,
                                "strike": strike,
                                "otm_pct": otm_pct,
                                "option_type": option_type,
                            },
                        )
                        alerts.append(alert)

        # ── Check 4: Expiry Day ITM ──────────────────────────────────
        if _is_expiry_today(expiry_date) and spot:
            for leg in legs:
                strike = leg.get("strike", 0)
                option_type = leg.get("option_type", "PE")
                action = leg.get("action", "SELL")
                if not strike:
                    continue

                is_itm = False
                intrinsic = 0
                if option_type in ("CE", "CALL", "C"):
                    is_itm = spot > strike
                    intrinsic = max(0, spot - strike)
                elif option_type in ("PE", "PUT", "P"):
                    is_itm = spot < strike
                    intrinsic = max(0, strike - spot)

                if is_itm and action == "SELL":
                    qty = leg.get("quantity", _get_lot_size(symbol))
                    assignment_loss = intrinsic * qty
                    alert = _create_alert(
                        alert_type="EXPIRY_ITM",
                        severity=ALERT_SEVERITY_URGENT,
                        title=f"Expiry ITM: {symbol} {strike}{option_type}",
                        message=(
                            f"Position is ITM on expiry day. Intrinsic value: ₹{intrinsic:.2f}. "
                            f"Potential assignment loss: ₹{assignment_loss:,.0f}. "
                            f"Close before 3:30 PM to avoid exercise."
                        ),
                        position_id=pos_id,
                        data={
                            "strike": strike,
                            "option_type": option_type,
                            "spot": spot,
                            "intrinsic": intrinsic,
                            "assignment_loss": assignment_loss,
                        },
                    )
                    alerts.append(alert)

                    # ── Check 7: Exercise STT Warning ─────────────────
                    if now_ist.hour >= EXERCISE_STT_WARNING_HOUR:
                        exercise_stt = intrinsic * qty * 0.00125
                        # Cost to manually close (buy back)
                        close_fees = calculate_fees("BUY", current_premium or intrinsic, qty)
                        manual_close_cost = close_fees["total"]
                        stt_savings = exercise_stt - manual_close_cost

                        alert = _create_alert(
                            alert_type="EXERCISE_STT",
                            severity=ALERT_SEVERITY_URGENT,
                            title=f"Exercise STT Warning: {symbol}",
                            message=(
                                f"After 2 PM on expiry. Exercise STT: ₹{exercise_stt:,.0f} vs "
                                f"manual close cost: ₹{manual_close_cost:,.0f}. "
                                f"{'Close manually to save ₹' + f'{stt_savings:,.0f}' if stt_savings > 0 else 'Manual close not cheaper.'}"
                            ),
                            position_id=pos_id,
                            data={
                                "exercise_stt": exercise_stt,
                                "manual_close_cost": manual_close_cost,
                                "stt_savings": stt_savings,
                                "recommendation": "CLOSE_MANUALLY" if stt_savings > 0 else "LET_EXPIRE",
                            },
                        )
                        alerts.append(alert)

        # ── Check 6: Margin Squeeze ──────────────────────────────────
        available_margin, total_capital = _get_available_margin()
        if total_capital > 0:
            margin_util = 1 - (available_margin / total_capital)
            if margin_util > MARGIN_SQUEEZE_THRESHOLD:
                alert = _create_alert(
                    alert_type="MARGIN_SQUEEZE",
                    severity=ALERT_SEVERITY_WARNING,
                    title="Margin Utilization High",
                    message=(
                        f"Margin utilization at {margin_util:.0%} (threshold: "
                        f"{MARGIN_SQUEEZE_THRESHOLD:.0%}). Consider closing positions "
                        f"to free margin."
                    ),
                    position_id=pos_id,
                    data={
                        "margin_util": margin_util,
                        "available_margin": available_margin,
                        "total_capital": total_capital,
                    },
                )
                alerts.append(alert)

    # ── Check 5: Daily Loss Limit (aggregate) ─────────────────────────
    if total_unrealized_loss > daily_loss_limit:
        alert = _create_alert(
            alert_type="DAILY_LOSS_LIMIT",
            severity=ALERT_SEVERITY_URGENT,
            title="Daily Loss Limit Breached",
            message=(
                f"Total unrealized losses ₹{total_unrealized_loss:,.0f} exceed daily limit "
                f"₹{daily_loss_limit:,.0f}. "
                f"{'Circuit breaker activated — no new positions allowed.' if circuit_breaker_enabled else 'Review open positions immediately.'}"
            ),
            data={
                "total_unrealized_loss": total_unrealized_loss,
                "daily_loss_limit": daily_loss_limit,
                "circuit_breaker": circuit_breaker_enabled,
            },
        )
        alerts.append(alert)

        if circuit_breaker_enabled:
            circuit_breaker_triggered = True
            logger.warning("CIRCUIT BREAKER ACTIVATED — losses ₹%s exceed limit ₹%s",
                           f"{total_unrealized_loss:,.0f}", f"{daily_loss_limit:,.0f}")

    return {
        "alerts": alerts,
        "circuit_breaker_triggered": circuit_breaker_triggered,
        "positions_checked": len(positions),
        "total_unrealized_loss": total_unrealized_loss,
        "timestamp": now_iso(),
    }


# ─── 2. Compute Adjustments ──────────────────────────────────────────────────

def compute_adjustments(position_id):
    """
    Compute 4 adjustment options for a position:
      1. EXIT NOW — buy back immediately
      2. ROLL DOWN+OUT — close current + open at next expiry, lower strike
      3. CONVERT TO SPREAD — buy protective option to cap max loss
      4. DO NOTHING — probability-based scenario analysis

    Returns:
        dict with position info and list of adjustment options
    """
    pos = _get_position(position_id)
    if not pos:
        return {"error": "Position not found", "position_id": position_id}

    symbol = pos["symbol"]
    entry_premium = pos.get("entry_premium", 0)
    current_premium = pos.get("current_premium", entry_premium)
    expiry_date = pos.get("expiry_date")
    legs = _parse_legs(pos.get("legs", "[]"))
    margin_blocked = pos.get("margin_blocked", 0)

    spot = _get_spot_price(symbol)
    T = _dte_years(expiry_date)
    iv = _get_iv(symbol)
    lot_size = _get_lot_size(symbol)
    is_idx = _is_index(symbol)

    # Determine the primary sold leg for adjustments
    sold_leg = None
    for leg in legs:
        if leg.get("action") == "SELL":
            sold_leg = leg
            break
    if not sold_leg and legs:
        sold_leg = legs[0]

    strike = sold_leg.get("strike", 0) if sold_leg else 0
    option_type = sold_leg.get("option_type", "PE") if sold_leg else "PE"
    quantity = sold_leg.get("quantity", lot_size) if sold_leg else lot_size

    adjustments = []

    # ── Option 1: EXIT NOW ────────────────────────────────────────────
    slippage = estimate_slippage(symbol, is_index=is_idx)
    buyback_price = current_premium + slippage
    buyback_cost = buyback_price * quantity
    exit_fees = calculate_fees("BUY", buyback_price, quantity)
    realized_loss = (buyback_price - entry_premium) * quantity
    margin_freed = margin_blocked

    adjustments.append({
        "type": ADJUSTMENT_EXIT,
        "label": "Exit Now",
        "description": "Buy back the position immediately to limit further losses.",
        "details": {
            "buyback_price": round(buyback_price, 2),
            "buyback_cost": round(buyback_cost, 2),
            "slippage_estimate": slippage,
            "fees": exit_fees,
            "realized_loss": round(realized_loss, 2),
            "margin_freed": margin_freed,
            "net_loss": round(realized_loss + exit_fees["total"], 2),
        },
    })

    # ── Option 2: ROLL DOWN+OUT ───────────────────────────────────────
    # Roll to next weekly/monthly expiry at a lower strike (for puts) or higher (for calls)
    step = INDEX_STRIKE_STEP.get(symbol.split()[0] if " " in symbol else symbol, STOCK_STRIKE_STEP)

    if option_type in ("PE", "PUT", "P"):
        new_strike = strike - step  # Roll down for puts
    else:
        new_strike = strike + step  # Roll up for calls

    # Next expiry: 7 days out
    next_expiry_T = T + (7 / 365.0)
    if next_expiry_T <= 0:
        next_expiry_T = 7 / 365.0

    # Price the new option
    new_greeks = compute_greeks(spot, new_strike, next_expiry_T, RISK_FREE_RATE, iv, option_type)
    new_premium = new_greeks["price"]

    # Roll cost = buyback current - sell new
    roll_debit = buyback_price - new_premium
    roll_cost = roll_debit * quantity

    # Fees for both legs
    close_fees = calculate_fees("BUY", buyback_price, quantity)
    open_fees = calculate_fees("SELL", new_premium, quantity)
    total_roll_fees = close_fees["total"] + open_fees["total"]

    # New breakeven
    net_credit_collected = entry_premium - roll_debit
    if option_type in ("PE", "PUT", "P"):
        new_breakeven = new_strike - net_credit_collected
    else:
        new_breakeven = new_strike + net_credit_collected

    adjustments.append({
        "type": ADJUSTMENT_ROLL,
        "label": "Roll Down & Out",
        "description": (
            f"Close current {strike}{option_type} and sell {new_strike}{option_type} "
            f"at next expiry. Extends time horizon and moves strike further OTM."
        ),
        "details": {
            "close_strike": strike,
            "new_strike": new_strike,
            "new_expiry_dte": round(next_expiry_T * 365),
            "buyback_price": round(buyback_price, 2),
            "new_premium": round(new_premium, 2),
            "roll_debit": round(roll_debit, 2),
            "roll_cost": round(roll_cost, 2),
            "fees": {"close": close_fees, "open": open_fees, "total": round(total_roll_fees, 2)},
            "new_breakeven": round(new_breakeven, 2),
            "new_delta": round(new_greeks["delta"], 4),
            "new_prob_otm": round(new_greeks["prob_otm"], 4),
        },
    })

    # ── Option 3: CONVERT TO SPREAD ───────────────────────────────────
    # Buy a protective option to cap max loss
    if option_type in ("PE", "PUT", "P"):
        protective_strike = strike - step  # Buy lower put
    else:
        protective_strike = strike + step  # Buy higher call

    protective_greeks = compute_greeks(spot, protective_strike, T if T > 0 else 1 / 365, RISK_FREE_RATE, iv, option_type)
    protective_cost = protective_greeks["price"]
    protective_total = protective_cost * quantity
    protective_fees = calculate_fees("BUY", protective_cost, quantity)

    # Max loss after spread conversion
    spread_width = abs(strike - protective_strike)
    max_loss_spread = (spread_width - entry_premium + protective_cost) * quantity

    adjustments.append({
        "type": ADJUSTMENT_SPREAD,
        "label": "Convert to Spread",
        "description": (
            f"Buy {protective_strike}{option_type} to convert naked position into a "
            f"defined-risk spread. Caps maximum loss at ₹{max_loss_spread:,.0f}."
        ),
        "details": {
            "protective_strike": protective_strike,
            "protective_premium": round(protective_cost, 2),
            "protective_total_cost": round(protective_total, 2),
            "fees": protective_fees,
            "spread_width": spread_width,
            "max_loss": round(max_loss_spread, 2),
            "margin_reduction_estimate": round(margin_blocked * 0.5, 2),  # Spreads use ~50% less margin
        },
    })

    # ── Option 4: DO NOTHING ──────────────────────────────────────────
    if T > 0 and spot and strike:
        # Best case: option expires OTM — keep full premium
        best_pnl = entry_premium * quantity

        # Base case: option at current level at expiry
        base_pnl = (entry_premium - current_premium) * quantity

        # Worst case: option goes deeper ITM
        if option_type in ("PE", "PUT", "P"):
            worst_spot = spot * (1 - 0.05)  # 5% further drop
            worst_intrinsic = max(0, strike - worst_spot)
        else:
            worst_spot = spot * (1 + 0.05)  # 5% further rise
            worst_intrinsic = max(0, worst_spot - strike)
        worst_pnl = (entry_premium - worst_intrinsic) * quantity

        # Probability from Greeks
        greeks = compute_greeks(spot, strike, T, RISK_FREE_RATE, iv, option_type)
        prob_otm = greeks["prob_otm"]
        prob_itm = 1 - prob_otm

        adjustments.append({
            "type": ADJUSTMENT_NOTHING,
            "label": "Do Nothing",
            "description": "Hold the position and let theta decay work. Suitable if conviction remains.",
            "details": {
                "prob_otm": round(prob_otm, 4),
                "prob_itm": round(prob_itm, 4),
                "theta_per_day": round(greeks["theta"] * quantity, 2),
                "days_to_expiry": round(T * 365),
                "scenarios": {
                    "best_case": {
                        "description": "Expires OTM — keep full premium",
                        "probability": round(prob_otm, 4),
                        "pnl": round(best_pnl, 2),
                    },
                    "base_case": {
                        "description": "Stays near current level",
                        "probability": round(min(prob_otm, prob_itm) * 0.8, 4),
                        "pnl": round(base_pnl, 2),
                    },
                    "worst_case": {
                        "description": f"Underlying moves 5% against position",
                        "probability": round(prob_itm * 0.4, 4),
                        "pnl": round(worst_pnl, 2),
                    },
                },
            },
        })
    else:
        adjustments.append({
            "type": ADJUSTMENT_NOTHING,
            "label": "Do Nothing",
            "description": "Hold the position. Insufficient data for scenario analysis.",
            "details": {
                "prob_otm": None,
                "scenarios": None,
            },
        })

    return {
        "position_id": position_id,
        "symbol": symbol,
        "strategy_type": pos.get("strategy_type"),
        "entry_premium": entry_premium,
        "current_premium": current_premium,
        "unrealized_pnl": pos.get("unrealized_pnl"),
        "expiry_date": expiry_date,
        "spot_price": spot,
        "adjustments": adjustments,
        "computed_at": now_iso(),
    }


# ─── 3. Execute Adjustment ───────────────────────────────────────────────────

def execute_adjustment(position_id, adjustment_type, params=None):
    """
    Execute a chosen adjustment on a position.

    Args:
        position_id: ID of the position to adjust
        adjustment_type: one of EXIT_NOW, ROLL_DOWN_OUT, CONVERT_TO_SPREAD, DO_NOTHING
        params: additional parameters (reason, new strike overrides, etc.)

    Returns:
        dict with execution result and updated position state
    """
    params = params or {}
    pos = _get_position(position_id)
    if not pos:
        return {"error": "Position not found", "position_id": position_id}

    symbol = pos["symbol"]
    legs = _parse_legs(pos.get("legs", "[]"))
    entry_premium = pos.get("entry_premium", 0)
    current_premium = pos.get("current_premium", entry_premium)
    lot_size = _get_lot_size(symbol)

    conn = get_db()
    try:
        if adjustment_type == ADJUSTMENT_EXIT:
            # Close the position
            slippage = estimate_slippage(symbol, is_index=_is_index(symbol))
            exit_premium = current_premium + slippage
            quantity = lot_size
            for leg in legs:
                if leg.get("action") == "SELL":
                    quantity = leg.get("quantity", lot_size)
                    break

            realized_pnl = (entry_premium - exit_premium) * quantity
            exit_fees = calculate_fees("BUY", exit_premium, quantity)

            # Update position
            conn.execute(
                """UPDATE positions SET status = 'CLOSED', current_premium = ?,
                   unrealized_pnl = 0, last_updated = ? WHERE id = ?""",
                (exit_premium, now_iso(), position_id),
            )

            # Update trade record
            if pos.get("trade_id"):
                conn.execute(
                    """UPDATE trades SET exit_premium = ?, exit_time = ?, exit_reason = ?,
                       pnl = ?, fees = COALESCE(fees, 0) + ?, status = 'CLOSED' WHERE id = ?""",
                    (exit_premium, now_iso(), params.get("reason", "manual_exit"),
                     realized_pnl, exit_fees["total"], pos["trade_id"]),
                )

            # Record adjustment
            conn.execute(
                """INSERT INTO adjustments (id, trade_id, adjustment_type, old_legs, new_legs, cost, reason, created_at)
                   VALUES (?, ?, ?, ?, NULL, ?, ?, ?)""",
                (generate_id(), pos.get("trade_id"), ADJUSTMENT_EXIT,
                 json.dumps(legs), round(realized_pnl + exit_fees["total"], 2),
                 params.get("reason", "exit_now"), now_iso()),
            )
            conn.commit()

            return {
                "success": True,
                "adjustment_type": ADJUSTMENT_EXIT,
                "position_id": position_id,
                "exit_premium": round(exit_premium, 2),
                "realized_pnl": round(realized_pnl, 2),
                "fees": exit_fees,
                "margin_freed": pos.get("margin_blocked", 0),
                "executed_at": now_iso(),
            }

        elif adjustment_type == ADJUSTMENT_ROLL:
            # Close current + open new position at next expiry
            spot = _get_spot_price(symbol)
            iv = _get_iv(symbol)
            T = _dte_years(pos.get("expiry_date"))
            step = INDEX_STRIKE_STEP.get(
                symbol.split()[0] if " " in symbol else symbol, STOCK_STRIKE_STEP
            )

            sold_leg = None
            for leg in legs:
                if leg.get("action") == "SELL":
                    sold_leg = leg
                    break
            if not sold_leg:
                sold_leg = legs[0] if legs else {}

            old_strike = sold_leg.get("strike", 0)
            option_type = sold_leg.get("option_type", "PE")
            quantity = sold_leg.get("quantity", lot_size)

            new_strike = params.get("new_strike")
            if not new_strike:
                if option_type in ("PE", "PUT", "P"):
                    new_strike = old_strike - step
                else:
                    new_strike = old_strike + step

            # New expiry 7 days out
            new_dte = params.get("new_dte", 7)
            next_expiry_T = new_dte / 365.0

            slippage = estimate_slippage(symbol, is_index=_is_index(symbol))
            buyback_price = current_premium + slippage

            new_greeks = compute_greeks(spot, new_strike, next_expiry_T, RISK_FREE_RATE, iv, option_type)
            new_premium = new_greeks["price"]

            close_fees = calculate_fees("BUY", buyback_price, quantity)
            open_fees = calculate_fees("SELL", new_premium, quantity)
            roll_cost = (buyback_price - new_premium) * quantity + close_fees["total"] + open_fees["total"]

            # Close current position
            conn.execute(
                """UPDATE positions SET status = 'ROLLED', current_premium = ?,
                   unrealized_pnl = 0, last_updated = ? WHERE id = ?""",
                (buyback_price, now_iso(), position_id),
            )

            # Create new position
            new_pos_id = generate_id()
            new_trade_id = generate_id()
            new_expiry_date = (datetime.utcnow() + timedelta(days=new_dte)).strftime("%Y-%m-%d")
            new_legs = [{
                "action": "SELL",
                "strike": new_strike,
                "option_type": option_type,
                "quantity": quantity,
                "premium": new_premium,
            }]

            conn.execute(
                """INSERT INTO positions (id, trade_id, symbol, strategy_type, legs,
                   entry_premium, current_premium, expiry_date, margin_blocked, status, last_updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?)""",
                (new_pos_id, new_trade_id, symbol, pos.get("strategy_type", ""),
                 json.dumps(new_legs), new_premium, new_premium, new_expiry_date,
                 pos.get("margin_blocked", 0), now_iso()),
            )

            # Record adjustment
            conn.execute(
                """INSERT INTO adjustments (id, trade_id, adjustment_type, old_legs, new_legs, cost, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (generate_id(), pos.get("trade_id"), ADJUSTMENT_ROLL,
                 json.dumps(legs), json.dumps(new_legs), round(roll_cost, 2),
                 params.get("reason", "roll_down_out"), now_iso()),
            )
            conn.commit()

            return {
                "success": True,
                "adjustment_type": ADJUSTMENT_ROLL,
                "position_id": position_id,
                "new_position_id": new_pos_id,
                "old_strike": old_strike,
                "new_strike": new_strike,
                "buyback_price": round(buyback_price, 2),
                "new_premium": round(new_premium, 2),
                "roll_cost": round(roll_cost, 2),
                "new_expiry": new_expiry_date,
                "fees": {"close": close_fees, "open": open_fees},
                "executed_at": now_iso(),
            }

        elif adjustment_type == ADJUSTMENT_SPREAD:
            # Buy a protective option to convert to spread
            spot = _get_spot_price(symbol)
            iv = _get_iv(symbol)
            T = _dte_years(pos.get("expiry_date"))
            step = INDEX_STRIKE_STEP.get(
                symbol.split()[0] if " " in symbol else symbol, STOCK_STRIKE_STEP
            )

            sold_leg = None
            for leg in legs:
                if leg.get("action") == "SELL":
                    sold_leg = leg
                    break
            if not sold_leg:
                sold_leg = legs[0] if legs else {}

            strike = sold_leg.get("strike", 0)
            option_type = sold_leg.get("option_type", "PE")
            quantity = sold_leg.get("quantity", lot_size)

            protective_strike = params.get("protective_strike")
            if not protective_strike:
                if option_type in ("PE", "PUT", "P"):
                    protective_strike = strike - step
                else:
                    protective_strike = strike + step

            T_adj = T if T > 0 else 1 / 365.0
            protective_greeks = compute_greeks(spot, protective_strike, T_adj, RISK_FREE_RATE, iv, option_type)
            protective_cost = protective_greeks["price"]
            slippage = estimate_slippage(symbol, is_index=_is_index(symbol))
            buy_price = protective_cost + slippage

            buy_fees = calculate_fees("BUY", buy_price, quantity)
            total_cost = buy_price * quantity + buy_fees["total"]

            # Update position legs to include the new protective leg
            new_legs = list(legs) + [{
                "action": "BUY",
                "strike": protective_strike,
                "option_type": option_type,
                "quantity": quantity,
                "premium": buy_price,
            }]

            spread_width = abs(strike - protective_strike)
            max_loss = (spread_width - entry_premium + buy_price) * quantity

            conn.execute(
                """UPDATE positions SET legs = ?, strategy_type = ?,
                   margin_blocked = margin_blocked * 0.5, last_updated = ? WHERE id = ?""",
                (json.dumps(new_legs),
                 pos.get("strategy_type", "") + "_SPREAD",
                 now_iso(), position_id),
            )

            # Record adjustment
            conn.execute(
                """INSERT INTO adjustments (id, trade_id, adjustment_type, old_legs, new_legs, cost, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (generate_id(), pos.get("trade_id"), ADJUSTMENT_SPREAD,
                 json.dumps(legs), json.dumps(new_legs), round(total_cost, 2),
                 params.get("reason", "convert_to_spread"), now_iso()),
            )
            conn.commit()

            return {
                "success": True,
                "adjustment_type": ADJUSTMENT_SPREAD,
                "position_id": position_id,
                "protective_strike": protective_strike,
                "protective_premium": round(buy_price, 2),
                "total_cost": round(total_cost, 2),
                "fees": buy_fees,
                "max_loss": round(max_loss, 2),
                "margin_reduction": round(pos.get("margin_blocked", 0) * 0.5, 2),
                "executed_at": now_iso(),
            }

        elif adjustment_type == ADJUSTMENT_NOTHING:
            # Just log the decision
            conn.execute(
                """INSERT INTO adjustments (id, trade_id, adjustment_type, old_legs, new_legs, cost, reason, created_at)
                   VALUES (?, ?, ?, ?, NULL, 0, ?, ?)""",
                (generate_id(), pos.get("trade_id"), ADJUSTMENT_NOTHING,
                 json.dumps(legs), params.get("reason", "hold_position"), now_iso()),
            )
            conn.commit()

            return {
                "success": True,
                "adjustment_type": ADJUSTMENT_NOTHING,
                "position_id": position_id,
                "message": "Position held. Decision recorded.",
                "executed_at": now_iso(),
            }

        else:
            return {"error": f"Unknown adjustment type: {adjustment_type}"}

    except Exception as e:
        conn.rollback()
        logger.error("Adjustment execution failed: %s", e, exc_info=True)
        return {"error": str(e), "adjustment_type": adjustment_type, "position_id": position_id}
    finally:
        conn.close()


# ─── 4. Risk Disclosure ──────────────────────────────────────────────────────

def compute_risk_disclosure(recommendation):
    """
    Generate risk disclosure text for the confirmation modal (Section 4N).

    Args:
        recommendation: dict with strategy details (strategy_type, legs, premium,
                        max_loss, margin_required, prob_otm, symbol, etc.)

    Returns:
        dict with disclosure sections for display in the confirmation modal
    """
    strategy_type = recommendation.get("strategy_type", "UNKNOWN")
    symbol = recommendation.get("symbol", "")
    max_loss = recommendation.get("max_loss")
    margin_required = recommendation.get("margin_required", 0)
    premium = recommendation.get("premium", 0)
    prob_otm = recommendation.get("prob_otm", 0)
    legs = recommendation.get("legs", [])
    lot_size = _get_lot_size(symbol)

    # Base risk warnings common to all strategies
    warnings = [
        "Options trading involves significant risk of loss.",
        "Past performance does not guarantee future results.",
        "You may lose more than your initial investment on naked positions.",
    ]

    # Strategy-specific disclosures
    strategy_risks = {
        "COVERED_CALL": [
            "Your shares may be called away if the option expires in-the-money.",
            "You forego upside beyond the strike price for the duration of the contract.",
            "If the underlying drops sharply, the premium collected provides limited downside cushion.",
        ],
        "CASH_SECURED_PUT": [
            "You are obligated to buy the underlying at the strike price if assigned.",
            "If the underlying drops significantly, losses can be substantial.",
            f"Maximum loss: ₹{(legs[0].get('strike', 0) - premium) * lot_size:,.0f}" if legs else "Maximum loss depends on strike price.",
            "Margin will be blocked until expiry or position close.",
        ],
        "PUT_CREDIT_SPREAD": [
            f"Maximum loss is capped at ₹{max_loss:,.0f}." if max_loss else "Maximum loss is the spread width minus net credit.",
            "Both legs must be managed together — do not close one leg independently.",
            "Early assignment risk exists on the short leg, especially near expiry.",
        ],
        "COLLAR": [
            "Upside is capped at the call strike; downside protected below the put strike.",
            "Net cost of the collar reduces your effective position value.",
            "Both options must be managed at expiry to avoid unintended assignment.",
        ],
    }

    specific_risks = strategy_risks.get(strategy_type, [
        "This strategy involves option selling with potentially unlimited risk.",
        "Ensure you understand the maximum loss before proceeding.",
    ])

    # Fee disclosure
    fee_note = "Fees include brokerage, STT, exchange charges, SEBI levy, stamp duty, and GST."

    # Exercise/assignment warning
    assignment_warning = None
    for leg in legs:
        if leg.get("action") == "SELL":
            assignment_warning = (
                "SHORT OPTION: You have an obligation if this option is exercised. "
                "On expiry day, ITM options are auto-exercised and STT on exercise "
                "is 0.125% of intrinsic value (vs 0.0625% on normal sell). "
                "Monitor ITM positions closely on expiry day."
            )
            break

    # Margin disclosure
    margin_note = None
    if margin_required:
        margin_note = (
            f"Margin required: ₹{margin_required:,.0f}. This amount will be blocked "
            f"from your available capital. Margin requirements may increase during "
            f"volatile market conditions."
        )

    # Probability disclaimer
    prob_note = None
    if prob_otm:
        prob_note = (
            f"Estimated probability of expiring OTM (profitable): {prob_otm:.0%}. "
            f"This is a model estimate based on current implied volatility and "
            f"Black-Scholes assumptions. Actual outcomes may differ significantly."
        )

    return {
        "strategy_type": strategy_type,
        "symbol": symbol,
        "general_warnings": warnings,
        "strategy_specific_risks": specific_risks,
        "fee_disclosure": fee_note,
        "assignment_warning": assignment_warning,
        "margin_disclosure": margin_note,
        "probability_disclaimer": prob_note,
        "acknowledgment_required": [
            "I understand the risks involved in this trade.",
            "I have reviewed the maximum potential loss.",
            "I confirm this trade is within my risk tolerance.",
        ],
        "generated_at": now_iso(),
    }


# ─── 5. Risk Status ──────────────────────────────────────────────────────────

def get_risk_status():
    """
    Current portfolio risk summary.

    Returns:
        dict with total delta exposure, margin utilization, daily P&L status,
        and count of positions at risk.
    """
    positions = _get_active_positions()
    available_margin, total_capital = _get_available_margin()
    daily_loss_limit = float(get_setting("daily_loss_limit") or "25000")

    total_delta = 0.0
    total_margin_used = 0.0
    total_unrealized_pnl = 0.0
    total_unrealized_loss = 0.0
    positions_at_risk = 0
    expiring_today = 0

    for pos in positions:
        symbol = pos["symbol"]
        legs = _parse_legs(pos.get("legs", "[]"))
        spot = _get_spot_price(symbol)
        T = _dte_years(pos.get("expiry_date"))
        lot_size = _get_lot_size(symbol)

        # Aggregate delta
        if spot and T > 0:
            for leg in legs:
                greeks = _compute_leg_greeks(leg, spot, T)
                qty = leg.get("quantity", lot_size)
                leg_delta = greeks["delta"]
                if leg.get("action") == "SELL":
                    leg_delta = -leg_delta
                total_delta += leg_delta * qty

        # Aggregate margin
        total_margin_used += pos.get("margin_blocked", 0)

        # Aggregate P&L
        pnl = pos.get("unrealized_pnl", 0) or 0
        total_unrealized_pnl += pnl
        if pnl < 0:
            total_unrealized_loss += abs(pnl)

        # Count positions at risk (losing or near expiry)
        entry_premium = pos.get("entry_premium", 0)
        current_premium = pos.get("current_premium", entry_premium)
        if entry_premium and current_premium:
            if current_premium >= entry_premium * 1.5:
                positions_at_risk += 1

        if _is_expiry_today(pos.get("expiry_date")):
            expiring_today += 1

    margin_util = (total_margin_used / total_capital) if total_capital > 0 else 0
    daily_loss_remaining = daily_loss_limit - total_unrealized_loss
    circuit_breaker_enabled = (get_setting("circuit_breaker_enabled") or "false").lower() == "true"

    return {
        "total_positions": len(positions),
        "total_delta": round(total_delta, 4),
        "total_margin_used": round(total_margin_used, 2),
        "available_margin": round(available_margin, 2),
        "total_capital": total_capital,
        "margin_utilization": round(margin_util, 4),
        "margin_status": (
            "CRITICAL" if margin_util > 0.90 else
            "WARNING" if margin_util > MARGIN_SQUEEZE_THRESHOLD else
            "OK"
        ),
        "total_unrealized_pnl": round(total_unrealized_pnl, 2),
        "total_unrealized_loss": round(total_unrealized_loss, 2),
        "daily_loss_limit": daily_loss_limit,
        "daily_loss_remaining": round(daily_loss_remaining, 2),
        "daily_loss_status": (
            "BREACHED" if total_unrealized_loss > daily_loss_limit else
            "WARNING" if total_unrealized_loss > daily_loss_limit * 0.75 else
            "OK"
        ),
        "circuit_breaker_enabled": circuit_breaker_enabled,
        "circuit_breaker_active": circuit_breaker_enabled and total_unrealized_loss > daily_loss_limit,
        "positions_at_risk": positions_at_risk,
        "expiring_today": expiring_today,
        "timestamp": now_iso(),
    }


# ─── 6. Risk Alerts ──────────────────────────────────────────────────────────

def get_risk_alerts():
    """
    Retrieve active (unread) risk alerts from notifications table.

    Returns:
        dict with alerts grouped by severity and total counts.
    """
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT * FROM notifications
               WHERE type IN ('STOP_LOSS', 'DELTA_BREACH', 'UNDERLYING_DROP',
                              'EXPIRY_ITM', 'DAILY_LOSS_LIMIT', 'MARGIN_SQUEEZE',
                              'EXERCISE_STT')
               AND read = 0
               ORDER BY
                 CASE severity
                   WHEN 'URGENT' THEN 0
                   WHEN 'WARNING' THEN 1
                   WHEN 'INFO' THEN 2
                   ELSE 3
                 END,
                 created_at DESC"""
        ).fetchall()

        alerts = [dict(row) for row in rows]

        urgent = [a for a in alerts if a.get("severity") == ALERT_SEVERITY_URGENT]
        warnings = [a for a in alerts if a.get("severity") == ALERT_SEVERITY_WARNING]
        info = [a for a in alerts if a.get("severity") == ALERT_SEVERITY_INFO]

        return {
            "total": len(alerts),
            "urgent_count": len(urgent),
            "warning_count": len(warnings),
            "info_count": len(info),
            "urgent": urgent,
            "warnings": warnings,
            "info": info,
            "has_critical": len(urgent) > 0,
            "timestamp": now_iso(),
        }
    finally:
        conn.close()
