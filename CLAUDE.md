# GBX Yield Engine — Claude Code Rules

## What is Yield Engine?
Portfolio income system for Indian F&O (Futures & Options) markets. Scans option strategies (covered calls, cash-secured puts, credit spreads, collars), detects arbitrage, monitors risk, tracks trades & P&L. Uses Zerodha Kite for live trading with simulation fallback.

## Tech Stack
- **Backend**: Python 3.12, Flask 3.1, Gunicorn (single worker + 4 threads for SQLite)
- **Frontend**: React 18, Vite 6, React Router 6, Recharts, Lucide React
- **Database**: SQLite (file-based, `data/yield_engine.db`)
- **Broker**: Zerodha Kite Connect API + TOTP auto-login
- **Market Data**: Yahoo Finance (spot prices) + NSE India API (option chains)
- **Options Pricing**: Black-Scholes (custom implementation)
- **Scheduling**: APScheduler (IST timezone)
- **Deploy**: Docker (multi-stage) → GHCR → Azure Container Apps (Central India)

## Self-Updating Rules (MANDATORY)

**This file MUST stay in sync with the codebase.** When making changes:
- **New service module added** → update Project Registry
- **New API endpoint added** → update API Endpoints section
- **New page added** → update Frontend Pages section
- **New env var added** → update Environment Variables section
- **New DB table added** → update Database Schema section
- **Pattern change** → update relevant Code Style section

---

## Task Management (MANDATORY)

1. **Create tasks** using `TaskCreate` for every non-trivial request
2. **Break into atomic tasks** — one task per logical unit
3. **Work iteratively** — pick one task, mark `in_progress`, complete, mark `completed`
4. **Never skip tasks** — work in order
5. **Update honestly** — only `completed` when verified working

---

## Testing Rules (MANDATORY)

### No Tests Exist Yet — Build the Foundation

This repo currently has **no automated tests**. When tests are eventually added:

- **Backend**: Use `pytest` with Flask test client
- **Frontend**: Use Vitest + React Testing Library
- **E2E**: Use Playwright
- Every code change SHOULD have tests
- Every new endpoint SHOULD have at minimum: happy path + error case + edge case

### Test Organization (target)
```
backend/tests/
├── conftest.py           # Flask test client, temp SQLite DB
├── test_api_status.py    # Health + permission endpoints
├── test_holdings.py      # Import/export/delete holdings
├── test_scan.py          # Strategy scanner
├── test_execute.py       # Order execution + dry-run
├── test_positions.py     # Position management
├── test_analytics.py     # P&L analytics
├── test_settings.py      # Settings CRUD
├── test_kite_auth.py     # Kite login flow (mocked)
└── services/
    ├── test_strategy_engine.py
    ├── test_risk_manager.py
    ├── test_black_scholes.py
    ├── test_fee_calculator.py
    └── test_live_price.py
```

---

## Code Style Rules

### Python (Backend)
- **No ORM** — raw SQL with `sqlite3`, parameterized queries
- **Type hints** encouraged but not enforced (existing code is mixed)
- **UUIDs**: `uuid.uuid4()` for all primary keys
- **Monetary**: Use `float` with `round(x, 2)` for display (not Decimal — SQLite stores as REAL)
- **DB access**: Always use `get_db()` → shared connection via `_SharedConnection`
- **Connection pattern**: Call `conn.close()` after use (no-op due to shared connection wrapper)
- **Imports**: stdlib → third-party → local modules
- **IST timezone**: All scheduler jobs use `Asia/Kolkata`

### React (Frontend)
- **JSX** (not TypeScript) — all components are `.jsx`
- **Functional components** with hooks
- **API calls**: Go through `src/api.js` — never use `fetch()` directly
- **Routing**: React Router v6 (BrowserRouter in App.jsx)
- **Charts**: Recharts for all visualizations
- **Icons**: Lucide React only
- **Styling**: Inline styles or utility classes (no CSS framework)

### SQLite Patterns (Azure File Share / SMB)
- **Single worker** (gunicorn): Multiple workers break SQLite locking on SMB
- **Shared connection**: `_SharedConnection` wrapper makes `close()` a no-op
- **Global lock**: `_db_lock` serializes DB access across threads
- **journal_mode=DELETE**: WAL doesn't work on SMB
- **busy_timeout=10000**: For contention handling
- **Individual execute()**: Never use `executescript()` (ignores busy_timeout)

---

## Workflow Rules

### Before Writing Code
1. **Read the file first** — understand context before modifying
2. **Check related modules** — understand data flow between services
3. **Check the models** — understand DB schema constraints

