# YieldEngine v4 — Product Transformation Plan

## Goal
Transform YieldEngine from a personal trading tool into a **sellable SaaS analytics platform** that requires **no SEBI RA/IA registration**.

**Product positioning:** Read-only options analytics & portfolio intelligence tool. Users make their own decisions — the app shows the math, not recommendations.

---

## Summary of Changes

| Area | Current State | Target State |
|------|--------------|--------------|
| **Auth** | None — app is fully open | JWT login/signup + subscription gating |
| **Kite Integration** | Server stores user's password/TOTP | Kite OAuth — user logs in on Zerodha's site |
| **Scanner** | Auto-recommends ranked strategies with safety tags | Strategy Builder — user picks symbol/strategy/strike |
| **Execution** | Can place orders via Kite API | Removed — read-only only |
| **Payment** | None | Razorpay subscription (like InvoiceManagement) |
| **Landing Page** | None — goes straight to dashboard | Public marketing page with pricing |
| **Data** | SQLite local file | PostgreSQL (Azure Flexible Server) for multi-user |

---

## Phase 1: Authentication & User Management

### Backend (FastAPI-style auth on Flask, or migrate to FastAPI)

**New files to create:**
- `backend/auth_service.py` — JWT creation, password hashing (bcrypt), token refresh, blacklisting
- `backend/user_models.py` — User, Subscription, SubscriptionPlan tables
- `backend/auth_routes.py` — `/api/auth/signup`, `/api/auth/login`, `/api/auth/refresh`, `/api/auth/logout`
- `backend/auth_middleware.py` — `@require_auth` decorator for protected routes

**Database tables needed:**
```sql
users (id, email, name, password_hash, role, is_active, must_change_password,
       failed_login_attempts, locked_until, created_at)

subscription_plans (id, name, billing_cycle, price, max_symbols, features,
                    is_active, is_default_trial, offer_price, offer_ends_at)

vendor_subscriptions (id, user_id, plan_id, status, started_at, expires_at,
                      grace_period_ends_at, amount_paid, razorpay_order_id,
                      razorpay_payment_id)

subscription_orders (id, user_id, plan_id, status, plan_price, effective_price,
                     net_amount, razorpay_order_id, razorpay_payment_id, created_at)

token_blacklist (id, token_hash, expires_at)
```

**Auth flow:**
1. `POST /api/auth/signup` — Create user + auto-assign 14-day trial + return JWT tokens
2. `POST /api/auth/login` — Validate credentials, return access (30min) + refresh (7day) tokens
3. `POST /api/auth/refresh` — Exchange refresh token for new access token
4. `POST /api/auth/logout` — Blacklist refresh token
5. All existing `/api/*` routes — Add `@require_auth` decorator
6. Subscription check — `@require_active_subscription` on data endpoints

**Changes to existing files:**
- `app.py` — Wrap all route handlers with auth middleware; add auth blueprint
- `models.py` — Add user/subscription tables to `init_db()`

**Reference implementation:** `GBX.InvoiceManagementApi/invoice-api/app/routers/auth.py` and `app/services/auth_service.py`

### Frontend

**New files to create:**
- `frontend/src/stores/authStore.js` — Zustand store for auth state (token, user, isAuthenticated)
- `frontend/src/stores/subscriptionStore.js` — Subscription status, plan info
- `frontend/src/lib/api.js` — Update with auth headers + 401 interceptor + token refresh
- `frontend/src/pages/Login.jsx` — Email + password login form
- `frontend/src/pages/Signup.jsx` — Registration form
- `frontend/src/pages/Subscription.jsx` — Plan selection + Razorpay checkout
- `frontend/src/components/ProtectedRoute.jsx` — Wrapper that redirects to /login if not authed
- `frontend/src/pages/LandingPage.jsx` — Public marketing page (features, pricing, CTA)

**Changes to existing files:**
- `frontend/src/App.jsx` — Wrap all routes in `<ProtectedRoute>`, add public routes for login/signup/landing
- `frontend/src/api.js` — Add Authorization header to all requests, handle 401 → refresh flow
- `frontend/src/components/Header.jsx` — Add user menu (profile, logout, subscription status)

**Reference implementation:** `GBX.InvoiceManagementUI/invoice-portal/src/stores/authStore.ts` and `src/lib/api.ts`

---

## Phase 2: Kite OAuth (Replace Stored Credentials)

### Backend

**Changes to `kite_service.py`:**
- Remove env vars: `KITE_PASSWORD`, `KITE_TOTP_SECRET`
- Remove `_auto_login()` method entirely
- Remove `_encrypt_value()` / `_decrypt_value()` TOTP functions
- Keep `KITE_API_KEY` and `KITE_API_SECRET` (these are YOUR app's credentials, not user's)
- Store per-user access_token in the user's DB record (not global state)

**New OAuth flow:**
1. `GET /api/kite/connect` — Returns Kite login URL: `https://kite.zerodha.com/connect/login?v=3&api_key=XXX`
2. User logs into Zerodha in browser (your app never sees their password)
3. Zerodha redirects to your callback URL with `request_token`
4. `GET /api/kite/callback?request_token=XXX` — Exchange for `access_token`, store in user's DB record
5. `GET /api/kite/status` — Check if user has a valid Kite session

