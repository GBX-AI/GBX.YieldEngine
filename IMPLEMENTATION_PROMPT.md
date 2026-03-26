# YieldEngine v4 — Implementation Prompt

Use this prompt to guide Claude Code through the transformation of YieldEngine from a personal trading tool into a sellable SaaS analytics platform.

**Context:** Read `CHANGES_NEEDED.md` in this repo for the full plan. The app is a Flask + React (Vite) monorepo deployed on Azure Container Apps. Reference `GBX.InvoiceManagementApi` and `GBX.InvoiceManagementUI` repos for auth/subscription patterns.

---

## Prompt (copy and use phase by phase)

### Phase 1: Auth & Subscription System

```
You are working on the YieldEngine monorepo at C:\Users\modes\codebase\GBX.YieldEngine.

Read CHANGES_NEEDED.md for the full transformation plan.

TASK: Implement Phase 1 — Authentication & User Management.

Reference repos for patterns:
- GBX.InvoiceManagementApi (FastAPI) — clone from https://github.com/GBX-AI/GBX.InvoiceManagementApi and study auth_service.py, auth.py router, dependencies.py, security.py
- GBX.InvoiceManagementUI (Next.js) — clone from https://github.com/GBX-AI/GBX.InvoiceManagementUI and study authStore.ts, api.ts, LoginForm.tsx, layout.tsx (protected routes)

Implement in the Flask + React stack (not FastAPI/Next.js):

BACKEND:
1. Create auth_service.py — JWT (HS256) with access (30min) + refresh (7day) tokens, bcrypt password hashing, token blacklisting
2. Create user_models.py — users, subscription_plans, user_subscriptions, subscription_orders, token_blacklist tables
3. Create auth_routes.py — Flask blueprint with:
   - POST /api/auth/signup (auto-assign 14-day trial, return tokens)
   - POST /api/auth/login (validate credentials, account lockout after 5 failures)
   - POST /api/auth/refresh (exchange refresh token)
   - POST /api/auth/logout (blacklist token)
   - POST /api/auth/forgot-password
   - POST /api/auth/reset-password
4. Create auth_middleware.py — @require_auth decorator (extracts user from JWT, returns 401 if invalid), @require_active_subscription (checks trial/active/grace status)
5. Update app.py — Register auth blueprint, wrap ALL existing routes with @require_auth
6. Update models.py — Add new tables to init_db(), migrate from SQLite to PostgreSQL (use psycopg2 or SQLAlchemy). Add user_id foreign key to holdings, trades, positions, settings, notifications tables.

FRONTEND:
1. Install zustand for state management, js-cookie for tokens
2. Create stores/authStore.js — token storage, user info, isAuthenticated flag, login/logout actions
3. Create stores/subscriptionStore.js — subscription status, plan info, limits
4. Update api.js — Add Authorization: Bearer header to all requests, add 401 response interceptor that auto-refreshes tokens and retries
5. Create pages/Login.jsx — Email + password form, styled to match existing dark theme (use same design tokens as Dashboard.jsx)
6. Create pages/Signup.jsx — Name, email, password, confirm password form
7. Create components/ProtectedRoute.jsx — Checks authStore.isAuthenticated, redirects to /login if false
8. Update App.jsx — Wrap all routes in ProtectedRoute, add public routes: /login, /signup, /landing
9. Update Header.jsx — Add user dropdown menu (email, subscription status, logout button)

IMPORTANT:
- Match the existing dark theme (bg: #0a0f1a, card: rgba(15,23,42,0.7), emerald: #6ee7b7, etc.)
- Use the same font stack (DM Sans + IBM Plex Mono)
- Follow the InvoiceManagement patterns for JWT flow, but implement in Flask, not FastAPI
- Keep the app functional throughout — don't break existing features while adding auth
```

### Phase 2: Kite OAuth

