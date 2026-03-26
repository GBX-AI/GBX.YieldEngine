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


import threading as _threading

_db_lock = _threading.Lock()
_db_conn = None


def get_db():
    """
    Get a shared database connection. Uses a single connection per process
    with serialized access via lock — required for Azure File Share (SMB)
    which doesn't support POSIX file locking properly.
    """
    global _db_conn
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    if _db_conn is None:
        _db_conn = _SharedConnection(DB_PATH)

    return _db_conn


class _SharedConnection:
    """
    Wrapper around sqlite3.Connection that makes close() a no-op.
    Required because the codebase calls conn.close() everywhere,
    but we need a single shared connection for Azure File Share (SMB).
    """
    def __init__(self, db_path):
        self._conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=DELETE")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def close(self):
        pass  # no-op — keep connection alive

    def __getattr__(self, name):
        return getattr(self._conn, name)


def generate_id():
    return str(uuid.uuid4())


def now_iso():
    return datetime.utcnow().isoformat()


_CREATE_TABLE_STMTS = [
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        kite_api_key TEXT, kite_api_secret TEXT,
        kite_access_token TEXT, kite_token_date TEXT, kite_user_id TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS trades (
        id TEXT PRIMARY KEY, rec_id TEXT NOT NULL, strategy_type TEXT NOT NULL,
        symbol TEXT NOT NULL, direction TEXT NOT NULL, legs TEXT NOT NULL,
        entry_premium REAL NOT NULL, entry_time TEXT NOT NULL,
        exit_premium REAL, exit_time TEXT, exit_reason TEXT, pnl REAL,
        fees REAL DEFAULT 0, margin_used REAL DEFAULT 0,
        status TEXT DEFAULT 'OPEN', notes TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS positions (
        id TEXT PRIMARY KEY, trade_id TEXT REFERENCES trades(id),
        symbol TEXT NOT NULL, strategy_type TEXT NOT NULL, legs TEXT NOT NULL,
        entry_premium REAL NOT NULL, current_premium REAL, unrealized_pnl REAL,
        days_held INTEGER DEFAULT 0, expiry_date TEXT, margin_blocked REAL DEFAULT 0,
        last_updated TEXT, status TEXT DEFAULT 'ACTIVE'
    )""",
    """CREATE TABLE IF NOT EXISTS portfolio_snapshots (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, holdings TEXT NOT NULL,
        cash_balance REAL DEFAULT 0, total_value REAL,
        created_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS notifications (
        id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL,
        message TEXT NOT NULL, severity TEXT DEFAULT 'INFO',
        read INTEGER DEFAULT 0, action_url TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS daily_summary (
        date TEXT PRIMARY KEY, open_positions INTEGER DEFAULT 0,
        trades_executed INTEGER DEFAULT 0, premium_collected REAL DEFAULT 0,
        premium_paid REAL DEFAULT 0, realized_pnl REAL DEFAULT 0,
        unrealized_pnl REAL DEFAULT 0, margin_used REAL DEFAULT 0,
        collateral_value REAL DEFAULT 0, notes TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL,
        updated_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS order_audit (
        id TEXT PRIMARY KEY, timestamp TEXT DEFAULT (datetime('now')),
        action TEXT NOT NULL, rec_id TEXT, trade_id TEXT,
        legs TEXT NOT NULL, dry_run_result TEXT NOT NULL,
        kite_response TEXT, reconciliation TEXT,
        user_confirmed INTEGER DEFAULT 0, status TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS gtt_orders (
        id TEXT PRIMARY KEY, trade_id TEXT REFERENCES trades(id),
        kite_gtt_id INTEGER, symbol TEXT NOT NULL, trigger_type TEXT NOT NULL,
        trigger_price REAL NOT NULL, order_type TEXT NOT NULL,
        limit_price REAL, quantity INTEGER NOT NULL, exchange TEXT NOT NULL,
        status TEXT DEFAULT 'ACTIVE',
        created_at TEXT DEFAULT (datetime('now')), triggered_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS adjustments (
        id TEXT PRIMARY KEY, trade_id TEXT REFERENCES trades(id),
        adjustment_type TEXT NOT NULL, old_legs TEXT NOT NULL,
        new_legs TEXT, cost REAL NOT NULL, reason TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS holdings (
        symbol TEXT PRIMARY KEY, qty INTEGER NOT NULL,
        avg_price REAL NOT NULL, ltp REAL,
        updated_at TEXT DEFAULT (datetime('now'))
    )""",
]


def init_db():
    """Create all tables, run migrations, insert default settings."""
    conn = get_db()
    for stmt in _CREATE_TABLE_STMTS:
        conn.execute(stmt)

    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )

    conn.commit()
    _migrate_add_user_id()


def _migrate_add_user_id():
    """Add user_id column to all data tables (idempotent)."""
    conn = get_db()
    tables_needing_user_id = [
        "trades", "positions", "portfolio_snapshots", "notifications",
        "daily_summary", "order_audit", "gtt_orders", "adjustments",
    ]
    for table in tables_needing_user_id:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT")
        except Exception:
            pass  # Column already exists

    # Settings table: add user_id
    try:
        conn.execute("ALTER TABLE settings ADD COLUMN user_id TEXT")
    except Exception:
        pass

    # Users table: add kite_api_key, kite_api_secret (for per-user Kite apps)
    for col in ("kite_api_key", "kite_api_secret"):
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
        except Exception:
            pass

    # Holdings table: need composite PK (user_id, symbol) — recreate
    try:
        conn.execute("ALTER TABLE holdings ADD COLUMN user_id TEXT")
        # Recreate with composite PK
        conn.execute("""CREATE TABLE IF NOT EXISTS holdings_v2 (
            user_id TEXT NOT NULL, symbol TEXT NOT NULL,
            qty INTEGER NOT NULL, avg_price REAL NOT NULL, ltp REAL,
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, symbol)
        )""")
        conn.execute("""INSERT OR IGNORE INTO holdings_v2 (user_id, symbol, qty, avg_price, ltp, updated_at)
            SELECT COALESCE(user_id, ''), symbol, qty, avg_price, ltp, updated_at FROM holdings""")
        conn.execute("DROP TABLE holdings")
        conn.execute("ALTER TABLE holdings_v2 RENAME TO holdings")
    except Exception:
        pass  # Already migrated

    conn.commit()


def migrate_orphaned_data(user_id):
    """Assign all data with empty/NULL user_id to the given user. Called on first signup."""
    conn = get_db()
    tables = ["holdings", "trades", "positions", "portfolio_snapshots", "notifications",
              "daily_summary", "order_audit", "gtt_orders", "adjustments", "settings"]
    for table in tables:
        try:
            conn.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL OR user_id = ''", (user_id,))
        except Exception:
            pass
    conn.commit()


# ─── User functions ──────────────────────────────────────────────────────────

def create_user(email, name, password_hash):
    """Create a new user and return the user dict."""
    conn = get_db()
    user_id = generate_id()
    conn.execute(
        "INSERT INTO users (id, email, name, password_hash) VALUES (?, ?, ?, ?)",
        (user_id, email.lower().strip(), name.strip(), password_hash)
    )
    conn.commit()

    # If this is the first user, migrate orphaned data
    count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    if count == 1:
        migrate_orphaned_data(user_id)

    return {"id": user_id, "email": email.lower().strip(), "name": name.strip()}


def get_user_by_email(email):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def save_user_kite_credentials(user_id, api_key, api_secret):
    """Store user's Kite API key and secret."""
    conn = get_db()
    conn.execute(
        "UPDATE users SET kite_api_key = ?, kite_api_secret = ? WHERE id = ?",
        (api_key, api_secret, user_id)
    )
    conn.commit()


def get_user_kite_credentials(user_id):
    """Return user's Kite API key and secret, or None."""
    conn = get_db()
    row = conn.execute(
        "SELECT kite_api_key, kite_api_secret FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()
    if row and row["kite_api_key"]:
        return {"kite_api_key": row["kite_api_key"], "kite_api_secret": row["kite_api_secret"]}
    return None


def update_user_kite_token(user_id, access_token, token_date, kite_user_id):
    conn = get_db()
    conn.execute(
        "UPDATE users SET kite_access_token = ?, kite_token_date = ?, kite_user_id = ? WHERE id = ?",
        (access_token, token_date, kite_user_id, user_id)
    )
    conn.commit()


def clear_user_kite_token(user_id):
    conn = get_db()
    conn.execute(
        "UPDATE users SET kite_access_token = NULL, kite_token_date = NULL, kite_user_id = NULL WHERE id = ?",
        (user_id,)
    )
    conn.commit()


def get_user_kite_token(user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT kite_access_token, kite_token_date, kite_user_id FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()
    if row and row["kite_access_token"]:
        return {"kite_access_token": row["kite_access_token"],
                "kite_token_date": row["kite_token_date"],
                "kite_user_id": row["kite_user_id"]}
    return None


def get_setting(key, user_id=None):
    conn = get_db()
    if user_id:
        row = conn.execute("SELECT value FROM settings WHERE key = ? AND user_id = ?", (key, user_id)).fetchone()
        if row:
            return row["value"]
    # Fall back to global setting
    row = conn.execute("SELECT value FROM settings WHERE key = ? AND (user_id IS NULL OR user_id = '')", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(key, value, user_id=None):
    conn = get_db()
    if user_id:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, user_id, updated_at) VALUES (?, ?, ?, ?)",
            (key, str(value), user_id, now_iso())
        )
    else:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, str(value), now_iso())
        )
    conn.commit()


def get_all_settings(user_id=None):
    conn = get_db()
    # Start with global defaults
    rows = conn.execute("SELECT key, value FROM settings WHERE user_id IS NULL OR user_id = ''").fetchall()
    result = {row["key"]: row["value"] for row in rows}
    # Override with user-specific settings
    if user_id:
        user_rows = conn.execute("SELECT key, value FROM settings WHERE user_id = ?", (user_id,)).fetchall()
        for row in user_rows:
            result[row["key"]] = row["value"]
    return result


# ─── Holdings persistence ────────────────────────────────────────────────────

def get_all_holdings(user_id=None):
    """Load all holdings from the database."""
    conn = get_db()
    if user_id:
        rows = conn.execute("SELECT symbol, qty, avg_price, ltp FROM holdings WHERE user_id = ? ORDER BY symbol", (user_id,)).fetchall()
    else:
        rows = conn.execute("SELECT symbol, qty, avg_price, ltp FROM holdings ORDER BY symbol").fetchall()
    return [{"symbol": r["symbol"], "qty": r["qty"], "avgPrice": r["avg_price"],
             "ltp": r["ltp"] or r["avg_price"]} for r in rows]


def save_holdings(holdings, user_id=None):
    """Replace all holdings in the database for a user."""
    conn = get_db()
    if user_id:
        conn.execute("DELETE FROM holdings WHERE user_id = ?", (user_id,))
    else:
        conn.execute("DELETE FROM holdings")
    for h in holdings:
        conn.execute(
            "INSERT INTO holdings (user_id, symbol, qty, avg_price, ltp, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id or "", h["symbol"], h["qty"], h["avgPrice"], h.get("ltp", h["avgPrice"]), now_iso())
        )
    conn.commit()


def upsert_holding(holding, user_id=None):
    """Insert or update a single holding."""
    uid = user_id or ""
    conn = get_db()
    conn.execute(
        "INSERT INTO holdings (user_id, symbol, qty, avg_price, ltp, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, symbol) DO UPDATE SET qty=?, avg_price=?, ltp=?, updated_at=?",
        (uid, holding["symbol"], holding["qty"], holding["avgPrice"],
         holding.get("ltp", holding["avgPrice"]), now_iso(),
         holding["qty"], holding["avgPrice"],
         holding.get("ltp", holding["avgPrice"]), now_iso())
    )
    conn.commit()


def delete_holding(symbol, user_id=None):
    """Delete a holding by symbol."""
    conn = get_db()
    if user_id:
        conn.execute("DELETE FROM holdings WHERE symbol = ? AND user_id = ?", (symbol, user_id))
    else:
        conn.execute("DELETE FROM holdings WHERE symbol = ?", (symbol,))
    conn.commit()


def get_cash_balance(user_id=None):
    """Get persisted cash balance from settings."""
    val = get_setting("cash_balance", user_id=user_id)
    try:
        return float(val) if val else 0
    except (ValueError, TypeError):
        return 0


def save_cash_balance(amount, user_id=None):
    """Persist cash balance to settings."""
    set_setting("cash_balance", str(amount), user_id=user_id)