### After Writing Code
1. **Test manually** if no automated tests exist
2. **Check imports** — ensure no circular dependencies
3. **Verify DB changes** — run `init_db()` to test table creation

### When Fixing Bugs
1. Understand the data flow (API → service → DB)
2. Check if it's a frontend naming issue (`avgPrice` vs `average_price`)
3. Check if it's an Azure/SQLite issue (SMB locking, shared connection)

---

## Environment Variables

### Backend
```
# Kite API (broker integration)
KITE_API_KEY            # Zerodha API key
KITE_API_SECRET         # Zerodha API secret
KITE_USER_ID            # Zerodha user ID
KITE_PASSWORD           # Used once for auto-login, never stored
KITE_TOTP_SECRET        # Base32 TOTP seed for auto-login

# Database
SQLITE_DB_PATH          # Default: backend/data/yield_engine.db

# Server
PORT                    # Default: 8000
FLASK_SECRET_KEY        # For TOTP encryption (default: "yield-engine-default-key")
ENVIRONMENT             # Default: Development

# Safety Caps (NOT configurable via UI)
MAX_LOTS_NIFTY          # Default: 2
MAX_LOTS_BANKNIFTY      # Default: 1
MAX_LOTS_STOCK          # Default: 2
MAX_ORDER_VALUE         # Default: 500000
MAX_ORDERS_PER_DAY      # Default: 20
MAX_OPEN_POSITIONS      # Default: 10
PRICE_DEVIATION_LIMIT   # Default: 0.20
```

### Frontend
```
VITE_API_BASE           # API base URL (set at build time, empty = relative URLs)
```

### GitHub Actions Secrets (Required for Deploy)
```
AZURE_CREDENTIALS       # Service principal JSON (clientId, clientSecret, subscriptionId, tenantId)
GHCR_PAT                # GitHub PAT with read:packages scope
BACKEND_FQDN            # e.g., yield-engine-api.whiteocean-b818a22a.centralindia.azurecontainerapps.io
```

---

## Azure Deployment

### Infrastructure
- **Resource Group**: `YieldEngine` (Central India)
- **Container Environment**: `yield-engine-env`
- **Backend App**: `yield-engine-api` → `https://yield-engine-api.whiteocean-b818a22a.centralindia.azurecontainerapps.io`
- **Frontend App**: `yield-engine-web` → `https://yield-engine-web.whiteocean-b818a22a.centralindia.azurecontainerapps.io`
- **Registry**: GHCR (ghcr.io/gbx-ai/yieldengine-backend, ghcr.io/gbx-ai/yieldengine-frontend)

### Deploy Pipeline (GitHub Actions)
1. Push to `main` triggers `.github/workflows/deploy.yml`
2. **build-backend** and **build-frontend** run in parallel → push to GHCR
3. **deploy** job: Azure login → set GHCR registry → update container apps

### Manual Deploy
```bash
az login
bash deploy/azure-deploy.sh [TAG]   # default: latest
```

### Azure CLI Notes (Windows / Git Bash)
- Azure CLI path: `"/c/Program Files/Microsoft SDKs/Azure/CLI2/wbin/az"`
- Use `MSYS_NO_PATHCONV=1` for az commands with `/subscriptions/...` paths
- Use `azure/cli@v2` action (not raw `run:`) in GitHub Actions for reliable auth

---

## Database Schema

### Tables (10 total, raw SQL in `models.py`)
| Table | PK | Purpose |
|-------|-----|---------|
| `trades` | `id` (UUID) | Trade entries/exits, P&L, strategy type, fees |
| `positions` | `id` (UUID) | Active option positions, Greeks, margin, expiry |
| `holdings` | `symbol` (TEXT) | Stock holdings (qty, avg_price, ltp) |
| `portfolio_snapshots` | `id` (UUID) | Saved portfolio snapshots for comparison |
| `notifications` | `id` (UUID) | System notifications (type, severity, read) |
| `daily_summary` | `date` (TEXT) | Daily P&L, margin, collateral aggregates |
| `settings` | `key` (TEXT) | Key-value configuration store |
| `order_audit` | `id` (UUID) | Order placement history, dry-run results |
| `gtt_orders` | `id` (UUID) | GTT stop-loss orders |
| `adjustments` | `id` (UUID) | Position adjustment history |

---

## Project Registry (KEEP UPDATED)