```
You are working on the YieldEngine monorepo at C:\Users\modes\codebase\GBX.YieldEngine.

Read CHANGES_NEEDED.md for context. Phase 1 (auth) is complete.

TASK: Implement Phase 2 — Replace stored Kite credentials with OAuth flow.

BACKEND changes to kite_service.py:
1. Remove KITE_PASSWORD and KITE_TOTP_SECRET env vars entirely
2. Remove _auto_login() method and all TOTP-related code (_encrypt_value, _decrypt_value, _resolve_totp_secret)
3. Keep KITE_API_KEY and KITE_API_SECRET (these are the app's credentials, registered on Kite Connect)
4. Make KiteService per-user instead of singleton:
   - Constructor takes user_id
   - Loads/stores kite_access_token and kite_token_date from user's DB record
   - Each request resolves the current user's Kite session
5. New endpoints:
   - GET /api/kite/connect — Returns {"login_url": "https://kite.zerodha.com/connect/login?v=3&api_key=XXX"}
   - GET /api/kite/callback?request_token=XXX — Exchanges request_token for access_token via KiteConnect.generate_session(), stores in user's DB record, redirects to /settings
   - GET /api/kite/status — Returns connection status for current user
   - POST /api/kite/disconnect — Clears user's Kite session

FRONTEND changes to Settings.jsx:
1. Remove API key/secret/TOTP input fields
2. Add "Connect to Zerodha" button that opens the login_url
3. Show connection status: "Connected as {kite_user_id}" or "Not connected"
4. Add "Disconnect" button
5. Handle the OAuth callback redirect (Kite redirects to your app's callback URL)

Update app.py:
- All endpoints that use kite_service should resolve the per-user KiteService from the current JWT user
- If user has no Kite connection, fall back to Yahoo Finance / simulation data (existing behavior)
```

### Phase 3: Strategy Builder (Replace Scanner)

```
You are working on the YieldEngine monorepo at C:\Users\modes\codebase\GBX.YieldEngine.

Read CHANGES_NEEDED.md for context. Phases 1-2 are complete.

TASK: Implement Phase 3 — Replace Scanner with user-driven Strategy Builder.

REMOVE (these trigger SEBI RA registration requirements):
- Backend: Remove strategy_engine.py entirely (auto-recommendation engine)
- Backend: Remove /api/scan and /api/recommendations endpoints from app.py
- Backend: Remove all safety_tag, rank, classify_safety logic
- Frontend: Remove Scanner.jsx
- Frontend: Remove Arbitrage.jsx (or convert to data-only comparison)
- Remove dry_run_validator.py and reconciliation.py (no execution)
- Remove /api/execute, /api/positions/<id>/close, /api/positions/<id>/roll from app.py
- Remove place_order() and place_gtt() from kite_service.py
- Remove READONLY/EXECUTE permission toggle

CREATE new backend (strategy_analyzer.py):
```python
# User-driven analysis — no auto-recommendations

POST /api/strategy/analyze
  Input: { symbol, strategy_type, expiry, strikes: [{strike, option_type, action}], lots }
  Output: {
    legs: [{strike, option_type, action, premium, greeks}],
    payoff: [{price, pnl}],  # payoff at various underlying prices
    max_profit, max_loss, breakeven: [float],
    margin_needed, estimated_fees,
    probability_of_profit,
    annualized_return_if_otm,
    greeks_net: {delta, gamma, theta, vega}
  }

GET /api/options/chain?symbol=NIFTY&expiry=2026-04-03
  Output: Full option chain with IV, OI, volume, Greeks per strike
  Source: NSE API (live) → Kite (if connected) → Black-Scholes simulation (fallback)

GET /api/options/expiries?symbol=NIFTY
  Output: [{ expiry: "2026-04-03", dte: 8 }, ...]

GET /api/symbols/fno
  Output: [{ symbol: "RELIANCE", lot_size: 250, spot: 1413.10 }, ...]
```

CREATE new frontend pages:

StrategyBuilder.jsx (replaces Scanner.jsx):
- Step 1: Symbol selector (searchable dropdown of F&O stocks + indices)
- Step 2: Strategy template buttons (Covered Call, Cash Secured Put, Bull Put Spread, Bear Call Spread, Collar, Custom)
- Step 3: Option chain displayed — user clicks rows to select strikes
- Step 4: Analysis panel shows: payoff diagram (Recharts), Greeks, max P/L, margin, fees, probability
- Step 5: User can adjust lots/strikes, metrics update in real-time
- Match existing dark theme

OptionChain.jsx (new page):
- Full interactive option chain grid
- Expiry tabs at top
- Spot price display with source indicator
- Columns: Strike | CE Premium, IV, OI, Vol, Delta | PE Premium, IV, OI, Vol, Delta
- OI heatmap coloring
- Click on any cell to see detailed Greeks
- Add to Header.jsx nav: "Option Chain" between Holdings and Strategy Builder

IMPORTANT:
- No "Recommended", "Top Picks", "Best", "Safe/Risky" labels anywhere
- No auto-generated suggestions
- No ranking of strategies
- User drives ALL selections
- Add disclaimer footer on Strategy Builder: "Estimates based on Black-Scholes model. Not investment advice."
```

