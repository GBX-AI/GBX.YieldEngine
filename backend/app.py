"""
Yield Engine v3 — Flask app factory with all API routes.
Complete implementation with all endpoints from the spec.
"""

import os
import json
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from models import (
    init_db, get_db, generate_id, now_iso,
    get_setting, set_setting, get_all_settings,
    SAFETY_HARD_CAPS, SIMULATION_STOCKS, SIMULATION_INDICES,
)
from fee_calculator import calculate_fees, calculate_trade_fees, format_fee_breakdown
from black_scholes import (
    option_price, delta as bs_delta, compute_greeks,
    implied_volatility, probability_otm, RISK_FREE_RATE,
)
from strategy_engine import scan_strategies
from arbitrage_scanner import scan_arbitrage
from trade_tracker import record_trade, close_position, get_open_positions, get_trade_history, update_mtm
from risk_manager import monitor_positions, compute_adjustments, compute_risk_disclosure, get_risk_status, get_risk_alerts
from notification_service import create_notification as ns_create_notification
from dry_run_validator import validate_order
from reconciliation import reconcile_order
from kite_service import kite_service

# In-memory state
state = {
    "permission": "READONLY",
    "kite": None,
    "access_token": None,
    "holdings": [],
    "cash_balance": 0,
    "last_scan": None,
    "recommendations": [],
    "arbitrage_opportunities": [],
}