**Changes to existing files:**
- `kite_service.py` — Make it per-user (not singleton). Each user gets their own KiteConnect instance with their own access_token
- `app.py` — All Kite-dependent endpoints resolve the current user's Kite session from their DB record
- Global `state` dict — Remove. Replace with per-user state in DB

**Per-user Kite state in DB:**
```sql
ALTER TABLE users ADD COLUMN kite_access_token TEXT;
ALTER TABLE users ADD COLUMN kite_token_date TEXT;
ALTER TABLE users ADD COLUMN kite_user_id TEXT;
```

### Frontend

**Changes to `Settings.jsx`:**
- Replace API key/secret input fields with a single "Connect to Zerodha" button
- Button opens Kite login in new window/redirect
- Show connection status: "Connected as RE8646" or "Not connected — using simulated data"
- Add "Disconnect" button

---

## Phase 3: Replace Scanner with Strategy Builder

### What to REMOVE (implies advice/recommendation — triggers SEBI RA requirement)

**Backend removals:**
- `strategy_engine.py` → `scan_strategies()` — Remove auto-scanning logic
- `app.py` → `/api/scan` endpoint — Remove or repurpose
- `app.py` → `/api/recommendations` endpoint — Remove entirely
- `arbitrage_scanner.py` → Remove auto-generated arbitrage "opportunities"
- All `safety_tag`, `rank`, `classify_safety()` logic — Remove
- `_add_frontend_aliases()` that adds `safety` and `premium` recommendation fields — Remove

**Frontend removals:**
- `Scanner.jsx` → Auto-scan on load, recommendation cards, safety filters, rank display — Remove
- `Arbitrage.jsx` → Remove or convert to pure data display

### What to BUILD (user-driven analytics — no license needed)

**New backend endpoints:**
```
POST /api/strategy/analyze
  Body: { symbol, strategy_type, expiry, strikes: [strike1, strike2], lots }
  Returns: { payoff, greeks, max_profit, max_loss, breakeven, margin_needed, fees, probability }

GET /api/options/chain?symbol=NIFTY&expiry=2026-04-03
  Returns: Full option chain with IV, OI, volume, Greeks for every strike

GET /api/options/expiries?symbol=NIFTY
  Returns: Available expiry dates

GET /api/symbols/fno
  Returns: List of F&O eligible symbols with lot sizes
```

**New/modified frontend pages:**

#### Strategy Builder (`StrategyBuilder.jsx` — replaces `Scanner.jsx`)
```
Step 1: User selects symbol (dropdown with search)
Step 2: User selects strategy type (Covered Call, Cash Secured Put, Bull Put Spread, Collar, Custom)
Step 3: App shows option chain — user clicks to select strikes
Step 4: App computes and displays:
  - Payoff diagram (interactive chart)
  - Greeks (delta, gamma, theta, vega)
  - Max profit / Max loss / Breakeven
  - Margin required
  - Estimated fees
  - Probability of profit (Black-Scholes)
  - Annualized return (informational, not ranked)
Step 5: User can adjust strikes/lots and see metrics update in real-time
```

#### Option Chain Viewer (`OptionChain.jsx` — new page)
```
- Full interactive option chain grid
- Columns: Strike, CE Premium, CE IV, CE OI, CE Volume, CE Greeks | PE Premium, PE IV, PE OI, PE Volume, PE Greeks
- Color coding: OI heatmap, IV percentile
- Expiry selector tabs
- Spot price with live update indicator
- Click-to-select strikes for strategy builder
```

#### Changes to Arbitrage page
- Remove "opportunities" auto-generation
- Replace with: user-input comparison tool (compare two related instruments)
- OR remove entirely and add the data to option chain viewer

### What to RENAME

| Current Term | New Term | Why |
|---|---|---|
| "Recommendations" | Remove entirely | Implies advice |
| "Safety Tag" (VERY_SAFE etc) | Remove entirely | Implies advice on risk |
| "Rank" | Remove entirely | Implies best/worst |
| "Scanner" | "Strategy Builder" | User-driven, not auto |
| "Scan Now" | "Analyze" | Neutral |
| "Premium Income" | "Estimated Premium" | Informational |
| "Arbitrage Opportunities" | "Spread Comparison" or remove | Neutral |

---

## Phase 4: Remove Trade Execution

**Backend removals:**
- `app.py` → `/api/execute` endpoint — Remove
- `app.py` → `/api/positions/<id>/close` — Remove
- `app.py` → `/api/positions/<id>/roll` — Remove
- `kite_service.py` → `place_order()` — Remove
- `kite_service.py` → `place_gtt()` — Remove
- `dry_run_validator.py` → Remove entirely (validates orders before execution)
- `reconciliation.py` → Remove entirely (reconciles executed orders)
- Permission system (READONLY/EXECUTE toggle) — Remove, always read-only