### Backend Services (`backend/`)
| File | Purpose |
|------|---------|
| `app.py` | Flask app factory + all API routes (1240 lines) |
| `models.py` | SQLite DB, tables, settings, holdings CRUD |
| `kite_service.py` | Zerodha Kite integration + TOTP auto-login |
| `live_price_service.py` | Yahoo Finance + NSE API spot/option prices |
| `strategy_engine.py` | Option strategy scanner (4 strategies) |
| `arbitrage_scanner.py` | Cash-futures arbitrage detector |
| `risk_manager.py` | Position monitoring, alerts, adjustment suggestions |
| `trade_tracker.py` | P&L tracking, trade record/close |
| `scheduler.py` | APScheduler background jobs (IST) |
| `black_scholes.py` | Option pricing + Greeks |
| `strike_selector.py` | Strike selection by risk profile |
| `fee_calculator.py` | Zerodha brokerage + STT + fees |
| `notification_service.py` | Create notifications |
| `dry_run_validator.py` | Pre-order validation |
| `reconciliation.py` | Post-order reconciliation |
| `gunicorn.conf.py` | Single worker, 4 threads, port 8000 |

### Frontend Pages (`frontend/src/pages/`)
| File | Route | Purpose |
|------|-------|---------|
| `Dashboard.jsx` | `/` | Portfolio overview, P&L summary |
| `Holdings.jsx` | `/holdings` | Holdings management, CSV import |
| `Scanner.jsx` | `/scanner` | Strategy + arbitrage scanner |
| `Positions.jsx` | `/positions` | Active positions, close/roll |
| `TradeLog.jsx` | `/trades` | Trade history |
| `Analytics.jsx` | `/analytics` | P&L charts, strategy breakdown |
| `RiskMonitor.jsx` | `/risk` | Risk metrics, alerts |
| `Arbitrage.jsx` | `/arbitrage` | Arbitrage opportunities |
| `Settings.jsx` | `/settings` | App configuration |

### Frontend Core (`frontend/src/`)
| File | Purpose |
|------|---------|
| `App.jsx` | Router + navigation layout |
| `api.js` | Fetch wrapper + all endpoint definitions |
| `main.jsx` | React entry point |
| `components/Header.jsx` | Navigation header |
| `components/PermissionGate.jsx` | Execute mode confirmation modal |
| `components/OrderConfirmation.jsx` | Order confirmation dialog |

### API Endpoints (`/api/...`)
- **Status**: `/status`, `/permission`
- **Holdings**: `/holdings`, `/import/csv`, `/import/json`, `/import/manual`, `/import/kite`
- **Portfolios**: `/portfolios`, `/portfolios/:id/load`
- **Scanner**: `/scan`, `/recommendations`, `/arbitrage`
- **Execute**: `/execute` (EXECUTE mode required)
- **Positions**: `/positions`, `/positions/:id/close`, `/positions/:id/roll`, `/positions/:id/adjustments`
- **Trades**: `/trades`, `/trades/:id`
- **Analytics**: `/analytics/summary`, `/analytics/strategy`, `/analytics/monthly`, `/analytics/daily`
- **Notifications**: `/notifications`, `/notifications/unread-count`, `/notifications/:id/read`, `/notifications/read-all`
- **Settings**: `/settings`, `/settings/risk-profile`, `/settings/circuit-breaker`, `/safety/caps`
- **Risk**: `/risk/status`, `/risk/alerts`
- **Fees**: `/fees/estimate`, `/fees/summary`
- **Audit**: `/audit/orders`, `/gtt/active`, `/gtt/:id`
- **Auth**: `/kite/login`, `/callback`, `/kite/auto-login`
- **Daily**: `/daily-summary`, `/daily-summary/:date`, `/collateral`

---

## Authentication & Security

### Kite Login Flow
1. **Auto-Login** (06:30 IST): TOTP secret → OTP → Kite API → access token → stored in settings
2. **Manual Login**: Redirect to Kite → callback with `request_token` → exchange for access token
3. **Simulation Mode**: No Kite credentials → fully simulated with Yahoo Finance prices

### Permission Model
- **READONLY** (default): View-only, no trading
- **EXECUTE**: Can place orders — requires explicit UI confirmation modal

### Safety Controls
- Hard caps via env vars (not UI-configurable)
- Dry-run validation before every order
- Circuit breaker on daily loss limits
- GTT auto-placement for stop-losses

---

## Git Conventions
- Conventional commits: `feat:`, `fix:`, `refactor:`, `docs:`
- Keep commits focused — one logical change per commit
- Push to `main` triggers deployment

## Important Notes
- NEVER store Kite passwords — used once and discarded
- NEVER use `executescript()` — breaks on Azure SMB
- ALWAYS use parameterized SQL queries — prevent injection
- ALWAYS use `get_db()` for database access — shared connection required
- Frontend field naming: `avgPrice`, `qty`, `ltp` (camelCase)
- Backend DB columns: `avg_price`, `qty`, `ltp` (snake_case)
- Holdings API returns BOTH conventions for compatibility