### Phase 4: Landing Page & Razorpay

```
You are working on the YieldEngine monorepo at C:\Users\modes\codebase\GBX.YieldEngine.

Read CHANGES_NEEDED.md for context. Phases 1-3 are complete.

TASK: Implement Phase 4 — Landing page and Razorpay payment integration.

BACKEND:
1. Create razorpay_service.py — Reference: GBX.InvoiceManagementApi/invoice-api/app/services/razorpay_service.py
   - Order creation (amount in paise, INR currency)
   - Signature verification (HMAC-SHA256)
   - Support vendor-specific Razorpay keys via settings
2. Create subscription_routes.py:
   - GET /api/subscription/plans — List active plans (public for landing page)
   - GET /api/subscription/status — Current user's subscription status + usage
   - POST /api/subscription/create-order — Create Razorpay order for plan upgrade
   - POST /api/subscription/verify-payment — Verify signature, activate subscription
   - GET /api/subscription/payment-history — User's payment history
3. Seed default plans in init_db():
   - Trial: 14 days, free, auto-assigned on signup
   - Basic: Rs.299/month, 5 symbols
   - Pro: Rs.799/month, unlimited symbols, advanced analytics
   - Annual: Rs.5,999/year, same as Pro

FRONTEND:
1. Create pages/LandingPage.jsx (public, no auth required):
   - Hero: "Options Analytics for Smarter Decisions" with gradient text
   - Feature cards: Portfolio Intelligence, Strategy Builder, Option Chain, Risk Analytics
   - Pricing section: 3 plan cards (Basic, Pro, Annual) with features list
   - CTA: "Start Free Trial" → /signup
   - Footer: Disclaimer text, links to Terms/Privacy
   - Match dark theme but more marketing-oriented (wider layout, bigger text)

2. Create pages/Subscription.jsx (authenticated):
   - Current plan display with usage stats
   - Upgrade/downgrade plan cards
   - Razorpay checkout popup integration
   - Payment history table
   - Reference: GBX.InvoiceManagementUI/invoice-portal/src/app/(vendor)/subscription/page.tsx

3. Update App.jsx:
   - Route "/" → LandingPage (public)
   - Route "/dashboard" → Portfolio Overview (protected)
   - Route "/login", "/signup" → Public auth pages

4. Add env vars: RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET

DISCLAIMERS (add as a shared component):
- Footer disclaimer on every page: "YieldEngine is an analytics tool for informational purposes only. Not investment advice."
- Strategy Builder disclaimer: "Computed using Black-Scholes model. Estimates only."
- Terms of Service page (basic template)
- Privacy Policy page (basic template)
```

### Phase 5: Polish & Deploy

```
You are working on the YieldEngine monorepo at C:\Users\modes\codebase\GBX.YieldEngine.

Read CHANGES_NEEDED.md for context. Phases 1-4 are complete.

TASK: Final polish, testing, and deployment.

1. Test all auth flows: signup → trial → login → refresh → logout → forgot password
2. Test Kite OAuth: connect → import holdings → disconnect → reconnect
3. Test Strategy Builder: select symbol → select strategy → analyze → change strikes
4. Test subscription: trial expiry → upgrade via Razorpay → plan change
5. Test data isolation: user A cannot see user B's holdings/settings
6. Update Dockerfile for PostgreSQL client library (psycopg2-binary)
7. Create Azure PostgreSQL Flexible Server (B1ms, Central India)
8. Update CI/CD with DATABASE_URL, RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET env vars
9. Deploy and run database migrations
10. Set up custom domain (optional)
11. Update MEMORY.md with new project status
```

---

## Reference Architecture

```
                    ┌─────────────────┐
                    │  Landing Page   │ (public)
                    │  Login/Signup   │
                    └────────┬────────┘
                             │ JWT
                    ┌────────▼────────┐
                    │   React SPA     │
                    │  (Protected)    │
                    └────────┬────────┘
                             │ API calls + Bearer token
                    ┌────────▼────────┐
                    │   Flask API     │
                    │  (Auth + Data)  │
                    └──┬─────┬────┬───┘
                       │     │    │
              ┌────────▼┐ ┌──▼──┐ ┌▼────────┐
              │PostgreSQL│ │Yahoo│ │Kite OAuth│
              │ (Users,  │ │Fin  │ │(per-user │
              │ Holdings)│ │     │ │ session) │
              └──────────┘ └─────┘ └──────────┘
```
