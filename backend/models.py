"""
SQLite database models and initialization for Yield Engine.
All tables created via raw SQL — no ORM.
DB file at data/yield_engine.db
"""

import os
import sqlite3
import uuid
from datetime import datetime

DB_PATH = os.getenv("SQLITE_DB_PATH", os.path.join(os.path.dirname(__file__), "data", "yield_engine.db"))

# Safety hard caps — NOT configurable via UI, only via env vars
SAFETY_HARD_CAPS = {
    "MAX_LOTS_PER_ORDER_NIFTY": int(os.getenv("MAX_LOTS_NIFTY", "2")),
    "MAX_LOTS_PER_ORDER_BANKNIFTY": int(os.getenv("MAX_LOTS_BANKNIFTY", "1")),
    "MAX_LOTS_PER_ORDER_STOCK": int(os.getenv("MAX_LOTS_STOCK", "2")),
    "MAX_ORDER_VALUE": int(os.getenv("MAX_ORDER_VALUE", "500000")),
    "MAX_ORDERS_PER_DAY": int(os.getenv("MAX_ORDERS_PER_DAY", "20")),
    "MAX_OPEN_POSITIONS": int(os.getenv("MAX_OPEN_POSITIONS", "10")),
    "PRICE_DEVIATION_LIMIT": float(os.getenv("PRICE_DEVIATION_LIMIT", "0.20")),
    "ALLOWED_EXCHANGES": ["NFO", "NSE"],
    "ALLOWED_PRODUCTS": ["NRML", "CNC"],
    "PASSWORD_RETENTION_MINUTES": 0,
}

# Simulation stock data (used when Kite is not connected)
SIMULATION_STOCKS = {
    "RELIANCE": {"ltp": 2520, "lotSize": 250, "haircut": 0.125, "iv": 0.22},
    "TCS": {"ltp": 3050, "lotSize": 175, "haircut": 0.125, "iv": 0.26},
    "HDFCBANK": {"ltp": 1620, "lotSize": 550, "haircut": 0.125, "iv": 0.18},
    "INFY": {"ltp": 1390, "lotSize": 400, "haircut": 0.125, "iv": 0.28},
    "BEL": {"ltp": 338, "lotSize": 1500, "haircut": 0.18, "iv": 0.35},
    "SBIN": {"ltp": 755, "lotSize": 1500, "haircut": 0.15, "iv": 0.24},
    "HAL": {"ltp": 4150, "lotSize": 150, "haircut": 0.20, "iv": 0.38},
    "ICICIBANK": {"ltp": 1095, "lotSize": 700, "haircut": 0.125, "iv": 0.19},
}

SIMULATION_INDICES = {
    "NIFTY": {"spot": 23150, "lotSize": 25, "iv": 0.145, "support": 22800, "resistance": 23500},
    "BANKNIFTY": {"spot": 48900, "lotSize": 15, "iv": 0.162, "support": 47800, "resistance": 50000},
}

DEFAULT_SETTINGS = {
    "max_loss_per_trade": "10000",
    "min_prob_otm": "0.75",
    "max_margin_util": "0.6",
    "preferred_dte": "7",
    "notify_scan_complete": "true",
    "notify_expiry_reminder": "true",
    "notify_token_expired": "true",
    "notify_margin_warning": "true",
    "notify_daily_summary": "true",
    "notify_pnl_threshold": "5000",
    "kite_auto_login": "false",
    "kite_user_id": "",
    "kite_totp_secret": "",
    "risk_profile": "moderate",
    "strike_selection_mode": "auto",
    "manual_min_otm_pct": "2",
    "manual_max_otm_pct": "10",
    "manual_target_delta_puts": "0.20",
    "manual_target_delta_calls": "0.15",
    "skip_if_iv_rank_above": "80",
    "skip_before_events": "true",
    "stop_loss_multiplier": "2.0",
    "delta_alert_threshold": "0.50",
    "daily_loss_limit": "25000",
    "circuit_breaker_enabled": "false",
    "auto_stop_loss_enabled": "false",
    "auto_gtt_on_entry": "true",
    "intraday_drop_alert_pct": "1.5",
    "close_itm_before_expiry": "true",
    "allowed_strategies": "COVERED_CALL,CASH_SECURED_PUT,PUT_CREDIT_SPREAD,COLLAR,CASH_FUTURES_ARB",
}


