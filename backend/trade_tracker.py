"""
Position & P&L Tracking — Yield Engine v3, Section 4B.

Records trades, manages open positions, computes realized/unrealized P&L,
and maintains daily summaries.

Functions:
  - record_trade       — open a new trade + position
  - close_position     — close a trade with exit info + P&L
  - update_mtm         — mark-to-market an open position
  - get_open_positions  — list all ACTIVE positions
  - get_trade_history   — query trades with optional filters
  - update_daily_summary — aggregate today's stats
"""

import json
from datetime import datetime

from models import get_db, generate_id, now_iso, SIMULATION_STOCKS
from fee_calculator import calculate_fees, calculate_trade_fees


# ─── Exit reason enum ────────────────────────────────────────────────────────

VALID_EXIT_REASONS = ("EXPIRY", "MANUAL", "STOP_LOSS", "TARGET", "ROLLED")


# ─── P&L Calculation ─────────────────────────────────────────────────────────

def _parse_legs(legs):
    """Parse legs from JSON string or return list as-is."""
    if isinstance(legs, str):
        return json.loads(legs)
    return legs


def _calculate_pnl(direction, legs, entry_premium, exit_premium, fees_total):
    """
    Calculate realized P&L based on direction and leg structure.

    Sold options:   PnL = (entry_premium - exit_premium) * lot_size * lots - fees
    Bought options: PnL = (exit_premium - entry_premium) * lot_size * lots - fees
    Spreads:        PnL = net_credit - net_debit_at_close - fees

    Args:
        direction:     "SELL", "BUY", or "SPREAD"
        legs:          list of leg dicts with action, premium, quantity, lot_size, lots
        entry_premium: total premium at entry (net for spreads)
        exit_premium:  total premium at exit (net for spreads)
        fees_total:    total fees for entry + exit

    Returns:
        float: net realized P&L
    """
    parsed_legs = _parse_legs(legs)

    if direction == "SPREAD":
        # net_credit (entry) - net_debit_at_close (exit) - fees
        pnl = entry_premium - exit_premium - fees_total
    elif direction == "SELL":
        # Sold options profit when premium decays
        total_qty = sum(
            leg.get("lot_size", 1) * leg.get("lots", 1)
            for leg in parsed_legs
            if leg.get("action") == "SELL"
        )
        if total_qty == 0:
            total_qty = sum(
                leg.get("quantity", leg.get("lot_size", 1) * leg.get("lots", 1))
                for leg in parsed_legs
            )
        pnl = (entry_premium - exit_premium) * total_qty - fees_total
    else:
        # Bought options profit when premium rises
        total_qty = sum(
            leg.get("lot_size", 1) * leg.get("lots", 1)
            for leg in parsed_legs
            if leg.get("action") == "BUY"
        )
        if total_qty == 0:
            total_qty = sum(
                leg.get("quantity", leg.get("lot_size", 1) * leg.get("lots", 1))
                for leg in parsed_legs
            )
        pnl = (exit_premium - entry_premium) * total_qty - fees_total

    return round(pnl, 2)


def _calculate_unrealized_pnl(direction, legs, entry_premium, current_premium):
    """Calculate unrealized P&L (no fees deducted — fees apply on close)."""
    parsed_legs = _parse_legs(legs)

    if direction == "SPREAD":
        return round(entry_premium - current_premium, 2)

    if direction == "SELL":
        total_qty = sum(
            leg.get("lot_size", 1) * leg.get("lots", 1)
            for leg in parsed_legs
            if leg.get("action") == "SELL"
        )
        if total_qty == 0:
            total_qty = sum(
                leg.get("quantity", leg.get("lot_size", 1) * leg.get("lots", 1))
                for leg in parsed_legs
            )
        return round((entry_premium - current_premium) * total_qty, 2)

    # BUY
    total_qty = sum(
        leg.get("lot_size", 1) * leg.get("lots", 1)
        for leg in parsed_legs
        if leg.get("action") == "BUY"
    )
    if total_qty == 0:
        total_qty = sum(
            leg.get("quantity", leg.get("lot_size", 1) * leg.get("lots", 1))
            for leg in parsed_legs
        )
    return round((current_premium - entry_premium) * total_qty, 2)


# ─── Trade Recording ─────────────────────────────────────────────────────────

