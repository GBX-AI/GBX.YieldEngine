"""
Database models and initialization for Yield Engine.
Supports PostgreSQL (via DATABASE_URL) with SQLite fallback for local dev.
All tables created via raw SQL — no ORM.
"""

import os
import sqlite3
import uuid
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_PATH = os.getenv("SQLITE_DB_PATH", os.path.join(os.path.dirname(__file__), "data", "yield_engine.db"))
_is_pg = bool(DATABASE_URL)

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
    "NIFTY": {"spot": 23150, "lotSize": 75, "iv": 0.145, "support": 22800, "resistance": 23500},
    "BANKNIFTY": {"spot": 48900, "lotSize": 30, "iv": 0.162, "support": 47800, "resistance": 50000},
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
    "allowed_strategies": "COVERED_CALL,CASH_SECURED_PUT,PUT_CREDIT_SPREAD,COLLAR,SHORT_STRANGLE,IRON_CONDOR,RSI_OPTION_SELL,CALENDAR_SPREAD,CASH_FUTURES_ARB",
}


# ─── SQLite wrapper that accepts %s placeholders ─────────────────────────────

class _SQLiteWrapper:
    """Wraps a sqlite3.Connection so all SQL written with %s placeholders
    is transparently converted to ? for SQLite. Also converts NOW() to
    datetime('now') in SQL strings."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        sql = sql.replace("%s", "?").replace("NOW()", "datetime('now')")
        return self._conn.execute(sql, params or ())

    def executemany(self, sql, params_list):
        sql = sql.replace("%s", "?").replace("NOW()", "datetime('now')")
        return self._conn.executemany(sql, params_list)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        pass  # no-op — keep connection alive

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._conn.row_factory = value

    def __getattr__(self, name):
        return getattr(self._conn, name)


# ─── PostgreSQL wrapper that returns dict rows ───────────────────────────────

class _PgWrapper:
    """Wraps a psycopg2 connection. Uses RealDictCursor so rows come back
    as dicts (matching sqlite3.Row behaviour). execute() returns a cursor
    with a fetchone/fetchall interface."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        import psycopg2.extras
        # Convert SQLite-style ? placeholders to PostgreSQL %s
        sql = sql.replace("?", "%s")
        # Convert datetime('now') to NOW() if any slipped through
        sql = sql.replace("datetime('now')", "NOW()")
        try:
            cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params or ())
            return cur
        except Exception:
            try:
                self._conn.rollback()
            except Exception:
                pass
            raise

    def executemany(self, sql, params_list):
        import psycopg2.extras
        sql = sql.replace("?", "%s")
        sql = sql.replace("datetime('now')", "NOW()")
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.executemany(sql, params_list)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        pass  # no-op — keep connection alive

    def __getattr__(self, name):
        return getattr(self._conn, name)


# ─── Connection management ───────────────────────────────────────────────────

_db_conn = None


def get_db():
    """Return a wrapped database connection (PostgreSQL or SQLite)."""
    global _db_conn
    if _db_conn is not None:
        return _db_conn

    if _is_pg:
        import psycopg2
        raw = psycopg2.connect(DATABASE_URL)
        raw.autocommit = False
        _db_conn = _PgWrapper(raw)
    else:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        raw = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA journal_mode=DELETE")
        raw.execute("PRAGMA busy_timeout=10000")
        raw.execute("PRAGMA foreign_keys=ON")
        _db_conn = _SQLiteWrapper(raw)

    return _db_conn


def generate_id():
    return str(uuid.uuid4())


def now_iso():
    """Return current IST time as ISO string."""
    from datetime import timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).isoformat()


# ─── Table definitions (use %s placeholders, NOW() for timestamps) ───────────
# SQLite wrapper converts %s→? and NOW()→datetime('now') automatically.