def create_app():
    app = Flask(__name__, static_folder="static", static_url_path="")
    CORS(app)

    # Initialize database on startup
    with app.app_context():
        init_db()
        # Load holdings from last session if available
        _load_persisted_state()

    # ─── HEALTH / STATUS ─────────────────────────────────────────

    @app.route("/api/status")
    def api_status():
        return jsonify({
            "status": "running",
            "version": "3.0.0",
            "permission": state["permission"],
            "kite_connected": state["access_token"] is not None,
            "holdings_count": len(state["holdings"]),
            "simulation_mode": state["access_token"] is None,
            "timestamp": now_iso(),
        })

    # ─── PERMISSION ──────────────────────────────────────────────

    @app.route("/api/permission", methods=["GET"])
    def get_permission():
        return jsonify({"permission": state["permission"]})

    @app.route("/api/permission", methods=["POST"])
    def set_permission():
        data = request.json or {}
        requested = data.get("permission", "READONLY")

        if requested == "EXECUTE":
            if not data.get("confirm") or not data.get("understand_risk"):
                return jsonify({
                    "error": "Must set confirm=true and understand_risk=true to enable EXECUTE"
                }), 400
            state["permission"] = "EXECUTE"
            _create_notification(
                "PERMISSION_CHANGE", "Execute mode enabled",
                "You have enabled execute mode. All trades require individual confirmation.",
                "WARNING"
            )
        else:
            state["permission"] = "READONLY"
            _create_notification(
                "PERMISSION_CHANGE", "Read-only mode",
                "Execution disabled. No orders can be placed.", "INFO"
            )

        return jsonify({"permission": state["permission"]})

    # ─── SETTINGS ────────────────────────────────────────────────

    @app.route("/api/settings", methods=["GET"])
    def api_get_settings():
        settings = get_all_settings()
        # Mask TOTP secret
        if settings.get("kite_totp_secret"):
            secret = settings["kite_totp_secret"]
            if len(secret) > 6:
                settings["kite_totp_secret"] = secret[:4] + "•" * (len(secret) - 6) + secret[-2:]
            else:
                settings["kite_totp_secret"] = "••••••"
        return jsonify(settings)

    @app.route("/api/settings", methods=["POST"])
    def api_update_settings():
        data = request.json or {}
        updated = []
        for key, value in data.items():
            # Don't allow changing hard caps via settings
            if key in ("MAX_LOTS_PER_ORDER_NIFTY", "MAX_ORDER_VALUE", "MAX_ORDERS_PER_DAY",
                       "MAX_OPEN_POSITIONS", "ALLOWED_EXCHANGES", "ALLOWED_PRODUCTS"):
                continue
            set_setting(key, value)
            updated.append(key)
        return jsonify({"updated": updated})

    @app.route("/api/safety/caps", methods=["GET"])
    def api_safety_caps():
        return jsonify(SAFETY_HARD_CAPS)

    # ─── HOLDINGS / PORTFOLIO ────────────────────────────────────

    @app.route("/api/holdings", methods=["GET"])
    def api_holdings():
        return jsonify({
            "holdings": state["holdings"],
            "cash_balance": state["cash_balance"],
            "summary": _compute_portfolio_summary(),
        })

    @app.route("/api/import/json", methods=["POST"])
    def api_import_json():
        data = request.json or {}
        holdings = data.get("holdings", [])
        cash = data.get("cash_balance", state["cash_balance"])

        state["holdings"] = holdings
        state["cash_balance"] = cash
        return jsonify({
            "imported": len(holdings),
            "cash_balance": cash,
            "summary": _compute_portfolio_summary(),
        })

    @app.route("/api/import/csv", methods=["POST"])
    def api_import_csv():
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]
        content = file.read().decode("utf-8")
        lines = content.strip().split("\n")

        holdings = []
        for i, line in enumerate(lines):
            if i == 0 and "symbol" in line.lower():
                continue  # skip header
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                holding = {
                    "symbol": parts[0].upper(),
                    "qty": int(parts[1]),
                    "avgPrice": float(parts[2]),
                    "ltp": float(parts[3]) if len(parts) > 3 else float(parts[2]),
                }
                holdings.append(holding)

        state["holdings"] = holdings
        return jsonify({
            "imported": len(holdings),
            "summary": _compute_portfolio_summary(),
        })

    @app.route("/api/import/manual", methods=["POST"])
    def api_import_manual():
        data = request.json or {}
        required = ["symbol", "qty", "avgPrice"]
        for field in required:
            if field not in data:
                return jsonify({"error": f"Missing field: {field}"}), 400

        holding = {
            "symbol": data["symbol"].upper(),
            "qty": int(data["qty"]),
            "avgPrice": float(data["avgPrice"]),
            "ltp": float(data.get("ltp", data["avgPrice"])),
        }

        # Check if symbol already exists, update if so
        existing = next((h for h in state["holdings"] if h["symbol"] == holding["symbol"]), None)
        if existing:
            existing.update(holding)
        else:
            state["holdings"].append(holding)

        return jsonify({
            "holding": holding,
            "total_holdings": len(state["holdings"]),
        })

    @app.route("/api/holdings/<symbol>", methods=["DELETE"])
    def api_delete_holding(symbol):
        symbol = symbol.upper()
        state["holdings"] = [h for h in state["holdings"] if h["symbol"] != symbol]
        return jsonify({"deleted": symbol, "remaining": len(state["holdings"])})

    # ─── PORTFOLIO SNAPSHOTS ─────────────────────────────────────

    @app.route("/api/portfolios", methods=["GET"])
    def api_get_portfolios():
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY created_at DESC"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/portfolios", methods=["POST"])
    def api_save_portfolio():
        data = request.json or {}
        name = data.get("name", f"Snapshot {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        snapshot_id = generate_id()
        summary = _compute_portfolio_summary()

        conn = get_db()
        conn.execute(
            "INSERT INTO portfolio_snapshots (id, name, holdings, cash_balance, total_value) VALUES (?, ?, ?, ?, ?)",
            (snapshot_id, name, json.dumps(state["holdings"]),
             state["cash_balance"], summary["portfolio_value"])
        )
        conn.commit()
        conn.close()
        return jsonify({"id": snapshot_id, "name": name})

    @app.route("/api/portfolios/<pid>", methods=["DELETE"])
    def api_delete_portfolio(pid):
        conn = get_db()
        conn.execute("DELETE FROM portfolio_snapshots WHERE id = ?", (pid,))
        conn.commit()
        conn.close()
        return jsonify({"deleted": pid})

    @app.route("/api/portfolios/<pid>/load", methods=["POST"])
    def api_load_portfolio(pid):
        conn = get_db()
        row = conn.execute("SELECT * FROM portfolio_snapshots WHERE id = ?", (pid,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Snapshot not found"}), 404

        state["holdings"] = json.loads(row["holdings"])
        state["cash_balance"] = row["cash_balance"] or 0
        return jsonify({
            "loaded": row["name"],
            "holdings_count": len(state["holdings"]),
            "cash_balance": state["cash_balance"],
        })

    # ─── COLLATERAL ──────────────────────────────────────────────

    @app.route("/api/collateral", methods=["GET"])
    def api_collateral():
        return jsonify(_compute_portfolio_summary())

    # ─── NOTIFICATIONS ───────────────────────────────────────────

    @app.route("/api/notifications", methods=["GET"])
    def api_notifications():
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        offset = (page - 1) * per_page

        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (per_page, offset)
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) as c FROM notifications").fetchone()["c"]
        conn.close()
        return jsonify({
            "notifications": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "per_page": per_page,
        })

    @app.route("/api/notifications/unread-count", methods=["GET"])
    def api_unread_count():
        conn = get_db()
        count = conn.execute(
            "SELECT COUNT(*) as c FROM notifications WHERE read = 0"
        ).fetchone()["c"]
        conn.close()
        return jsonify({"unread_count": count})

    @app.route("/api/notifications/<nid>/read", methods=["POST"])
    def api_mark_read(nid):
        conn = get_db()
        conn.execute("UPDATE notifications SET read = 1 WHERE id = ?", (nid,))
        conn.commit()
        conn.close()
        return jsonify({"marked": nid})

    @app.route("/api/notifications/read-all", methods=["POST"])
    def api_mark_all_read():
        conn = get_db()
        conn.execute("UPDATE notifications SET read = 1 WHERE read = 0")
        conn.commit()
        conn.close()
        return jsonify({"status": "all_read"})

    @app.route("/api/notifications/<nid>", methods=["DELETE"])
    def api_delete_notification(nid):
        conn = get_db()
        conn.execute("DELETE FROM notifications WHERE id = ?", (nid,))
        conn.commit()
        conn.close()
        return jsonify({"deleted": nid})

    # ─── DAILY SUMMARY ───────────────────────────────────────────

    @app.route("/api/daily-summary", methods=["GET"])
    def api_today_summary():
        today = datetime.now().strftime("%Y-%m-%d")
        conn = get_db()
        row = conn.execute("SELECT * FROM daily_summary WHERE date = ?", (today,)).fetchone()
        conn.close()
        if row:
            return jsonify(dict(row))
        return jsonify({
            "date": today, "open_positions": 0, "trades_executed": 0,
            "premium_collected": 0, "premium_paid": 0, "realized_pnl": 0,
            "unrealized_pnl": 0, "margin_used": 0, "collateral_value": 0,
        })

    @app.route("/api/daily-summary/<date>", methods=["GET"])
    def api_summary_by_date(date):
        conn = get_db()
        row = conn.execute("SELECT * FROM daily_summary WHERE date = ?", (date,)).fetchone()
        conn.close()
        if row:
            return jsonify(dict(row))
        return jsonify({"error": "No summary for this date"}), 404

    # ─── TRADES (read-only for Phase 1) ──────────────────────────

    @app.route("/api/trades", methods=["GET"])
    def api_trades():
        conn = get_db()
        strategy = request.args.get("strategy")
        symbol = request.args.get("symbol")
        status = request.args.get("status")

        query = "SELECT * FROM trades WHERE 1=1"
        params = []
        if strategy:
            query += " AND strategy_type = ?"
            params.append(strategy)
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.upper())
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY entry_time DESC"

        rows = conn.execute(query, params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/trades/<tid>", methods=["GET"])
    def api_trade_detail(tid):
        conn = get_db()
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (tid,)).fetchone()
        conn.close()
        if row:
            return jsonify(dict(row))
        return jsonify({"error": "Trade not found"}), 404

    # ─── POSITIONS (read-only for Phase 1) ───────────────────────

    @app.route("/api/positions", methods=["GET"])
    def api_positions():
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM positions WHERE status = 'ACTIVE' ORDER BY expiry_date ASC"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    # ─── ANALYTICS (basic for Phase 1) ───────────────────────────

    @app.route("/api/analytics/summary", methods=["GET"])
    def api_analytics_summary():
        conn = get_db()
        closed = conn.execute(
            "SELECT * FROM trades WHERE status = 'CLOSED'"
        ).fetchall()
        conn.close()

        total_income = sum(t["pnl"] for t in closed if t["pnl"] and t["pnl"] > 0)
        total_loss = sum(t["pnl"] for t in closed if t["pnl"] and t["pnl"] < 0)
        total_fees = sum(t["fees"] for t in closed if t["fees"])
        wins = sum(1 for t in closed if t["pnl"] and t["pnl"] > 0)
        losses = sum(1 for t in closed if t["pnl"] and t["pnl"] <= 0)
        total = wins + losses

        return jsonify({
            "total_income": round(total_income, 2),
            "total_loss": round(total_loss, 2),
            "net_pnl": round(total_income + total_loss, 2),
            "total_fees": round(total_fees, 2),
            "net_after_fees": round(total_income + total_loss - total_fees, 2),
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
        })

    @app.route("/api/analytics/strategy", methods=["GET"])
    def api_analytics_strategy():
        conn = get_db()
        rows = conn.execute(
            "SELECT strategy_type, COUNT(*) as count, "
            "SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as income, "
            "SUM(CASE WHEN pnl <= 0 THEN pnl ELSE 0 END) as loss, "
            "SUM(fees) as total_fees, "
            "AVG(pnl) as avg_pnl "
            "FROM trades WHERE status = 'CLOSED' GROUP BY strategy_type"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/analytics/monthly", methods=["GET"])
    def api_analytics_monthly():
        conn = get_db()
        rows = conn.execute(
            "SELECT strftime('%Y-%m', entry_time) as month, "
            "SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as income, "
            "SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END) as loss, "
            "SUM(pnl) as net, SUM(fees) as fees "
            "FROM trades WHERE status = 'CLOSED' "
            "GROUP BY month ORDER BY month DESC LIMIT 12"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/analytics/daily", methods=["GET"])
    def api_analytics_daily():
        start = request.args.get("start")
        end = request.args.get("end")
        conn = get_db()
        query = "SELECT * FROM daily_summary WHERE 1=1"
        params = []
        if start:
            query += " AND date >= ?"
            params.append(start)
        if end:
            query += " AND date <= ?"
            params.append(end)
        query += " ORDER BY date DESC"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    # ─── FEES ────────────────────────────────────────────────────

    @app.route("/api/fees/estimate", methods=["GET"])
    def api_fees_estimate():
        action = request.args.get("action", "SELL")
        premium = float(request.args.get("premium", 0))
        quantity = int(request.args.get("quantity", 0))
        fees = calculate_fees(action, premium, quantity)
        return jsonify(fees)

    @app.route("/api/fees/summary", methods=["GET"])
    def api_fees_summary():
        period = request.args.get("period", "monthly")
        conn = get_db()

        if period == "daily":
            rows = conn.execute(
                "SELECT strftime('%Y-%m-%d', entry_time) as date, SUM(fees) as total_fees "
                "FROM trades WHERE status = 'CLOSED' GROUP BY date ORDER BY date DESC LIMIT 30"
            ).fetchall()
        elif period == "yearly":
            rows = conn.execute(
                "SELECT strftime('%Y', entry_time) as year, SUM(fees) as total_fees "
                "FROM trades WHERE status = 'CLOSED' GROUP BY year ORDER BY year DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT strftime('%Y-%m', entry_time) as month, SUM(fees) as total_fees "
                "FROM trades WHERE status = 'CLOSED' GROUP BY month ORDER BY month DESC LIMIT 12"
            ).fetchall()

        conn.close()
        return jsonify([dict(r) for r in rows])

    # ─── AUDIT ───────────────────────────────────────────────────

    @app.route("/api/audit/orders", methods=["GET"])
    def api_audit_orders():
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM order_audit ORDER BY timestamp DESC LIMIT 100"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    # ─── GTT ─────────────────────────────────────────────────────

    @app.route("/api/gtt/active", methods=["GET"])
    def api_gtt_active():
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM gtt_orders WHERE status = 'ACTIVE' ORDER BY created_at DESC"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    # ─── KITE AUTH ────────────────────────────────────────────────

    @app.route("/api/kite/login", methods=["GET"])
    def api_kite_login():
        login_url = kite_service.get_login_url()
        return jsonify({"login_url": login_url, "authenticated": kite_service.is_authenticated()})

    @app.route("/api/callback", methods=["GET"])
    def api_kite_callback():
        request_token = request.args.get("request_token")
        if not request_token:
            return jsonify({"error": "No request_token provided"}), 400
        try:
            kite_service.set_access_token(request_token)
            state["access_token"] = request_token
            return jsonify({"status": "authenticated"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/kite/auto-login", methods=["POST"])
    def api_kite_auto_login():
        try:
            result = kite_service.initialize()
            if kite_service.is_authenticated():
                state["access_token"] = "active"
                return jsonify({"status": "authenticated", "message": "Auto-login successful"})
            return jsonify({"status": "failed", "message": "Auto-login failed, use manual login",
                          "login_url": kite_service.get_login_url()}), 401
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/import/kite", methods=["POST"])
    def api_import_kite():
        if not kite_service.is_authenticated():
            return jsonify({"error": "Kite not connected"}), 401
        try:
            holdings = kite_service.get_holdings()
            state["holdings"] = holdings
            return jsonify({"imported": len(holdings), "summary": _compute_portfolio_summary()})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─── SCAN / RECOMMENDATIONS ──────────────────────────────────

    @app.route("/api/scan", methods=["POST"])
    def api_scan():
        data = request.json or {}
        cash = data.get("cash_balance", state["cash_balance"])
        state["cash_balance"] = cash

        settings = get_all_settings()
        recs = scan_strategies(state["holdings"], cash, settings)
        state["recommendations"] = recs
        state["last_scan"] = now_iso()

        arbs = scan_arbitrage(None, simulation=not kite_service.is_authenticated())
        state["arbitrage_opportunities"] = arbs

        _create_notification("SCAN_COMPLETE", "Scan complete",
            f"Found {len(recs)} strategy opportunities and {len(arbs)} arbitrage opportunities.",
            "INFO", "/scanner")

        return jsonify({
            "recommendations": recs,
            "arbitrage": arbs,
            "scanned_at": state["last_scan"],
        })

    @app.route("/api/recommendations", methods=["GET"])
    def api_recommendations():
        safety = request.args.get("safety")
        strategy = request.args.get("type")

        recs = state["recommendations"]
        if safety and safety != "ALL":
            recs = [r for r in recs if r.get("safety_tag") == safety]
        if strategy and strategy != "ALL":
            recs = [r for r in recs if r.get("strategy_type") == strategy]

        return jsonify(recs)

    @app.route("/api/arbitrage", methods=["GET"])
    def api_arbitrage():
        return jsonify(state["arbitrage_opportunities"])

    # ─── EXECUTE ─────────────────────────────────────────────────

    @app.route("/api/execute", methods=["POST"])
    def api_execute():
        if state["permission"] != "EXECUTE":
            return jsonify({"error": "Permission denied. Enable EXECUTE mode first."}), 403

        data = request.json or {}
        rec_id = data.get("rec_id")
        confirm_execution = data.get("confirm_execution", False)
        acknowledge_risk = data.get("acknowledge_risk", False)

        if not confirm_execution or not acknowledge_risk:
            return jsonify({"error": "Must confirm execution and acknowledge risk"}), 400

        # Find recommendation
        rec = next((r for r in state["recommendations"] if r.get("id") == rec_id), None)
        if not rec:
            return jsonify({"error": "Recommendation not found"}), 404

        # Build order legs for validation
        legs = rec.get("legs", [])
        order_legs = []
        for leg in legs:
            order_legs.append({
                "tradingsymbol": f"{rec['symbol']}{leg.get('strike', '')}{leg.get('type', 'PE')}",
                "qty": leg.get("quantity", leg.get("qty", 0)),
                "price": leg.get("premium", leg.get("price", 0)),
                "exchange": "NFO",
                "product": "NRML",
                "action": leg.get("action", "SELL"),
            })

        # Dry run validation
        validation = validate_order(order_legs, state)
        if not validation["valid"]:
            # Log rejected order
            conn = get_db()
            conn.execute(
                "INSERT INTO order_audit (id, action, rec_id, legs, dry_run_result, user_confirmed, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (generate_id(), "PLACE", rec_id, json.dumps(order_legs),
                 json.dumps(validation["errors"]), 1, "REJECTED_DRY_RUN")
            )
            conn.commit()
            conn.close()
            return jsonify({"error": "Order rejected by dry run validation",
                          "validation_errors": validation["errors"]}), 400

        # Preview mode (dry_run=true) — return risk disclosure without executing
        if data.get("dry_run", False):
            disclosure = compute_risk_disclosure(rec)
            return jsonify({"preview": True, "risk_disclosure": disclosure, "order_legs": order_legs})

        # Execute (simulation or live)
        try:
            if kite_service.is_authenticated():
                # Live execution via Kite
                kite_order_ids = []
                for leg in order_legs:
                    order_id = kite_service.place_order(leg)
                    kite_order_ids.append(order_id)

                    # Post-order reconciliation
                    recon = reconcile_order(leg, order_id, kite_service)
                    if recon["alert"]:
                        state["permission"] = "READONLY"
                        return jsonify({"error": recon["message"], "reconciliation": recon}), 500
            else:
                kite_order_ids = [f"SIM-{generate_id()[:8]}"]

            # Record trade
            trade_id = record_trade(
                rec_id=rec_id,
                strategy_type=rec["strategy_type"],
                symbol=rec["symbol"],
                direction=rec.get("direction", "SELL"),
                legs=json.dumps(legs),
                entry_premium=rec["premium_income"],
                margin_used=rec.get("margin_needed", 0),
            )

            # Log successful order
            conn = get_db()
            conn.execute(
                "INSERT INTO order_audit (id, action, rec_id, trade_id, legs, dry_run_result, "
                "kite_response, user_confirmed, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (generate_id(), "PLACE", rec_id, trade_id, json.dumps(order_legs),
                 "PASS", json.dumps(kite_order_ids), 1, "EXECUTED")
            )
            conn.commit()
            conn.close()

            return jsonify({
                "status": "executed",
                "trade_id": trade_id,
                "kite_order_ids": kite_order_ids,
                "simulation": not kite_service.is_authenticated(),
            })

        except Exception as e:
            conn = get_db()
            conn.execute(
                "INSERT INTO order_audit (id, action, rec_id, legs, dry_run_result, "
                "kite_response, user_confirmed, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (generate_id(), "PLACE", rec_id, json.dumps(order_legs),
                 "PASS", json.dumps({"error": str(e)}), 1, "KITE_ERROR")
            )
            conn.commit()
            conn.close()
            return jsonify({"error": f"Execution failed: {str(e)}"}), 500

    # ─── POSITIONS MANAGEMENT ────────────────────────────────────

    @app.route("/api/positions/<pid>/close", methods=["POST"])
    def api_close_position(pid):
        if state["permission"] != "EXECUTE":
            return jsonify({"error": "Permission denied. Enable EXECUTE mode first."}), 403

        data = request.json or {}
        exit_premium = data.get("exit_premium", 0)
        exit_reason = data.get("exit_reason", "MANUAL")

        try:
            result = close_position(pid, exit_premium, exit_reason)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/positions/<pid>/roll", methods=["POST"])
    def api_roll_position(pid):
        if state["permission"] != "EXECUTE":
            return jsonify({"error": "Permission denied. Enable EXECUTE mode first."}), 403

        data = request.json or {}
        # Close current position and open new one at next expiry
        try:
            close_result = close_position(pid, data.get("exit_premium", 0), "ROLLED")
            return jsonify({"status": "rolled", "closed": close_result})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/positions/<pid>/adjustments", methods=["GET"])
    def api_position_adjustments(pid):
        try:
            adjustments = compute_adjustments(pid)
            return jsonify(adjustments)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/positions/<pid>/adjust", methods=["POST"])
    def api_execute_adjustment(pid):
        if state["permission"] != "EXECUTE":
            return jsonify({"error": "Permission denied. Enable EXECUTE mode first."}), 403

        data = request.json or {}
        adjustment_type = data.get("adjustment_type")
        if not adjustment_type:
            return jsonify({"error": "adjustment_type required"}), 400

        try:
            from risk_manager import execute_adjustment
            result = execute_adjustment(pid, adjustment_type, data)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─── RISK ────────────────────────────────────────────────────

    @app.route("/api/risk/status", methods=["GET"])
    def api_risk_status():
        try:
            status = get_risk_status()
            return jsonify(status)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/risk/alerts", methods=["GET"])
    def api_risk_alerts():
        try:
            alerts = get_risk_alerts()
            return jsonify(alerts)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings/risk-profile", methods=["GET"])
    def api_get_risk_profile():
        profile = get_setting("risk_profile") or "moderate"
        from strike_selector import RISK_PROFILES
        profile_data = RISK_PROFILES.get(profile.capitalize(), RISK_PROFILES["Moderate"])
        return jsonify({"profile": profile, "details": profile_data})

    @app.route("/api/settings/risk-profile", methods=["POST"])
    def api_set_risk_profile():
        data = request.json or {}
        profile = data.get("profile", "moderate").lower()
        if profile not in ("conservative", "moderate", "aggressive"):
            return jsonify({"error": "Invalid profile. Use conservative, moderate, or aggressive."}), 400
        set_setting("risk_profile", profile)
        return jsonify({"profile": profile})

    @app.route("/api/settings/circuit-breaker", methods=["POST"])
    def api_circuit_breaker():
        data = request.json or {}
        enabled = data.get("enabled", False)
        set_setting("circuit_breaker_enabled", str(enabled).lower())
        return jsonify({"circuit_breaker_enabled": enabled})

    # ─── GTT DELETE ──────────────────────────────────────────────

    @app.route("/api/gtt/<gtt_id>", methods=["DELETE"])
    def api_delete_gtt(gtt_id):
        if state["permission"] != "EXECUTE":
            return jsonify({"error": "Permission denied. Enable EXECUTE mode first."}), 403
        conn = get_db()
        conn.execute("UPDATE gtt_orders SET status = 'CANCELLED' WHERE id = ?", (gtt_id,))
        conn.commit()
        conn.close()
        return jsonify({"cancelled": gtt_id})

    # ─── SERVE FRONTEND ──────────────────────────────────────────

    @app.route("/")
    def serve_frontend():
        return send_from_directory(app.static_folder, "index.html")

    @app.errorhandler(404)
    def not_found(e):
        # SPA fallback
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found"}), 404
        return send_from_directory(app.static_folder, "index.html")

    return app


# ─── HELPERS ─────────────────────────────────────────────────────

def _compute_portfolio_summary():
    """Compute collateral and portfolio summary from holdings."""
    holdings = state["holdings"]
    cash = state["cash_balance"]

    total_value = 0
    non_cash_collateral = 0
    detailed = []

    for h in holdings:
        symbol = h["symbol"]
        qty = h["qty"]
        ltp = h.get("ltp", h["avgPrice"])
        avg = h["avgPrice"]
        value = qty * ltp
        pnl = (ltp - avg) * qty

        # Get haircut from simulation data or default
        sim = SIMULATION_STOCKS.get(symbol, {})
        haircut = sim.get("haircut", 0.25)
        lot_size = sim.get("lotSize", 0)

        collateral = value * (1 - haircut)
        non_cash_collateral += collateral

        # Determine status
        if lot_size > 0 and qty >= lot_size:
            status = "WRITE_READY"
        elif lot_size > 0 and qty > 0:
            status = "PARTIAL"
        elif qty > 0:
            status = "COLLATERAL"
        else:
            status = "CASH_EQUIV"

        detailed.append({
            **h,
            "ltp": ltp,
            "value": round(value, 2),
            "pnl": round(pnl, 2),
            "haircut": haircut,
            "collateral": round(collateral, 2),
            "lot_size": lot_size,
            "status": status,
        })

        total_value += value

    # SEBI 50% cash rule
    cash_equivalent = cash * 0.5
    usable_margin = non_cash_collateral + cash_equivalent

    return {
        "portfolio_value": round(total_value, 2),
        "unrealized_pnl": round(sum(d["pnl"] for d in detailed), 2),
        "non_cash_collateral": round(non_cash_collateral, 2),
        "cash_balance": cash,
        "cash_equivalent": round(cash_equivalent, 2),
        "usable_margin": round(usable_margin, 2),
        "holdings": detailed,
    }


def _create_notification(ntype, title, message, severity="INFO", action_url=None):
    """Create a notification in the database."""
    conn = get_db()
    conn.execute(
        "INSERT INTO notifications (id, type, title, message, severity, action_url) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (generate_id(), ntype, title, message, severity, action_url)
    )
    conn.commit()
    conn.close()


def _load_persisted_state():
    """Load any persisted state from the database on startup."""
    # Check if we have a recent access token
    token = get_setting("access_token")
    token_date = get_setting("access_token_date")
    today = datetime.now().strftime("%Y-%m-%d")

    if token and token_date == today:
        state["access_token"] = token


# ─── APP ENTRY POINT ─────────────────────────────────────────────

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