def record_trade(rec_id, strategy_type, symbol, direction, legs, entry_premium, margin_used):
    """
    Insert a new trade with status=OPEN, create its position, send a notification,
    and update the daily summary.

    Args:
        rec_id:         recommendation ID that triggered this trade
        strategy_type:  e.g. COVERED_CALL, CASH_SECURED_PUT, PUT_CREDIT_SPREAD
        symbol:         underlying symbol (NIFTY, RELIANCE, etc.)
        direction:      SELL, BUY, or SPREAD
        legs:           list of leg dicts (action, strike, option_type, premium, quantity, lot_size, lots, expiry)
        entry_premium:  net premium collected/paid per unit
        margin_used:    margin blocked for this trade

    Returns:
        dict with trade_id and position_id
    """
    trade_id = generate_id()
    position_id = generate_id()
    notification_id = generate_id()
    ts = now_iso()
    legs_json = json.dumps(legs) if not isinstance(legs, str) else legs

    # Compute entry fees
    parsed_legs = _parse_legs(legs)
    fee_legs = []
    for leg in parsed_legs:
        fee_legs.append({
            "action": leg.get("action", "SELL"),
            "premium": leg.get("premium", entry_premium),
            "quantity": leg.get("quantity", leg.get("lot_size", 1) * leg.get("lots", 1)),
            "is_exercise": False,
        })
    trade_fees = calculate_trade_fees(fee_legs)
    fees = trade_fees["total"]

    # Extract expiry from first leg if available
    expiry_date = None
    if parsed_legs:
        expiry_date = parsed_legs[0].get("expiry")

    conn = get_db()
    try:
        cursor = conn.cursor()

        # Insert trade
        cursor.execute(
            """INSERT INTO trades
               (id, rec_id, strategy_type, symbol, direction, legs,
                entry_premium, entry_time, fees, margin_used, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')""",
            (trade_id, rec_id, strategy_type, symbol, direction,
             legs_json, entry_premium, ts, fees, margin_used),
        )

        # Create position
        cursor.execute(
            """INSERT INTO positions
               (id, trade_id, symbol, strategy_type, legs, entry_premium,
                current_premium, unrealized_pnl, margin_blocked, expiry_date,
                last_updated, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 'ACTIVE')""",
            (position_id, trade_id, symbol, strategy_type, legs_json,
             entry_premium, entry_premium, margin_used, expiry_date, ts),
        )

        # Create notification
        cursor.execute(
            """INSERT INTO notifications (id, type, title, message, severity)
               VALUES (?, 'TRADE_OPENED', ?, ?, 'INFO')""",
            (
                notification_id,
                f"Trade Opened — {strategy_type}",
                f"{direction} {symbol} | Premium ₹{entry_premium:.2f} | Margin ₹{margin_used:.0f}",
            ),
        )

        conn.commit()
    finally:
        conn.close()

    # Update daily summary
    _update_daily_summary_safe()

    return {"trade_id": trade_id, "position_id": position_id}


# ─── Position Closing ────────────────────────────────────────────────────────

def close_position(trade_id, exit_premium, exit_reason):
    """
    Close a trade: compute P&L, update trades + positions, notify, refresh summary.

    Args:
        trade_id:     ID of the trade to close
        exit_premium: premium at exit (per unit, net for spreads)
        exit_reason:  one of EXPIRY, MANUAL, STOP_LOSS, TARGET, ROLLED

    Returns:
        dict with trade_id, pnl, fees, exit_reason

    Raises:
        ValueError: if trade not found, already closed, or invalid exit_reason
    """
    if exit_reason not in VALID_EXIT_REASONS:
        raise ValueError(f"Invalid exit_reason '{exit_reason}'. Must be one of {VALID_EXIT_REASONS}")

    conn = get_db()
    try:
        cursor = conn.cursor()

        # Fetch trade
        trade = cursor.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if not trade:
            raise ValueError(f"Trade {trade_id} not found")
        if trade["status"] != "OPEN":
            raise ValueError(f"Trade {trade_id} is already {trade['status']}")

        ts = now_iso()
        direction = trade["direction"]
        legs = trade["legs"]
        entry_premium = trade["entry_premium"]
        entry_fees = trade["fees"] or 0

        # Calculate exit fees
        parsed_legs = _parse_legs(legs)
        exit_fee_legs = []
        for leg in parsed_legs:
            # On close, actions are reversed
            original_action = leg.get("action", "SELL")
            close_action = "BUY" if original_action == "SELL" else "SELL"
            is_exercise = exit_reason == "EXPIRY"
            exit_fee_legs.append({
                "action": close_action,
                "premium": exit_premium if len(parsed_legs) == 1 else leg.get("premium", exit_premium),
                "quantity": leg.get("quantity", leg.get("lot_size", 1) * leg.get("lots", 1)),
                "is_exercise": is_exercise,
            })
        exit_trade_fees = calculate_trade_fees(exit_fee_legs)
        exit_fees = exit_trade_fees["total"]

        total_fees = round(entry_fees + exit_fees, 2)

        # Calculate realized P&L
        pnl = _calculate_pnl(direction, legs, entry_premium, exit_premium, total_fees)

        # Update trade
        cursor.execute(
            """UPDATE trades
               SET exit_premium = ?, exit_time = ?, exit_reason = ?,
                   pnl = ?, fees = ?, status = 'CLOSED'
               WHERE id = ?""",
            (exit_premium, ts, exit_reason, pnl, total_fees, trade_id),
        )

        # Update position
        cursor.execute(
            """UPDATE positions
               SET current_premium = ?, unrealized_pnl = 0,
                   last_updated = ?, status = 'CLOSED'
               WHERE trade_id = ?""",
            (exit_premium, ts, trade_id),
        )

        # Create notification
        notification_id = generate_id()
        severity = "SUCCESS" if pnl >= 0 else "WARNING"
        pnl_label = f"+₹{pnl:.2f}" if pnl >= 0 else f"-₹{abs(pnl):.2f}"
        cursor.execute(
            """INSERT INTO notifications (id, type, title, message, severity)
               VALUES (?, 'TRADE_CLOSED', ?, ?, ?)""",
            (
                notification_id,
                f"Trade Closed — {trade['strategy_type']}",
                f"{trade['symbol']} | {exit_reason} | P&L {pnl_label} (fees ₹{total_fees:.2f})",
                severity,
            ),
        )

        conn.commit()
    finally:
        conn.close()

    # Refresh daily summary
    _update_daily_summary_safe()

    return {
        "trade_id": trade_id,
        "pnl": pnl,
        "fees": total_fees,
        "exit_reason": exit_reason,
    }