def get_db():
    """Get a database connection with row factory."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # Use DELETE journal mode for Azure File Share SMB compatibility (WAL needs shared memory)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def generate_id():
    return str(uuid.uuid4())


def now_iso():
    return datetime.utcnow().isoformat()


def init_db():
    """Create all tables and insert default settings."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id TEXT PRIMARY KEY,
            rec_id TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            legs TEXT NOT NULL,
            entry_premium REAL NOT NULL,
            entry_time TEXT NOT NULL,
            exit_premium REAL,
            exit_time TEXT,
            exit_reason TEXT,
            pnl REAL,
            fees REAL DEFAULT 0,
            margin_used REAL DEFAULT 0,
            status TEXT DEFAULT 'OPEN',
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS positions (
            id TEXT PRIMARY KEY,
            trade_id TEXT REFERENCES trades(id),
            symbol TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            legs TEXT NOT NULL,
            entry_premium REAL NOT NULL,
            current_premium REAL,
            unrealized_pnl REAL,
            days_held INTEGER DEFAULT 0,
            expiry_date TEXT,
            margin_blocked REAL DEFAULT 0,
            last_updated TEXT,
            status TEXT DEFAULT 'ACTIVE'
        );

        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            holdings TEXT NOT NULL,
            cash_balance REAL DEFAULT 0,
            total_value REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            severity TEXT DEFAULT 'INFO',
            read INTEGER DEFAULT 0,
            action_url TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS daily_summary (
            date TEXT PRIMARY KEY,
            open_positions INTEGER DEFAULT 0,
            trades_executed INTEGER DEFAULT 0,
            premium_collected REAL DEFAULT 0,
            premium_paid REAL DEFAULT 0,
            realized_pnl REAL DEFAULT 0,
            unrealized_pnl REAL DEFAULT 0,
            margin_used REAL DEFAULT 0,
            collateral_value REAL DEFAULT 0,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS order_audit (
            id TEXT PRIMARY KEY,
            timestamp TEXT DEFAULT (datetime('now')),
            action TEXT NOT NULL,
            rec_id TEXT,
            trade_id TEXT,
            legs TEXT NOT NULL,
            dry_run_result TEXT NOT NULL,
            kite_response TEXT,
            reconciliation TEXT,
            user_confirmed INTEGER DEFAULT 0,
            status TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS gtt_orders (
            id TEXT PRIMARY KEY,
            trade_id TEXT REFERENCES trades(id),
            kite_gtt_id INTEGER,
            symbol TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            trigger_price REAL NOT NULL,
            order_type TEXT NOT NULL,
            limit_price REAL,
            quantity INTEGER NOT NULL,
            exchange TEXT NOT NULL,
            status TEXT DEFAULT 'ACTIVE',
            created_at TEXT DEFAULT (datetime('now')),
            triggered_at TEXT
        );

        CREATE TABLE IF NOT EXISTS adjustments (
            id TEXT PRIMARY KEY,
            trade_id TEXT REFERENCES trades(id),
            adjustment_type TEXT NOT NULL,
            old_legs TEXT NOT NULL,
            new_legs TEXT,
            cost REAL NOT NULL,
            reason TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS holdings (
            symbol TEXT PRIMARY KEY,
            qty INTEGER NOT NULL,
            avg_price REAL NOT NULL,
            ltp REAL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)

    # Insert default settings if not present
    for key, value in DEFAULT_SETTINGS.items():
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )

    conn.commit()
    conn.close()


def get_setting(key):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def set_setting(key, value):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
        (key, str(value), now_iso())
    )
    conn.commit()
    conn.close()


def get_all_settings():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}


# ─── Holdings persistence ────────────────────────────────────────────────────

def get_all_holdings():
    """Load all holdings from the database."""
    conn = get_db()
    rows = conn.execute("SELECT symbol, qty, avg_price, ltp FROM holdings ORDER BY symbol").fetchall()
    conn.close()
    return [{"symbol": r["symbol"], "qty": r["qty"], "avgPrice": r["avg_price"],
             "ltp": r["ltp"] or r["avg_price"]} for r in rows]


def save_holdings(holdings):
    """Replace all holdings in the database."""
    conn = get_db()
    conn.execute("DELETE FROM holdings")
    for h in holdings:
        conn.execute(
            "INSERT INTO holdings (symbol, qty, avg_price, ltp, updated_at) VALUES (?, ?, ?, ?, ?)",
            (h["symbol"], h["qty"], h["avgPrice"], h.get("ltp", h["avgPrice"]), now_iso())
        )
    conn.commit()
    conn.close()


def upsert_holding(holding):
    """Insert or update a single holding."""
    conn = get_db()
    conn.execute(
        "INSERT INTO holdings (symbol, qty, avg_price, ltp, updated_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(symbol) DO UPDATE SET qty=?, avg_price=?, ltp=?, updated_at=?",
        (holding["symbol"], holding["qty"], holding["avgPrice"],
         holding.get("ltp", holding["avgPrice"]), now_iso(),
         holding["qty"], holding["avgPrice"],
         holding.get("ltp", holding["avgPrice"]), now_iso())
    )
    conn.commit()
    conn.close()


def delete_holding(symbol):
    """Delete a holding by symbol."""
    conn = get_db()
    conn.execute("DELETE FROM holdings WHERE symbol = ?", (symbol,))
    conn.commit()
    conn.close()


def get_cash_balance():
    """Get persisted cash balance from settings."""
    val = get_setting("cash_balance")
    try:
        return float(val) if val else 0
    except (ValueError, TypeError):
        return 0


def save_cash_balance(amount):
    """Persist cash balance to settings."""
    set_setting("cash_balance", str(amount))