**Frontend removals:**
- "Execute Trade" button on Scanner cards — Remove
- Permission toggle (READONLY/EXECUTE) — Remove
- Any confirmation dialogs for trade execution — Remove

---

## Phase 5: Database Migration (SQLite → PostgreSQL)

**Why:** Multi-user SaaS needs proper concurrent access, not SQLite.

**Azure resource:** Azure Database for PostgreSQL Flexible Server (Burstable B1ms: ~₹1,200/month)

**Changes:**
- `models.py` — Replace `sqlite3` with `psycopg2` or `SQLAlchemy`
- All raw SQL queries — Update for PostgreSQL syntax (minor differences)
- Connection pooling — Add via SQLAlchemy or psycopg2.pool
- `SQLITE_DB_PATH` env var → `DATABASE_URL` env var
- Remove `_SharedConnection` wrapper (not needed with PostgreSQL)

**Tables to add:** users, subscription_plans, vendor_subscriptions, subscription_orders, token_blacklist

**Tables to modify:**
- `holdings` — Add `user_id` foreign key (per-user holdings)
- `trades` — Add `user_id`
- `positions` — Add `user_id`
- `settings` — Add `user_id` (per-user settings)
- `notifications` — Add `user_id`

---

## Phase 6: Landing Page & Razorpay

### Landing Page (`LandingPage.jsx`)
- Hero section: "Options Analytics for Smarter Decisions"
- Feature highlights with screenshots
- Pricing cards (Basic / Pro / Annual)
- Testimonials placeholder
- CTA: "Start Free Trial"
- Footer with disclaimer

### Razorpay Integration
**Backend:**
- `backend/razorpay_service.py` — Order creation, signature verification
- `POST /api/subscription/create-order` — Create Razorpay order
- `POST /api/subscription/verify-payment` — Verify and activate subscription
- `GET /api/subscription/plans` — List available plans

**Frontend:**
- Razorpay checkout script loaded on Subscription page
- Plan selection → Razorpay popup → Payment → Subscription activated

**Reference:** `GBX.InvoiceManagementApi/invoice-api/app/services/razorpay_service.py`

---

## Phase 7: Disclaimers & Legal

**Add to every page (footer component):**
> "YieldEngine is an analytics tool for informational purposes only. It does not provide investment advice, recommendations, or trading signals. Users are solely responsible for their investment decisions. Options trading involves risk of loss."

**Add to Strategy Builder output:**
> "The metrics shown are computed using Black-Scholes model with market data. They are estimates, not guarantees. This is not a recommendation to trade."

**Terms of Service page** — Standard SaaS ToS + specific disclaimer about not being SEBI RA/IA

**Privacy Policy page** — Required for collecting user data

---

## Files Summary

### New files to create
```
backend/
  auth_service.py          — JWT, password hashing, token management
  auth_routes.py           — /api/auth/* endpoints
  auth_middleware.py        — @require_auth, @require_active_subscription
  user_models.py           — User, Subscription tables
  razorpay_service.py      — Payment integration
  subscription_routes.py   — /api/subscription/* endpoints
  strategy_analyzer.py     — Replaces strategy_engine.py (user-driven analysis)

frontend/src/
  stores/authStore.js      — Auth state (Zustand)
  stores/subscriptionStore.js
  pages/Login.jsx
  pages/Signup.jsx
  pages/Subscription.jsx
  pages/LandingPage.jsx
  pages/StrategyBuilder.jsx  — Replaces Scanner.jsx
  pages/OptionChain.jsx      — New interactive option chain
  components/ProtectedRoute.jsx
  components/Disclaimer.jsx
```

### Files to modify heavily
```
backend/
  app.py                  — Add auth middleware to all routes, new endpoints
  models.py               — PostgreSQL migration, new tables, per-user data
  kite_service.py         — Per-user OAuth, remove stored credentials

frontend/src/
  api.js                  — Auth headers, 401 interceptor, token refresh
  App.jsx                 — Protected routes, public routes
  components/Header.jsx   — User menu, logout, subscription badge
  pages/Settings.jsx      — Kite OAuth button, subscription management
  pages/Dashboard.jsx     — Rename to Portfolio Overview
```

### Files to remove
```
backend/
  strategy_engine.py      — Auto-recommendation engine (SEBI RA trigger)
  dry_run_validator.py    — Order validation (no execution)
  reconciliation.py       — Order reconciliation (no execution)

frontend/src/pages/
  Scanner.jsx             — Replaced by StrategyBuilder.jsx
  Arbitrage.jsx           — Remove or convert to data-only
```

---

## Estimated Monthly Cost (Production)

| Resource | Cost |
|---|---|
| Azure Container Apps (API + Web, minReplicas=1) | ~₹2,500 |
| Azure PostgreSQL Flexible Server (B1ms) | ~₹1,200 |
| Kite Connect subscription | ₹500 |
| Domain name | ~₹100 |
| **Total** | **~₹4,300/month** |

**Break-even:** 9 users at ₹499/month or 6 users at ₹799/month