# ─── Mark-to-Market ──────────────────────────────────────────────────────────

def update_mtm(position_id, current_premium):
    """
    Update a position's current premium and unrealized P&L.
    Called every 5 minutes during market hours.

    Args:
        position_id:     ID of the position to update
        current_premium: latest market premium per unit

    Returns:
        dict with position_id, current_premium, unrealized_pnl

    Raises:
        ValueError: if position not found or not ACTIVE
    """
    conn = get_db()
    try:
        cursor = conn.cursor()

        pos = cursor.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
        if not pos:
            raise ValueError(f"Position {position_id} not found")
        if pos["status"] != "ACTIVE":
            raise ValueError(f"Position {position_id} is {pos['status']}, not ACTIVE")

        # Fetch direction from parent trade
        trade = cursor.execute("SELECT direction FROM trades WHERE id = ?", (pos["trade_id"],)).fetchone()
        direction = trade["direction"] if trade else "SELL"

        unrealized_pnl = _calculate_unrealized_pnl(
            direction, pos["legs"], pos["entry_premium"], current_premium
        )

        ts = now_iso()
        cursor.execute(
            """UPDATE positions
               SET current_premium = ?, unrealized_pnl = ?, last_updated = ?
               WHERE id = ?""",
            (current_premium, unrealized_pnl, ts, position_id),
        )

        # Also update days_held
        if pos["last_updated"]:
            try:
                entry_time_str = cursor.execute(
                    "SELECT entry_time FROM trades WHERE id = ?", (pos["trade_id"],)
                ).fetchone()
                if entry_time_str:
                    entry_dt = datetime.fromisoformat(entry_time_str["entry_time"])
                    days_held = (datetime.utcnow() - entry_dt).days
                    cursor.execute(
                        "UPDATE positions SET days_held = ? WHERE id = ?",
                        (days_held, position_id),
                    )
            except (ValueError, TypeError):
                pass

        conn.commit()
    finally:
        conn.close()

    return {
        "position_id": position_id,
        "current_premium": current_premium,
        "unrealized_pnl": unrealized_pnl,
    }


# ─── Queries ──────────────────────────────────────────────────────────────────