_CREATE_TABLE_STMTS = [
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        kite_api_key TEXT, kite_api_secret TEXT,
        kite_access_token TEXT, kite_token_date TEXT, kite_user_id TEXT,
        kite_permission TEXT DEFAULT 'readonly',
        created_at TEXT DEFAULT (NOW())
    )""",
    """CREATE TABLE IF NOT EXISTS manual_trades (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
        symbol TEXT NOT NULL, strategy_type TEXT NOT NULL,
        tradingsymbol TEXT NOT NULL, action TEXT NOT NULL,
        strike REAL, option_type TEXT, expiry_date TEXT,
        entry_premium REAL NOT NULL, quantity INTEGER NOT NULL,
        lots INTEGER DEFAULT 1, lot_size INTEGER,
        entry_date TEXT DEFAULT (NOW()),
        exit_premium REAL, exit_date TEXT,
        status TEXT DEFAULT 'OPEN',
        pnl REAL, notes TEXT,
        rec_data TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS password_reset_tokens (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
        token_hash TEXT NOT NULL, expires_at TEXT NOT NULL, used INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (NOW())
    )""",
    """CREATE TABLE IF NOT EXISTS trades (
        id TEXT PRIMARY KEY, rec_id TEXT NOT NULL, strategy_type TEXT NOT NULL,
        symbol TEXT NOT NULL, direction TEXT NOT NULL, legs TEXT NOT NULL,
        entry_premium REAL NOT NULL, entry_time TEXT NOT NULL,
        exit_premium REAL, exit_time TEXT, exit_reason TEXT, pnl REAL,
        fees REAL DEFAULT 0, margin_used REAL DEFAULT 0,
        status TEXT DEFAULT 'OPEN', notes TEXT,
        created_at TEXT DEFAULT (NOW())
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
        created_at TEXT DEFAULT (NOW())
    )""",
    """CREATE TABLE IF NOT EXISTS notifications (
        id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL,
        message TEXT NOT NULL, severity TEXT DEFAULT 'INFO',
        read INTEGER DEFAULT 0, action_url TEXT,
        created_at TEXT DEFAULT (NOW())
    )""",
    """CREATE TABLE IF NOT EXISTS daily_summary (
        date TEXT PRIMARY KEY, open_positions INTEGER DEFAULT 0,
        trades_executed INTEGER DEFAULT 0, premium_collected REAL DEFAULT 0,
        premium_paid REAL DEFAULT 0, realized_pnl REAL DEFAULT 0,
        unrealized_pnl REAL DEFAULT 0, margin_used REAL DEFAULT 0,
        collateral_value REAL DEFAULT 0, notes TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS settings (
        key TEXT NOT NULL, value TEXT NOT NULL,
        user_id TEXT NOT NULL DEFAULT '',
        updated_at TEXT DEFAULT (NOW()),
        PRIMARY KEY (key, user_id)
    )""",
    """CREATE TABLE IF NOT EXISTS order_audit (
        id TEXT PRIMARY KEY, timestamp TEXT DEFAULT (NOW()),
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
        created_at TEXT DEFAULT (NOW()), triggered_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS adjustments (
        id TEXT PRIMARY KEY, trade_id TEXT REFERENCES trades(id),
        adjustment_type TEXT NOT NULL, old_legs TEXT NOT NULL,
        new_legs TEXT, cost REAL NOT NULL, reason TEXT,
        created_at TEXT DEFAULT (NOW())
    )""",
    """CREATE TABLE IF NOT EXISTS holdings (
        user_id TEXT NOT NULL DEFAULT '', symbol TEXT NOT NULL,
        qty INTEGER NOT NULL, avg_price REAL NOT NULL, ltp REAL,
        updated_at TEXT DEFAULT (NOW()),
        PRIMARY KEY (user_id, symbol)
    )""",
]


def init_db():
    """Create all tables, run migrations, insert default settings."""
    conn = get_db()
    for stmt in _CREATE_TABLE_STMTS:
        try:
            conn.execute(stmt)
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[MODELS] init_db table creation: {e}", flush=True)

    for key, value in DEFAULT_SETTINGS.items():
        try:
            conn.execute(
                "INSERT INTO settings (key, value, user_id) VALUES (%s, %s, '') "
                "ON CONFLICT (key, user_id) DO NOTHING",
                (key, value)
            )
        except Exception as e:
            conn.rollback()
            print(f"[MODELS] init_db setting {key}: {e}", flush=True)

    conn.commit()
    _migrate_add_user_id()


def _migrate_add_user_id():
    """Add user_id column to all data tables (idempotent)."""
    conn = get_db()
    tables_needing_user_id = [
        "trades", "positions", "portfolio_snapshots", "notifications",
        "daily_summary", "order_audit", "gtt_orders", "adjustments",
    ]

    if _is_pg:
        for table in tables_needing_user_id:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS user_id TEXT")
                conn.commit()
            except Exception:
                conn.rollback()
        for col in ("kite_api_key", "kite_api_secret", "kite_permission"):
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} TEXT")
                conn.commit()
            except Exception:
                conn.rollback()
    else:
        for table in tables_needing_user_id:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT")
            except Exception:
                pass
        for col in ("kite_api_key", "kite_api_secret", "kite_permission"):
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
            except Exception:
                pass
        conn.commit()


def migrate_orphaned_data(user_id):
    """Assign all data with empty/NULL user_id to the given user. Called on first signup."""
    conn = get_db()
    tables = ["holdings", "trades", "positions", "portfolio_snapshots", "notifications",
              "daily_summary", "order_audit", "gtt_orders", "adjustments", "settings"]
    for table in tables:
        try:
            conn.execute(f"UPDATE {table} SET user_id = %s WHERE user_id IS NULL OR user_id = ''", (user_id,))
        except Exception:
            conn.rollback()
    conn.commit()


# ─── User functions ──────────────────────────────────────────────────────────

def create_user(email, name, password_hash):
    """Create a new user and return the user dict."""
    conn = get_db()
    user_id = generate_id()
    conn.execute(
        "INSERT INTO users (id, email, name, password_hash) VALUES (%s, %s, %s, %s)",
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
    row = conn.execute("SELECT * FROM users WHERE email = %s", (email.lower().strip(),)).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
    return dict(row) if row else None


def save_user_kite_credentials(user_id, api_key, api_secret, permission='readonly'):
    """Store user's Kite API key, secret, and permission level."""
    conn = get_db()
    conn.execute(
        "UPDATE users SET kite_api_key = %s, kite_api_secret = %s, kite_permission = %s WHERE id = %s",
        (api_key, api_secret, permission, user_id)
    )
    conn.commit()


def get_user_kite_permission(user_id):
    """Return user's Kite permission level: 'readonly' or 'readwrite'."""
    conn = get_db()
    row = conn.execute("SELECT kite_permission FROM users WHERE id = %s", (user_id,)).fetchone()
    return (row["kite_permission"] or "readonly") if row else "readonly"


def set_user_kite_permission(user_id, permission):
    """Set user's Kite permission level."""
    conn = get_db()
    conn.execute("UPDATE users SET kite_permission = %s WHERE id = %s", (permission, user_id))
    conn.commit()


def get_user_kite_credentials(user_id):
    """Return user's Kite API key and secret, or None."""
    conn = get_db()
    row = conn.execute(
        "SELECT kite_api_key, kite_api_secret FROM users WHERE id = %s",
        (user_id,)
    ).fetchone()
    if row and row["kite_api_key"]:
        return {"kite_api_key": row["kite_api_key"], "kite_api_secret": row["kite_api_secret"]}
    return None


def update_user_kite_token(user_id, access_token, token_date, kite_user_id):
    conn = get_db()
    conn.execute(
        "UPDATE users SET kite_access_token = %s, kite_token_date = %s, kite_user_id = %s WHERE id = %s",
        (access_token, token_date, kite_user_id, user_id)
    )
    conn.commit()


def clear_user_kite_token(user_id):
    conn = get_db()
    conn.execute(
        "UPDATE users SET kite_access_token = NULL, kite_token_date = NULL, kite_user_id = NULL WHERE id = %s",
        (user_id,)
    )
    conn.commit()


def get_user_kite_token(user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT kite_access_token, kite_token_date, kite_user_id FROM users WHERE id = %s",
        (user_id,)
    ).fetchone()
    if row and row["kite_access_token"]:
        return {"kite_access_token": row["kite_access_token"],
                "kite_token_date": row["kite_token_date"],
                "kite_user_id": row["kite_user_id"]}
    return None


# ─── Password reset tokens ───────────────────────────────────────────────────

def create_reset_token(user_id, token_hash, expires_at):
    conn = get_db()
    token_id = generate_id()
    conn.execute(
        "INSERT INTO password_reset_tokens (id, user_id, token_hash, expires_at) VALUES (%s, %s, %s, %s)",
        (token_id, user_id, token_hash, expires_at)
    )
    conn.commit()
    return token_id


def get_valid_reset_token(token_hash):
    """Find a valid (unused, not expired) reset token."""
    conn = get_db()
    row = conn.execute(
        "SELECT id, user_id, expires_at FROM password_reset_tokens "
        "WHERE token_hash = %s AND used = 0 AND expires_at > NOW()",
        (token_hash,)
    ).fetchone()
    return dict(row) if row else None


def mark_reset_token_used(token_id):
    conn = get_db()
    conn.execute("UPDATE password_reset_tokens SET used = 1 WHERE id = %s", (token_id,))
    conn.commit()


def update_user_password(user_id, password_hash):
    conn = get_db()
    conn.execute("UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user_id))
    conn.commit()


# ─── Settings ────────────────────────────────────────────────────────────────

# ─── Manual trade tracking ────────────────────────────────────────────────────

def create_manual_trade(user_id, trade_data):
    """Record a manually executed trade."""
    conn = get_db()
    trade_id = generate_id()
    import json as _json
    conn.execute(
        "INSERT INTO manual_trades (id, user_id, symbol, strategy_type, tradingsymbol, action, "
        "strike, option_type, expiry_date, entry_premium, quantity, lots, lot_size, status, rec_data) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'OPEN',%s)",
        (trade_id, user_id, trade_data["symbol"], trade_data["strategy_type"],
         trade_data["tradingsymbol"], trade_data["action"],
         trade_data.get("strike"), trade_data.get("option_type"), trade_data.get("expiry_date"),
         trade_data["entry_premium"], trade_data["quantity"],
         trade_data.get("lots", 1), trade_data.get("lot_size"),
         _json.dumps(trade_data.get("rec_data")) if trade_data.get("rec_data") else None)
    )
    conn.commit()
    return trade_id


def get_open_manual_trades(user_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM manual_trades WHERE user_id = %s AND status = 'OPEN' ORDER BY entry_date DESC",
        (user_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_manual_trades(user_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM manual_trades WHERE user_id = %s ORDER BY entry_date DESC",
        (user_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def exit_manual_trade(trade_id, user_id, exit_premium, pnl=None, notes=None):
    conn = get_db()
    conn.execute(
        "UPDATE manual_trades SET status='CLOSED', exit_premium=%s, exit_date=NOW(), pnl=%s, notes=%s "
        "WHERE id=%s AND user_id=%s",
        (exit_premium, pnl, notes, trade_id, user_id)
    )
    conn.commit()


# ─── Settings ────────────────────────────────────────────────────────────────

def get_setting(key, user_id=None):
    conn = get_db()
    if user_id:
        row = conn.execute("SELECT value FROM settings WHERE key = %s AND user_id = %s", (key, user_id)).fetchone()
        if row:
            return row["value"]
    # Fall back to global setting
    row = conn.execute("SELECT value FROM settings WHERE key = %s AND (user_id IS NULL OR user_id = '')", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(key, value, user_id=None):
    conn = get_db()
    uid = user_id or ""
    conn.execute(
        "INSERT INTO settings (key, value, user_id, updated_at) VALUES (%s, %s, %s, NOW()) "
        "ON CONFLICT (key, user_id) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
        (key, str(value), uid)
    )
    conn.commit()


def get_all_settings(user_id=None):
    conn = get_db()
    # Start with global defaults
    rows = conn.execute("SELECT key, value FROM settings WHERE user_id IS NULL OR user_id = ''").fetchall()
    result = {row["key"]: row["value"] for row in rows}
    # Override with user-specific settings
    if user_id:
        user_rows = conn.execute("SELECT key, value FROM settings WHERE user_id = %s", (user_id,)).fetchall()
        for row in user_rows:
            result[row["key"]] = row["value"]
    return result


# ─── Holdings persistence ────────────────────────────────────────────────────

def get_all_holdings(user_id=None):
    """Load all holdings from the database."""
    conn = get_db()
    if user_id:
        rows = conn.execute("SELECT symbol, qty, avg_price, ltp FROM holdings WHERE user_id = %s ORDER BY symbol", (user_id,)).fetchall()
    else:
        rows = conn.execute("SELECT symbol, qty, avg_price, ltp FROM holdings ORDER BY symbol").fetchall()
    return [{"symbol": r["symbol"], "qty": r["qty"], "avgPrice": r["avg_price"],
             "ltp": r["ltp"] or r["avg_price"]} for r in rows]


def save_holdings(holdings, user_id=None):
    """Replace all holdings in the database for a user."""
    conn = get_db()
    if user_id:
        conn.execute("DELETE FROM holdings WHERE user_id = %s", (user_id,))
    else:
        conn.execute("DELETE FROM holdings")
    for h in holdings:
        conn.execute(
            "INSERT INTO holdings (user_id, symbol, qty, avg_price, ltp, updated_at) VALUES (%s, %s, %s, %s, %s, NOW())",
            (user_id or "", h["symbol"], h["qty"], h["avgPrice"], h.get("ltp", h["avgPrice"]))
        )
    conn.commit()


def upsert_holding(holding, user_id=None):
    """Insert or update a single holding."""
    uid = user_id or ""
    conn = get_db()
    conn.execute(
        "INSERT INTO holdings (user_id, symbol, qty, avg_price, ltp, updated_at) VALUES (%s, %s, %s, %s, %s, NOW()) "
        "ON CONFLICT(user_id, symbol) DO UPDATE SET qty = EXCLUDED.qty, avg_price = EXCLUDED.avg_price, ltp = EXCLUDED.ltp, updated_at = NOW()",
        (uid, holding["symbol"], holding["qty"], holding["avgPrice"],
         holding.get("ltp", holding["avgPrice"]))
    )
    conn.commit()


def delete_holding(symbol, user_id=None):
    """Delete a holding by symbol."""
    conn = get_db()
    if user_id:
        conn.execute("DELETE FROM holdings WHERE symbol = %s AND user_id = %s", (symbol, user_id))
    else:
        conn.execute("DELETE FROM holdings WHERE symbol = %s", (symbol,))
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
