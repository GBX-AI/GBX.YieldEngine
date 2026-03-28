"""
Yield Engine v3 — Flask app factory with all API routes.
Multi-user implementation with per-user Kite connections and auth.
"""

import csv
import io
import logging
import os
import json
from collections import defaultdict
from datetime import datetime, date
from flask import Flask, request, jsonify, send_from_directory, g, redirect
from flask_cors import CORS

# Configure root logger so all modules output to gunicorn
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

from models import (
    init_db, get_db, generate_id, now_iso,
    get_setting, set_setting, get_all_settings,
    get_all_holdings, save_holdings, upsert_holding, delete_holding as db_delete_holding,
    get_cash_balance, save_cash_balance,
    update_user_kite_token, clear_user_kite_token, get_user_kite_token,
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
from kite_service import KiteService, get_kite_for_user, get_login_url, exchange_request_token
from auth import auth_bp, require_auth
import live_price_service

# Per-user in-memory state (transient session data only — holdings/cash persisted in SQLite)
_user_state = {}  # {user_id: {recommendations: [], arbitrage_opportunities: [], last_scan: None}}

def _get_user_state(user_id):
    if user_id not in _user_state:
        _user_state[user_id] = {"recommendations": [], "arbitrage_opportunities": [], "last_scan": None, "permission": "READONLY"}
    return _user_state[user_id]


def create_app():
    app = Flask(__name__, static_folder="static", static_url_path="")
    CORS(app)

    # Register auth blueprint
    app.register_blueprint(auth_bp)

    # Initialize database on startup
    with app.app_context():
        init_db()
        # Pre-warm price cache in background so first page load is fast
        _warm_price_cache()

    # ─── HEALTH / STATUS ─────────────────────────────────────────

    @app.route("/api/status")
    def api_status():
        return jsonify({
            "status": "running",
            "version": "3.0.0",
            "price_sources": live_price_service.get_price_source_status(),
            "timestamp": now_iso(),
        })

    # ─── PERMISSION ──────────────────────────────────────────────

    @app.route("/api/permission", methods=["GET"])
    @require_auth
    def get_permission():
        user_id = g.current_user["id"]
        return jsonify({"permission": _get_user_state(user_id)["permission"]})

    @app.route("/api/permission", methods=["POST"])
    @require_auth
    def set_permission():
        user_id = g.current_user["id"]
        data = request.json or {}
        requested = data.get("permission", "READONLY")

        if requested == "EXECUTE":
            if not data.get("confirm") or not data.get("understand_risk"):
                return jsonify({
                    "error": "Must set confirm=true and understand_risk=true to enable EXECUTE"
                }), 400
            _get_user_state(user_id)["permission"] = "EXECUTE"
            _create_notification(
                "PERMISSION_CHANGE", "Execute mode enabled",
                "You have enabled execute mode. All trades require individual confirmation.",
                "WARNING"
            )
        else:
            _get_user_state(user_id)["permission"] = "READONLY"
            _create_notification(
                "PERMISSION_CHANGE", "Read-only mode",
                "Execution disabled. No orders can be placed.", "INFO"
            )

        return jsonify({"permission": _get_user_state(user_id)["permission"]})

    # ─── SETTINGS ────────────────────────────────────────────────

    @app.route("/api/settings", methods=["GET"])
    @require_auth
    def api_get_settings():
        user_id = g.current_user["id"]
        settings = get_all_settings(user_id)
        # Mask TOTP secret
        if settings.get("kite_totp_secret"):
            secret = settings["kite_totp_secret"]
            if len(secret) > 6:
                settings["kite_totp_secret"] = secret[:4] + "•" * (len(secret) - 6) + secret[-2:]
            else:
                settings["kite_totp_secret"] = "••••••"
        return jsonify(settings)

    @app.route("/api/settings", methods=["POST"])
    @require_auth
    def api_update_settings():
        user_id = g.current_user["id"]
        data = request.json or {}
        updated = []
        for key, value in data.items():
            # Don't allow changing hard caps via settings
            if key in ("MAX_LOTS_PER_ORDER_NIFTY", "MAX_ORDER_VALUE", "MAX_ORDERS_PER_DAY",
                       "MAX_OPEN_POSITIONS", "ALLOWED_EXCHANGES", "ALLOWED_PRODUCTS"):
                continue
            set_setting(key, value, user_id)
            updated.append(key)
        return jsonify({"updated": updated})

    @app.route("/api/safety/caps", methods=["GET"])
    @require_auth
    def api_safety_caps():
        return jsonify(SAFETY_HARD_CAPS)

    # ─── HOLDINGS / PORTFOLIO ────────────────────────────────────

    @app.route("/api/holdings", methods=["GET"])
    @require_auth
    def api_holdings():
        user_id = g.current_user["id"]
        summary = _compute_portfolio_summary(user_id)
        return jsonify({
            "holdings": summary["holdings"],  # enriched with value, pnl, status
            "cash_balance": get_cash_balance(user_id),
            "summary": summary,
        })

    @app.route("/api/import/json", methods=["POST"])
    @require_auth
    def api_import_json():
        user_id = g.current_user["id"]
        data = request.json or {}
        holdings = data.get("holdings", [])
        cash = data.get("cash_balance", get_cash_balance(user_id))

        save_holdings(holdings, user_id)
        save_cash_balance(cash, user_id)
        return jsonify({
            "imported": len(holdings),
            "cash_balance": cash,
            "summary": _compute_portfolio_summary(user_id),
        })

    # ─── CSV COLUMN AUTO-DETECTION ──────────────────────────────

    # Known column name aliases → canonical field
    CSV_COLUMN_ALIASES = {
        "symbol": ["symbol", "tradingsymbol", "trading_symbol", "scrip",
                    "instrument", "stock", "ticker", "instrument_name"],
        "quantity": ["quantity", "qty", "net_qty", "net_quantity",
                     "traded_qty", "filled_qty", "volume"],
        "price": ["price", "average_price", "avg_price", "avgprice",
                   "avg", "trade_price", "fill_price", "rate"],
        "trade_type": ["trade_type", "type", "buy_sell", "side",
                       "transaction_type", "order_side", "action"],
        "ltp": ["ltp", "last_price", "close", "closing_price",
                "market_price", "close_price", "last_traded_price"],
    }

    # Build reverse lookup: normalized alias → canonical field
    _ALIAS_LOOKUP = {}
    for canonical, aliases in CSV_COLUMN_ALIASES.items():
        for alias in aliases:
            _ALIAS_LOOKUP[alias] = canonical

    def _detect_csv_columns(headers):
        """Auto-detect column mapping from CSV headers."""
        mapping = {}
        for i, raw_header in enumerate(headers):
            normalized = raw_header.strip().lower().replace(" ", "_").replace("-", "_")
            canonical = _ALIAS_LOOKUP.get(normalized)
            if canonical and canonical not in mapping:
                mapping[canonical] = i
        return mapping

    def _aggregate_tradebook(rows, mapping):
        """Aggregate tradebook rows into net holdings per symbol."""
        groups = defaultdict(lambda: {"buy_qty": 0.0, "buy_value": 0.0, "sell_qty": 0.0})

        sym_idx = mapping["symbol"]
        qty_idx = mapping["quantity"]
        price_idx = mapping["price"]
        trade_type_idx = mapping.get("trade_type")

        for row in rows:
            if len(row) <= max(sym_idx, qty_idx, price_idx):
                continue
            try:
                symbol = row[sym_idx].strip().upper()
                qty = abs(float(row[qty_idx].strip()))
                price = abs(float(row[price_idx].strip()))
            except (ValueError, IndexError):
                continue

            if not symbol or qty == 0:
                continue

            trade_type = "buy"
            if trade_type_idx is not None and trade_type_idx < len(row):
                trade_type = row[trade_type_idx].strip().lower()

            if trade_type in ("buy", "b", "long"):
                groups[symbol]["buy_qty"] += qty
                groups[symbol]["buy_value"] += qty * price
            else:
                groups[symbol]["sell_qty"] += qty

        holdings = []
        for sym, g_item in groups.items():
            net_qty = g_item["buy_qty"] - g_item["sell_qty"]
            if net_qty > 0:
                avg_price = g_item["buy_value"] / g_item["buy_qty"] if g_item["buy_qty"] > 0 else 0
                holdings.append({
                    "symbol": sym,
                    "qty": int(net_qty),
                    "avgPrice": round(avg_price, 2),
                    "ltp": round(avg_price, 2),
                })
        return holdings

    def _parse_holdings_csv(rows, mapping):
        """Parse a simple holdings-format CSV (not a tradebook)."""
        sym_idx = mapping["symbol"]
        qty_idx = mapping["quantity"]
        price_idx = mapping["price"]
        ltp_idx = mapping.get("ltp")

        holdings = []
        for row in rows:
            if len(row) <= max(sym_idx, qty_idx, price_idx):
                continue
            try:
                symbol = row[sym_idx].strip().upper()
                qty = int(float(row[qty_idx].strip()))
                avg_price = float(row[price_idx].strip())
            except (ValueError, IndexError):
                continue

            if not symbol or qty <= 0:
                continue

            ltp = avg_price
            if ltp_idx is not None and ltp_idx < len(row):
                try:
                    ltp = float(row[ltp_idx].strip())
                except (ValueError, IndexError):
                    pass

            holdings.append({
                "symbol": symbol,
                "qty": qty,
                "avgPrice": round(avg_price, 2),
                "ltp": round(ltp, 2),
            })
        return holdings

    @app.route("/api/import/csv/detect", methods=["POST"])
    @require_auth
    def api_csv_detect():
        """Step 1: Upload CSV, auto-detect columns, return mapping + preview."""
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]
        content = file.read().decode("utf-8-sig")  # handle BOM
        reader = csv.reader(io.StringIO(content))
        all_rows = list(reader)

        if not all_rows:
            return jsonify({"error": "Empty CSV file"}), 400

        # Detect if first row is a header
        first_row = all_rows[0]
        has_header = any(
            cell.strip().lower().replace(" ", "_").replace("-", "_") in _ALIAS_LOOKUP
            for cell in first_row
        )

        if has_header:
            headers = [cell.strip() for cell in first_row]
            data_rows = all_rows[1:]
        else:
            headers = [f"column_{i+1}" for i in range(len(first_row))]
            data_rows = all_rows

        mapping = _detect_csv_columns(headers)

        # Determine format
        is_tradebook = "trade_type" in mapping
        detected_format = "tradebook" if is_tradebook else "holdings"

        # Check required fields
        unmapped = []
        for req in ["symbol", "quantity", "price"]:
            if req not in mapping:
                unmapped.append(req)

        confidence = "high" if not unmapped else "low"

        # Preview: first 5 data rows
        preview = data_rows[:5]

        # If confidence is high, also compute aggregated preview
        aggregated_preview = []
        if confidence == "high":
            if is_tradebook:
                aggregated_preview = _aggregate_tradebook(data_rows, mapping)
            else:
                aggregated_preview = _parse_holdings_csv(data_rows, mapping)

        return jsonify({
            "headers": headers,
            "mapping": mapping,
            "confidence": confidence,
            "detected_format": detected_format,
            "has_header": has_header,
            "total_rows": len(data_rows),
            "preview_rows": preview,
            "aggregated_preview": aggregated_preview[:20],
            "unmapped_required": unmapped,
        })

    @app.route("/api/import/csv", methods=["POST"])
    @require_auth
    def api_import_csv():
        """Step 2: Import CSV with confirmed column mapping."""
        user_id = g.current_user["id"]
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]
        content = file.read().decode("utf-8-sig")
        reader = csv.reader(io.StringIO(content))
        all_rows = list(reader)

        if not all_rows:
            return jsonify({"error": "Empty CSV file"}), 400

        # Get column mapping — either user-provided or auto-detected
        mapping_json = request.form.get("column_mapping")
        if mapping_json:
            mapping = json.loads(mapping_json)
            # Convert string keys to int values if needed
            mapping = {k: int(v) for k, v in mapping.items()}
        else:
            # Auto-detect from headers
            first_row = all_rows[0]
            has_header = any(
                cell.strip().lower().replace(" ", "_").replace("-", "_") in _ALIAS_LOOKUP
                for cell in first_row
            )
            headers = [cell.strip() for cell in first_row] if has_header else []
            mapping = _detect_csv_columns(headers) if has_header else {}

        # Validate required fields
        for req in ["symbol", "quantity", "price"]:
            if req not in mapping:
                return jsonify({"error": f"Column mapping missing required field: {req}"}), 400

        # Determine if first row is header
        has_header_flag = request.form.get("has_header", "true").lower() == "true"
        data_rows = all_rows[1:] if has_header_flag else all_rows

        # Parse based on format
        is_tradebook = "trade_type" in mapping
        if is_tradebook:
            holdings = _aggregate_tradebook(data_rows, mapping)
        else:
            holdings = _parse_holdings_csv(data_rows, mapping)

        if not holdings:
            return jsonify({"error": "No valid holdings found in CSV. Check column mapping."}), 400

        # Append or replace
        mode = request.form.get("mode", "replace")
        if mode == "append":
            existing = get_all_holdings(user_id)
            existing_map = {h["symbol"]: h for h in existing}
            for h in holdings:
                if h["symbol"] in existing_map:
                    old = existing_map[h["symbol"]]
                    total_qty = old["qty"] + h["qty"]
                    if total_qty > 0:
                        old["avgPrice"] = round(
                            (old["avgPrice"] * old["qty"] + h["avgPrice"] * h["qty"]) / total_qty, 2
                        )
                        old["qty"] = total_qty
                        old["ltp"] = h.get("ltp", old.get("ltp", old["avgPrice"]))
                    upsert_holding(old, user_id)
                else:
                    upsert_holding(h, user_id)
        else:
            save_holdings(holdings, user_id)

        return jsonify({
            "imported": len(holdings),
            "mode": mode,
            "format_detected": "tradebook" if is_tradebook else "holdings",
            "summary": _compute_portfolio_summary(user_id),
        })

    @app.route("/api/import/manual", methods=["POST"])
    @require_auth
    def api_import_manual():
        user_id = g.current_user["id"]
        data = request.json or {}
        # Accept both naming conventions
        symbol = data.get("symbol", "")
        qty = data.get("qty") or data.get("quantity")
        avg_price = data.get("avgPrice") or data.get("average_price")

        if not symbol or qty is None or avg_price is None:
            return jsonify({"error": "Missing required fields: symbol, qty/quantity, avgPrice/average_price"}), 400

        ltp_val = data.get("ltp", avg_price)

        holding = {
            "symbol": symbol.upper(),
            "qty": int(float(qty)),
            "avgPrice": float(avg_price),
            "ltp": float(ltp_val),
        }

        # Upsert into DB
        upsert_holding(holding, user_id)

        return jsonify({
            "holding": holding,
            "total_holdings": len(get_all_holdings(user_id)),
        })

    @app.route("/api/holdings/<symbol>", methods=["DELETE"])
    @require_auth
    def api_delete_holding(symbol):
        user_id = g.current_user["id"]
        symbol = symbol.upper()
        db_delete_holding(symbol, user_id)
        remaining = len(get_all_holdings(user_id))
        return jsonify({"deleted": symbol, "remaining": remaining})

    # ─── PORTFOLIO SNAPSHOTS ─────────────────────────────────────

    @app.route("/api/portfolios", methods=["GET"])
    @require_auth
    def api_get_portfolios():
        user_id = g.current_user["id"]
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY created_at DESC"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/portfolios", methods=["POST"])
    @require_auth
    def api_save_portfolio():
        user_id = g.current_user["id"]
        data = request.json or {}
        name = data.get("name", f"Snapshot {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        snapshot_id = generate_id()
        summary = _compute_portfolio_summary(user_id)

        conn = get_db()
        conn.execute(
            "INSERT INTO portfolio_snapshots (id, name, holdings, cash_balance, total_value) VALUES (?, ?, ?, ?, ?)",
            (snapshot_id, name, json.dumps(get_all_holdings(user_id)),
             get_cash_balance(user_id), summary["portfolio_value"])
        )
        conn.commit()
        conn.close()
        return jsonify({"id": snapshot_id, "name": name})

    @app.route("/api/portfolios/<pid>", methods=["DELETE"])
    @require_auth
    def api_delete_portfolio(pid):
        conn = get_db()
        conn.execute("DELETE FROM portfolio_snapshots WHERE id = ?", (pid,))
        conn.commit()
        conn.close()
        return jsonify({"deleted": pid})

    @app.route("/api/portfolios/<pid>/load", methods=["POST"])
    @require_auth
    def api_load_portfolio(pid):
        user_id = g.current_user["id"]
        conn = get_db()
        row = conn.execute("SELECT * FROM portfolio_snapshots WHERE id = ?", (pid,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Snapshot not found"}), 404

        loaded_holdings = json.loads(row["holdings"])
        loaded_cash = row["cash_balance"] or 0
        save_holdings(loaded_holdings, user_id)
        save_cash_balance(loaded_cash, user_id)
        return jsonify({
            "loaded": row["name"],
            "holdings_count": len(loaded_holdings),
            "cash_balance": loaded_cash,
        })

    # ─── COLLATERAL ──────────────────────────────────────────────

    @app.route("/api/collateral", methods=["GET"])
    @require_auth
    def api_collateral():
        user_id = g.current_user["id"]
        return jsonify(_compute_portfolio_summary(user_id))

    # ─── NOTIFICATIONS ───────────────────────────────────────────

    @app.route("/api/notifications", methods=["GET"])
    @require_auth
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
    @require_auth
    def api_unread_count():
        conn = get_db()
        count = conn.execute(
            "SELECT COUNT(*) as c FROM notifications WHERE read = 0"
        ).fetchone()["c"]
        conn.close()
        return jsonify({"unread_count": count})

    @app.route("/api/notifications/<nid>/read", methods=["POST"])
    @require_auth
    def api_mark_read(nid):
        conn = get_db()
        conn.execute("UPDATE notifications SET read = 1 WHERE id = ?", (nid,))
        conn.commit()
        conn.close()
        return jsonify({"marked": nid})

    @app.route("/api/notifications/read-all", methods=["POST"])
    @require_auth
    def api_mark_all_read():
        conn = get_db()
        conn.execute("UPDATE notifications SET read = 1 WHERE read = 0")
        conn.commit()
        conn.close()
        return jsonify({"status": "all_read"})

    @app.route("/api/notifications/<nid>", methods=["DELETE"])
    @require_auth
    def api_delete_notification(nid):
        conn = get_db()
        conn.execute("DELETE FROM notifications WHERE id = ?", (nid,))
        conn.commit()
        conn.close()
        return jsonify({"deleted": nid})

    # ─── DAILY SUMMARY ───────────────────────────────────────────

    @app.route("/api/daily-summary", methods=["GET"])
    @require_auth
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
    @require_auth
    def api_summary_by_date(date):
        conn = get_db()
        row = conn.execute("SELECT * FROM daily_summary WHERE date = ?", (date,)).fetchone()
        conn.close()
        if row:
            return jsonify(dict(row))
        return jsonify({"error": "No summary for this date"}), 404

    # ─── TRADES (read-only for Phase 1) ──────────────────────────

    @app.route("/api/trades", methods=["GET"])
    @require_auth
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
    @require_auth
    def api_trade_detail(tid):
        conn = get_db()
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (tid,)).fetchone()
        conn.close()
        if row:
            return jsonify(dict(row))
        return jsonify({"error": "Trade not found"}), 404

    # ─── POSITIONS (read-only for Phase 1) ───────────────────────

    @app.route("/api/positions", methods=["GET"])
    @require_auth
    def api_positions():
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM positions WHERE status = 'ACTIVE' ORDER BY expiry_date ASC"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    # ─── ANALYTICS (basic for Phase 1) ───────────────────────────

    @app.route("/api/analytics/summary", methods=["GET"])
    @require_auth
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
    @require_auth
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
    @require_auth
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
    @require_auth
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
    @require_auth
    def api_fees_estimate():
        action = request.args.get("action", "SELL")
        premium = float(request.args.get("premium", 0))
        quantity = int(request.args.get("quantity", 0))
        fees = calculate_fees(action, premium, quantity)
        return jsonify(fees)

    @app.route("/api/fees/summary", methods=["GET"])
    @require_auth
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
    @require_auth
    def api_audit_orders():
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM order_audit ORDER BY timestamp DESC LIMIT 100"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    # ─── GTT ─────────────────────────────────────────────────────

    @app.route("/api/gtt/active", methods=["GET"])
    @require_auth
    def api_gtt_active():
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM gtt_orders WHERE status = 'ACTIVE' ORDER BY created_at DESC"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    # ─── KITE AUTH ────────────────────────────────────────────────

    @app.route("/api/kite/credentials", methods=["POST"])
    @require_auth
    def api_kite_save_credentials():
        """Save user's Kite API key, secret, and permission level."""
        user_id = g.current_user["id"]
        data = request.json or {}
        api_key = (data.get("api_key") or "").strip()
        api_secret = (data.get("api_secret") or "").strip()
        permission = data.get("permission", "readonly").strip().lower()
        if not api_key or not api_secret:
            return jsonify({"error": "API key and secret are required"}), 400
        if permission not in ("readonly", "readwrite"):
            permission = "readonly"
        from models import save_user_kite_credentials
        save_user_kite_credentials(user_id, api_key, api_secret, permission)
        return jsonify({"status": "saved", "permission": permission,
                        "message": f"Kite credentials saved ({permission} mode)"})

    @app.route("/api/kite/login", methods=["GET"])
    @require_auth
    def api_kite_login():
        user_id = g.current_user["id"]
        login_url_val = get_login_url(user_id)
        kite = get_kite_for_user(user_id)
        authenticated = kite.is_authenticated() if kite else False
        from models import get_user_kite_credentials
        creds = get_user_kite_credentials(user_id)
        return jsonify({
            "login_url": login_url_val,
            "authenticated": authenticated,
            "simulation_mode": not authenticated,
            "kite_configured": bool(creds),
            "message": "Enter your Kite API key and secret in Settings first." if not creds else None,
        })

    @app.route("/api/kite/connect", methods=["POST"])
    @require_auth
    def api_kite_connect():
        """Exchange request_token for access_token and store on user."""
        user_id = g.current_user["id"]
        data = request.json or {}
        request_token = data.get("request_token")
        if not request_token:
            return jsonify({"error": "request_token is required"}), 400
        try:
            result = exchange_request_token(request_token, user_id)
            update_user_kite_token(user_id, result["access_token"],
                                   date.today().isoformat(), result["user_id"])

            # Auto-import holdings on connect
            imported_count = 0
            try:
                kite = get_kite_for_user(user_id)
                if kite and kite.is_authenticated():
                    live_holdings = kite.get_holdings()
                    if live_holdings:
                        parsed = []
                        for h in live_holdings:
                            symbol = h.get("tradingsymbol", h.get("symbol", ""))
                            qty = h.get("quantity", 0)
                            avg = h.get("average_price", 0)
                            ltp = h.get("last_price", avg)
                            if symbol and qty > 0:
                                parsed.append({"symbol": symbol, "qty": qty, "avgPrice": round(avg, 2), "ltp": round(ltp, 2)})
                        if parsed:
                            save_holdings(parsed, user_id)
                            imported_count = len(parsed)
            except Exception:
                pass  # Don't fail the connect if import fails

            return jsonify({"status": "connected", "kite_user_id": result["user_id"],
                           "holdings_imported": imported_count,
                           "message": f"Kite connected. {imported_count} holdings imported." if imported_count else "Kite connected successfully."})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/kite/status", methods=["GET"])
    @require_auth
    def api_kite_status():
        """Return user's Kite connection status."""
        user_id = g.current_user["id"]
        kite = get_kite_for_user(user_id)
        authenticated = kite.is_authenticated() if kite else False
        from models import get_user_kite_credentials, get_user_kite_permission
        creds = get_user_kite_credentials(user_id)
        token_data = get_user_kite_token(user_id)
        permission = get_user_kite_permission(user_id)
        return jsonify({
            "connected": authenticated,
            "simulation_mode": not authenticated,
            "kite_configured": bool(creds),
            "kite_user_id": token_data.get("kite_user_id") if token_data else None,
            "has_api_key": bool(creds),
            "permission": permission,
        })

    @app.route("/api/kite/disconnect", methods=["POST"])
    @require_auth
    def api_kite_disconnect():
        """Clear user's Kite session token (keeps API key/secret)."""
        user_id = g.current_user["id"]
        clear_user_kite_token(user_id)
        return jsonify({"status": "disconnected", "message": "Kite disconnected"})

    @app.route("/api/callback", methods=["GET"])
    def api_kite_callback():
        """Redirect to frontend with request_token (browser redirect from Zerodha)."""
        request_token = request.args.get("request_token")
        if not request_token:
            return jsonify({"error": "No request_token provided"}), 400
        return redirect(f"/kite/callback?request_token={request_token}")

    @app.route("/api/import/kite", methods=["POST"])
    @require_auth
    def api_import_kite():
        user_id = g.current_user["id"]
        kite = get_kite_for_user(user_id)
        if not kite or not kite.is_authenticated():
            return jsonify({"error": "Kite not connected"}), 401
        try:
            holdings = kite.get_holdings()
            save_holdings(holdings, user_id)
            return jsonify({"imported": len(holdings), "summary": _compute_portfolio_summary(user_id)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─── SCAN / RECOMMENDATIONS ──────────────────────────────────

    @app.route("/api/scan", methods=["POST"])
    @require_auth
    def api_scan():
        user_id = g.current_user["id"]
        data = request.json or {}
        cash = data.get("cash_balance", get_cash_balance(user_id))
        save_cash_balance(cash, user_id)

        settings = get_all_settings(user_id)
        kite = get_kite_for_user(user_id)
        recs = scan_strategies(get_all_holdings(user_id), cash, settings, kite_service=kite)
        _get_user_state(user_id)["recommendations"] = recs
        _get_user_state(user_id)["last_scan"] = now_iso()

        kite_authenticated = kite.is_authenticated() if kite else False
        arbs = scan_arbitrage(None, simulation=not kite_authenticated)
        _get_user_state(user_id)["arbitrage_opportunities"] = arbs

        _create_notification("SCAN_COMPLETE", "Scan complete",
            f"Found {len(recs)} strategy opportunities and {len(arbs)} arbitrage opportunities.",
            "INFO", "/scanner")

        # Split covered calls into separate section
        covered_calls = [r for r in recs if r.get("source") == "covered_call_from_holdings"]
        regular_recs = [r for r in recs if r.get("source") != "covered_call_from_holdings"]

        total_margin = sum(r.get("margin_needed", 0) for r in recs)
        total_net_premium = sum(r.get("net_premium", r.get("premium_income", 0)) for r in recs if r.get("net_premium", r.get("premium_income", 0)) > 0)

        # VIX from first rec (all have same vix_at_scan)
        vix_data = recs[0].get("vix_signal", {}) if recs else {}

        # Portfolio risk summary
        import portfolio_risk as pr
        margin_data = pr.get_available_margin(kite)
        port_delta = pr.get_portfolio_delta(kite)
        risk_summary = pr.get_portfolio_risk_summary(recs, margin_data.get("available", 0), port_delta)

        return jsonify({
            "recommendations": regular_recs,
            "covered_calls": covered_calls,
            "arbitrage": arbs,
            "scanned_at": _get_user_state(user_id)["last_scan"],
            "total_margin_required": round(total_margin, 2),
            "total_weekly_income": round(total_net_premium, 2),
            "vix": vix_data,
            "portfolio_risk": risk_summary,
        })

    @app.route("/api/recommendations", methods=["GET", "POST"])
    @require_auth
    def api_recommendations():
        user_id = g.current_user["id"]
        # Support both GET params and POST JSON body for filters
        if request.method == "POST":
            data = request.json or {}
            safety = data.get("safety")
            strategy = data.get("strategy") or data.get("type")
        else:
            safety = request.args.get("safety")
            strategy = request.args.get("type")

        recs = _get_user_state(user_id)["recommendations"]
        if safety and safety != "ALL":
            recs = [r for r in recs if r.get("safety_tag") == safety or r.get("safety") == safety]
        if strategy and strategy != "ALL":
            recs = [r for r in recs if r.get("strategy_type") == strategy or r.get("strategy") == strategy]

        return jsonify({"recommendations": recs})

    @app.route("/api/arbitrage", methods=["GET"])
    @require_auth
    def api_arbitrage():
        user_id = g.current_user["id"]
        return jsonify(_get_user_state(user_id)["arbitrage_opportunities"])

    # ─── EXECUTE ─────────────────────────────────────────────────

    @app.route("/api/execute", methods=["POST"])
    @require_auth
    def api_execute():
        user_id = g.current_user["id"]
        from models import get_user_kite_permission
        if get_user_kite_permission(user_id) != "readwrite":
            return jsonify({"error": "Trading disabled. Enable 'Read & Trade' mode in Settings → Broker Connection."}), 403
        if _get_user_state(user_id)["permission"] != "EXECUTE":
            return jsonify({"error": "Permission denied. Enable EXECUTE mode first."}), 403

        data = request.json or {}
        rec_id = data.get("rec_id")
        confirm_execution = data.get("confirm_execution", False)
        acknowledge_risk = data.get("acknowledge_risk", False)

        if not confirm_execution or not acknowledge_risk:
            return jsonify({"error": "Must confirm execution and acknowledge risk"}), 400

        # Find recommendation
        rec = next((r for r in _get_user_state(user_id)["recommendations"] if r.get("id") == rec_id), None)
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
        validation = validate_order(order_legs, _get_user_state(user_id))
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
            kite = get_kite_for_user(user_id)
            kite_authenticated = kite.is_authenticated() if kite else False

            if kite_authenticated:
                # Live execution via Kite
                kite_order_ids = []
                for leg in order_legs:
                    result = kite.place_order(**leg)
                    if not result.get("success"):
                        return jsonify({"error": f"Order failed: {result.get('error', 'unknown')}"}), 500
                    kite_order_ids.append(result["order_id"])

                    # Post-order reconciliation
                    recon = reconcile_order(leg, result["order_id"], kite)
                    if recon["alert"]:
                        _get_user_state(user_id)["permission"] = "READONLY"
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
                "simulation": not kite_authenticated,
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
    @require_auth
    def api_close_position(pid):
        user_id = g.current_user["id"]
        from models import get_user_kite_permission
        if get_user_kite_permission(user_id) != "readwrite":
            return jsonify({"error": "Trading disabled. Enable 'Read & Trade' mode in Settings."}), 403
        if _get_user_state(user_id)["permission"] != "EXECUTE":
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
    @require_auth
    def api_roll_position(pid):
        user_id = g.current_user["id"]
        from models import get_user_kite_permission
        if get_user_kite_permission(user_id) != "readwrite":
            return jsonify({"error": "Trading disabled. Enable 'Read & Trade' mode in Settings."}), 403
        if _get_user_state(user_id)["permission"] != "EXECUTE":
            return jsonify({"error": "Permission denied. Enable EXECUTE mode first."}), 403

        data = request.json or {}
        # Close current position and open new one at next expiry
        try:
            close_result = close_position(pid, data.get("exit_premium", 0), "ROLLED")
            return jsonify({"status": "rolled", "closed": close_result})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/positions/<pid>/adjustments", methods=["GET"])
    @require_auth
    def api_position_adjustments(pid):
        try:
            adjustments = compute_adjustments(pid)
            return jsonify(adjustments)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/positions/<pid>/adjust", methods=["POST"])
    @require_auth
    def api_execute_adjustment(pid):
        user_id = g.current_user["id"]
        from models import get_user_kite_permission
        if get_user_kite_permission(user_id) != "readwrite":
            return jsonify({"error": "Trading disabled. Enable 'Read & Trade' mode in Settings."}), 403
        if _get_user_state(user_id)["permission"] != "EXECUTE":
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
    @require_auth
    def api_risk_status():
        try:
            status = get_risk_status()
            return jsonify(status)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/risk/alerts", methods=["GET"])
    @require_auth
    def api_risk_alerts():
        try:
            alerts = get_risk_alerts()
            return jsonify(alerts)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings/risk-profile", methods=["GET"])
    @require_auth
    def api_get_risk_profile():
        user_id = g.current_user["id"]
        profile = get_setting("risk_profile", user_id) or "moderate"
        from strike_selector import RISK_PROFILES
        profile_data = RISK_PROFILES.get(profile.capitalize(), RISK_PROFILES["Moderate"])
        return jsonify({"profile": profile, "details": profile_data})

    @app.route("/api/settings/risk-profile", methods=["POST"])
    @require_auth
    def api_set_risk_profile():
        user_id = g.current_user["id"]
        data = request.json or {}
        profile = data.get("profile", "moderate").lower()
        if profile not in ("conservative", "moderate", "aggressive"):
            return jsonify({"error": "Invalid profile. Use conservative, moderate, or aggressive."}), 400
        set_setting("risk_profile", profile, user_id)
        return jsonify({"profile": profile})

    @app.route("/api/settings/circuit-breaker", methods=["POST"])
    @require_auth
    def api_circuit_breaker():
        user_id = g.current_user["id"]
        data = request.json or {}
        enabled = data.get("enabled", False)
        set_setting("circuit_breaker_enabled", str(enabled).lower(), user_id)
        return jsonify({"circuit_breaker_enabled": enabled})

    # ─── GTT DELETE ──────────────────────────────────────────────

    @app.route("/api/gtt/<gtt_id>", methods=["DELETE"])
    @require_auth
    def api_delete_gtt(gtt_id):
        user_id = g.current_user["id"]
        if _get_user_state(user_id)["permission"] != "EXECUTE":
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

def _compute_portfolio_summary(user_id):
    """Compute collateral and portfolio summary from holdings."""
    holdings = get_all_holdings(user_id)
    cash = get_cash_balance(user_id)

    # Fetch live prices for all symbols
    symbols = [h["symbol"] for h in holdings]
    live_prices = {}
    if symbols:
        live_prices = live_price_service.fetch_spot_prices_batch(symbols)

    total_value = 0
    non_cash_collateral = 0
    detailed = []

    for h in holdings:
        symbol = h["symbol"]
        qty = h["qty"]
        avg = h["avgPrice"]

        # Use live price if available, else stored LTP, else avgPrice
        live = live_prices.get(symbol)
        if live:
            ltp = live["ltp"]
            price_source = "yahoo"
        else:
            ltp = h.get("ltp", avg)
            price_source = "simulated"
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
            # Canonical names (backend)
            "ltp": ltp,
            "value": round(value, 2),
            "pnl": round(pnl, 2),
            "haircut": round(haircut * 100, 1),  # as percentage for display
            "collateral_value": round(collateral, 2),
            "lot_size": lot_size,
            "status": status,
            # Frontend-compatible aliases
            "quantity": qty,
            "average_price": avg,
            "price_source": price_source,
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


def _warm_price_cache():
    """Pre-fetch live prices for all users' holdings in background thread."""
    import threading
    # Collect symbols from all users
    try:
        conn = get_db()
        rows = conn.execute("SELECT DISTINCT symbol FROM holdings").fetchall()
        conn.close()
        symbols = [r["symbol"] for r in rows]
    except Exception:
        symbols = []

    if not symbols:
        return
    # Also warm index prices
    symbols.extend(["NIFTY", "BANKNIFTY"])

    def _fetch():
        try:
            live_price_service.fetch_spot_prices_batch(symbols)
        except Exception:
            pass
    threading.Thread(target=_fetch, daemon=True).start()


# ─── APP ENTRY POINT ─────────────────────────────────────────────

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