def get_open_positions():
    """
    Return all ACTIVE positions with their parent trade info.

    Returns:
        list of dicts, each containing position + trade fields
    """
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT p.*, t.direction, t.rec_id, t.entry_time, t.margin_used
               FROM positions p
               JOIN trades t ON t.id = p.trade_id
               WHERE p.status = 'ACTIVE'
               ORDER BY t.entry_time DESC"""
        ).fetchall()
        positions = [dict(row) for row in rows]
    finally:
        conn.close()

    # Parse legs JSON for convenience
    for pos in positions:
        pos["legs"] = _parse_legs(pos["legs"])

    return positions


def get_trade_history(filters=None):
    """
    Return trades with optional filters.

    Args:
        filters: dict with optional keys:
            - status:        "OPEN" | "CLOSED"
            - symbol:        e.g. "NIFTY"
            - strategy_type: e.g. "COVERED_CALL"
            - exit_reason:   e.g. "EXPIRY"
            - from_date:     ISO date string (inclusive)
            - to_date:       ISO date string (inclusive)
            - limit:         max rows (default 100)
            - offset:        pagination offset (default 0)

    Returns:
        list of trade dicts
    """
    filters = filters or {}
    conditions = []
    params = []

    if "status" in filters:
        conditions.append("status = ?")
        params.append(filters["status"])

    if "symbol" in filters:
        conditions.append("symbol = ?")
        params.append(filters["symbol"])

    if "strategy_type" in filters:
        conditions.append("strategy_type = ?")
        params.append(filters["strategy_type"])

    if "exit_reason" in filters:
        conditions.append("exit_reason = ?")
        params.append(filters["exit_reason"])

    if "from_date" in filters:
        conditions.append("entry_time >= ?")
        params.append(filters["from_date"])

    if "to_date" in filters:
        conditions.append("entry_time <= ?")
        params.append(filters["to_date"])

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    limit = filters.get("limit", 100)
    offset = filters.get("offset", 0)

    query = f"""SELECT * FROM trades
                WHERE {where_clause}
                ORDER BY entry_time DESC
                LIMIT ? OFFSET ?"""
    params.extend([limit, offset])

    conn = get_db()
    try:
        rows = conn.execute(query, params).fetchall()
        trades = [dict(row) for row in rows]
    finally:
        conn.close()

    # Parse legs JSON
    for trade in trades:
        trade["legs"] = _parse_legs(trade["legs"])

    return trades


# ─── Daily Summary ────────────────────────────────────────────────────────────

def update_daily_summary(date=None):
    """
    Aggregate today's (or given date's) trading data into daily_summary.

    Computes:
        - open_positions count
        - trades_executed count
        - premium_collected / premium_paid
        - realized_pnl (sum of closed trade P&Ls)
        - unrealized_pnl (sum of active position P&Ls)
        - margin_used (sum of active position margins)

    Args:
        date: ISO date string (YYYY-MM-DD). Defaults to today.
    """
    if date is None:
        date = datetime.utcnow().strftime("%Y-%m-%d")

    conn = get_db()
    try:
        cursor = conn.cursor()

        # Count open positions
        open_count = cursor.execute(
            "SELECT COUNT(*) as c FROM positions WHERE status = 'ACTIVE'"
        ).fetchone()["c"]

        # Trades executed today
        trades_today = cursor.execute(
            "SELECT COUNT(*) as c FROM trades WHERE entry_time LIKE ?",
            (f"{date}%",)
        ).fetchone()["c"]

        # Premium collected (SELL trades opened today)
        collected = cursor.execute(
            """SELECT COALESCE(SUM(entry_premium), 0) as total
               FROM trades WHERE direction IN ('SELL', 'SPREAD')
               AND entry_time LIKE ?""",
            (f"{date}%",)
        ).fetchone()["total"]

        # Premium paid (BUY trades opened today)
        paid = cursor.execute(
            """SELECT COALESCE(SUM(entry_premium), 0) as total
               FROM trades WHERE direction = 'BUY'
               AND entry_time LIKE ?""",
            (f"{date}%",)
        ).fetchone()["total"]

        # Realized P&L (trades closed today)
        realized = cursor.execute(
            """SELECT COALESCE(SUM(pnl), 0) as total
               FROM trades WHERE status = 'CLOSED'
               AND exit_time LIKE ?""",
            (f"{date}%",)
        ).fetchone()["total"]

        # Unrealized P&L (all active positions)
        unrealized = cursor.execute(
            "SELECT COALESCE(SUM(unrealized_pnl), 0) as total FROM positions WHERE status = 'ACTIVE'"
        ).fetchone()["total"]

        # Margin used (all active positions)
        margin = cursor.execute(
            "SELECT COALESCE(SUM(margin_blocked), 0) as total FROM positions WHERE status = 'ACTIVE'"
        ).fetchone()["total"]

        # Upsert daily_summary
        cursor.execute(
            """INSERT INTO daily_summary
               (date, open_positions, trades_executed, premium_collected,
                premium_paid, realized_pnl, unrealized_pnl, margin_used)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   open_positions = excluded.open_positions,
                   trades_executed = excluded.trades_executed,
                   premium_collected = excluded.premium_collected,
                   premium_paid = excluded.premium_paid,
                   realized_pnl = excluded.realized_pnl,
                   unrealized_pnl = excluded.unrealized_pnl,
                   margin_used = excluded.margin_used""",
            (date, open_count, trades_today, collected, paid, realized, unrealized, margin),
        )

        conn.commit()
    finally:
        conn.close()


def _update_daily_summary_safe():
    """Wrapper that silently handles errors so trade operations don't fail."""
    try:
        update_daily_summary()
    except Exception:
        pass
