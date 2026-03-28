"""
Kite Connect wrapper for Yield Engine.

Handles:
- Per-user KiteConnect sessions via OAuth (no stored passwords)
- Simulation mode fallback when Kite is not connected
- Holdings, quotes, and option chain retrieval (live + simulated)
"""

import logging
import math
import os
import random
from datetime import datetime, date, timedelta

from models import (
    get_db,
    generate_id,
    get_user_kite_token,
    get_user_kite_credentials,
    SIMULATION_STOCKS,
    SIMULATION_INDICES,
)
from black_scholes import option_price, compute_greeks, RISK_FREE_RATE
import live_price_service

logger = logging.getLogger(__name__)

# Fallback app-level credentials (used if no per-user credentials)
_FALLBACK_API_KEY = os.getenv("KITE_API_KEY", "")
_FALLBACK_API_SECRET = os.getenv("KITE_API_SECRET", "")


def _resolve_kite_credentials(user_id: str = None) -> tuple[str, str]:
    """Resolve Kite API key and secret: per-user first, then env fallback."""
    if user_id:
        creds = get_user_kite_credentials(user_id)
        if creds:
            return creds["kite_api_key"], creds["kite_api_secret"]
    return _FALLBACK_API_KEY, _FALLBACK_API_SECRET


def _log_notification(severity: str, title: str, message: str, user_id: str = None) -> None:
    """Insert an auth event into the notifications table."""
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO notifications (id, type, title, message, severity, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (generate_id(), "KITE_AUTH", title, message, severity, user_id or ""),
        )
        conn.commit()
    except Exception as exc:
        logger.error("Failed to log notification: %s", exc)


def get_kite_for_user(user_id: str) -> "KiteService":
    """Factory: construct a KiteService for the given user, loading their token from DB.
    Tries the token even if from yesterday — Kite tokens sometimes work past midnight."""
    api_key, _ = _resolve_kite_credentials(user_id)
    token_data = get_user_kite_token(user_id)
    if token_data and token_data["kite_access_token"]:
        # Try the token regardless of date — KiteService._setup_kite will validate
        return KiteService(
            access_token=token_data["kite_access_token"],
            kite_user_id=token_data["kite_user_id"],
            api_key=api_key,
        )
    return KiteService()  # simulation mode


def get_login_url(user_id: str = None) -> str | None:
    """Return the Kite login URL using per-user or app-level credentials."""
    api_key, _ = _resolve_kite_credentials(user_id)
    if not api_key:
        return None
    try:
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=api_key)
        return kite.login_url()
    except Exception:
        return f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"


def exchange_request_token(request_token: str, user_id: str = None) -> dict:
    """Exchange a Kite request_token for access_token using per-user or app credentials.
    Returns {"access_token": str, "user_id": str}."""
    api_key, api_secret = _resolve_kite_credentials(user_id)
    if not api_key or not api_secret:
        raise ValueError("Kite API key and secret not configured")
    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=api_key)
    data = kite.generate_session(request_token, api_secret=api_secret)
    return {
        "access_token": data["access_token"],
        "user_id": str(data.get("user_id", "")),
    }


class KiteService:
    """
    Per-user Kite Connect wrapper.

    Constructed via get_kite_for_user(user_id) factory which loads the
    user's access_token from DB. If no token, operates in simulation mode.
    """

    def __init__(self, access_token: str = None, kite_user_id: str = None, api_key: str = None):
        self._kite = None
        self._access_token: str = access_token or ""
        self._token_date: str = date.today().isoformat() if access_token else ""
        self._simulation_mode: bool = not bool(access_token)
        self._user_id: str = kite_user_id or ""
        self._api_key: str = api_key or _FALLBACK_API_KEY
        if access_token:
            self._setup_kite(access_token)

    def _setup_kite(self, access_token: str):
        """Initialize the KiteConnect instance with a valid token."""
        try:
            from kiteconnect import KiteConnect
            self._kite = KiteConnect(api_key=self._api_key)
            self._kite.set_access_token(access_token)
            self._simulation_mode = False
        except Exception as exc:
            logger.warning("Failed to setup Kite: %s", exc)
            self._simulation_mode = True

    # ------------------------------------------------------------------
    # Authentication helpers
    # ------------------------------------------------------------------

    def is_authenticated(self) -> bool:
        """Check whether we have a valid Kite session.
        The actual API call will fail if the token is expired — no date check needed.
        _setup_kite() sets _simulation_mode=True if token is invalid."""
        if self._simulation_mode:
            return False
        if not self._access_token:
            return False
        return True

    @property
    def is_simulation(self) -> bool:
        return self._simulation_mode

    def logout(self) -> dict:
        """Invalidate current session and switch to simulation mode."""
        if self._kite and self._access_token:
            try:
                self._kite.invalidate_access_token(self._access_token)
            except Exception:
                pass
        self._access_token = ""
        self._simulation_mode = True
        self._kite = None
        return {"status": "disconnected"}

    # ------------------------------------------------------------------
    # Holdings
    # ------------------------------------------------------------------

    def get_holdings(self) -> list[dict]:
        """Return portfolio holdings — live or simulated."""
        if self.is_authenticated():
            try:
                return self._kite.holdings()
            except Exception as exc:
                logger.error("Failed to fetch holdings: %s", exc)
                _log_notification("ERROR", "Holdings Error", str(exc))
                return self._simulated_holdings()
        return self._simulated_holdings()

    # ------------------------------------------------------------------
    # Quotes
    # ------------------------------------------------------------------

    def get_quote(self, instruments: list[str]) -> dict:
        """
        Fetch live quotes or return simulated quotes.
        instruments: list of exchange:tradingsymbol (e.g. ["NSE:RELIANCE", "NSE:NIFTY 50"])
        """
        if self.is_authenticated():
            try:
                return self._kite.quote(instruments)
            except Exception as exc:
                logger.error("Failed to fetch quotes: %s", exc)
                return self._simulated_quotes(instruments)
        return self._simulated_quotes(instruments)

    def get_ltp(self, instruments: list[str]) -> dict:
        """Fetch last traded price — live or simulated."""
        if self.is_authenticated():
            try:
                return self._kite.ltp(instruments)
            except Exception as exc:
                logger.error("Failed to fetch LTP: %s", exc)
                return self._simulated_ltp(instruments)
        return self._simulated_ltp(instruments)

    # ------------------------------------------------------------------
    # Option chain
    # ------------------------------------------------------------------

    def get_option_chain(
        self,
        symbol: str,
        expiry: str | None = None,
        num_strikes: int = 10,
    ) -> dict:
        """
        Get option chain for a symbol.

        In live mode: fetches instruments + quotes from Kite.
        In simulation mode: generates a realistic chain using Black-Scholes.

        Args:
            symbol: underlying symbol (e.g. "RELIANCE", "NIFTY", "BANKNIFTY")
            expiry: expiry date in YYYY-MM-DD format (default: nearest weekly/monthly)
            num_strikes: number of strikes above and below ATM
        """
        if self.is_authenticated():
            return self._live_option_chain(symbol, expiry, num_strikes)
        return self.generate_simulated_option_chain(symbol, expiry, num_strikes)

    # ------------------------------------------------------------------
    # Order placement (live mode only, with safety checks)
    # ------------------------------------------------------------------

    def place_order(self, **kwargs) -> dict:
        """Place an order through Kite. Simulation mode returns a mock order ID."""
        if self.is_authenticated():
            try:
                order_id = self._kite.place_order(
                    variety=kwargs.get("variety", "regular"),
                    exchange=kwargs.get("exchange", "NFO"),
                    tradingsymbol=kwargs["tradingsymbol"],
                    transaction_type=kwargs["transaction_type"],
                    quantity=kwargs["quantity"],
                    product=kwargs.get("product", "NRML"),
                    order_type=kwargs.get("order_type", "LIMIT"),
                    price=kwargs.get("price"),
                    trigger_price=kwargs.get("trigger_price"),
                    validity=kwargs.get("validity", "DAY"),
                    tag=kwargs.get("tag", "YieldEngine"),
                )
                return {"success": True, "order_id": order_id}
            except Exception as exc:
                logger.error("Order placement failed: %s", exc)
                return {"success": False, "error": str(exc)}

        # Simulation mode — return mock
        mock_id = f"SIM-{generate_id()[:8].upper()}"
        logger.info("Simulated order placed: %s | %s", mock_id, kwargs.get("tradingsymbol"))
        return {"success": True, "order_id": mock_id, "simulated": True}

    # ------------------------------------------------------------------
    # GTT (Good Till Triggered) orders
    # ------------------------------------------------------------------

    def place_gtt(self, **kwargs) -> dict:
        """Place a GTT order — live or simulated."""
        if self.is_authenticated():
            try:
                gtt_id = self._kite.place_gtt(
                    trigger_type=kwargs.get("trigger_type", "single"),
                    tradingsymbol=kwargs["tradingsymbol"],
                    exchange=kwargs.get("exchange", "NFO"),
                    trigger_values=kwargs["trigger_values"],
                    last_price=kwargs["last_price"],
                    orders=kwargs["orders"],
                )
                return {"success": True, "gtt_id": gtt_id}
            except Exception as exc:
                logger.error("GTT placement failed: %s", exc)
                return {"success": False, "error": str(exc)}

        mock_id = random.randint(10000000, 99999999)
        return {"success": True, "gtt_id": mock_id, "simulated": True}

    # ------------------------------------------------------------------
    # Margins
    # ------------------------------------------------------------------

    def get_margins(self) -> dict:
        """Fetch account margins — live or simulated."""
        if self.is_authenticated():
            try:
                return self._kite.margins()
            except Exception as exc:
                logger.error("Failed to fetch margins: %s", exc)
                return self._simulated_margins()
        return self._simulated_margins()

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def get_positions(self) -> dict:
        """Fetch open positions — live or simulated."""
        if self.is_authenticated():
            try:
                return self._kite.positions()
            except Exception as exc:
                logger.error("Failed to fetch positions: %s", exc)
                return {"net": [], "day": []}
        return {"net": [], "day": []}

    # ------------------------------------------------------------------
    # Instruments
    # ------------------------------------------------------------------

    def get_instruments(self, exchange: str = "NFO") -> list[dict]:
        """Fetch instruments list from Kite."""
        if self.is_authenticated():
            try:
                return self._kite.instruments(exchange)
            except Exception as exc:
                logger.error("Failed to fetch instruments: %s", exc)
                return []
        return []

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Current connection status for the frontend."""
        return self._status_dict(
            login_url=self.get_login_url() if not self.is_authenticated() else None,
            message="Connected" if self.is_authenticated() else "Simulation mode",
        )

    # ======================================================================
    # PRIVATE METHODS
    # ======================================================================

    def _get_kite_instance(self):
        """Lazy-create the KiteConnect instance."""
        if self._kite is None:
            try:
                from kiteconnect import KiteConnect
                self._kite = KiteConnect(api_key=KITE_API_KEY)
            except ImportError:
                logger.error("kiteconnect package not installed. pip install kiteconnect")
                raise
        return self._kite

    # ======================================================================
    # SIMULATION HELPERS
    # ======================================================================

    @staticmethod
    def _jitter(value: float, pct: float = 0.02) -> float:
        """Add small random jitter to a value to simulate market movement."""
        return round(value * (1 + random.uniform(-pct, pct)), 2)

    def _simulated_holdings(self) -> list[dict]:
        """Generate simulated holdings, using live prices where available."""
        # Batch fetch live prices for all simulation stocks
        live_prices = live_price_service.fetch_spot_prices_batch(list(SIMULATION_STOCKS.keys()))

        holdings = []
        for symbol, data in SIMULATION_STOCKS.items():
            live = live_prices.get(symbol)
            if live:
                ltp = live["ltp"]
                close_price = live["close"]
                day_change = round(ltp - close_price, 2)
                day_change_pct = round((ltp - close_price) / close_price * 100, 2) if close_price else 0
                source = "yahoo"
            else:
                ltp = self._jitter(data["ltp"])
                close_price = self._jitter(data["ltp"], 0.005)
                day_change = round(random.uniform(-2.5, 2.5), 2)
                day_change_pct = round(random.uniform(-1.5, 1.5), 2)
                source = "simulated"

            avg_price = self._jitter(data["ltp"], 0.08)  # bought at +/- 8%
            qty = data["lotSize"]
            holdings.append({
                "tradingsymbol": symbol,
                "exchange": "NSE",
                "isin": f"SIM{symbol[:6].upper()}",
                "quantity": qty,
                "average_price": avg_price,
                "last_price": ltp,
                "pnl": round((ltp - avg_price) * qty, 2),
                "day_change": day_change,
                "day_change_percentage": day_change_pct,
                "collateral_quantity": qty,
                "collateral_type": "margin",
                "t1_quantity": 0,
                "close_price": close_price,
                "used_quantity": 0,
                "price_source": source,
            })
        return holdings

    def _simulated_quotes(self, instruments: list[str]) -> dict:
        """Fetch live quotes where possible, fall back to simulated data."""
        quotes = {}
        for inst in instruments:
            parts = inst.split(":")
            symbol = parts[1] if len(parts) > 1 else parts[0]
            symbol_clean = symbol.replace(" ", "").upper()

            # Try live quote first
            live = live_price_service.get_live_quote(symbol_clean)
            if live is not None:
                ltp = live["ltp"]
                quotes[inst] = {
                    "instrument_token": abs(hash(symbol)) % 10000000,
                    "last_price": ltp,
                    "ohlc": {
                        "open": live["open"],
                        "high": live["high"],
                        "low": live["low"],
                        "close": live["close"],
                    },
                    "volume": live["volume"],
                    "oi": 0,
                    "depth": {"buy": [], "sell": []},
                    "change": round(ltp - live["close"], 2),
                    "price_source": "yahoo",
                }
                continue

            # Fallback to hardcoded jitter
            if symbol_clean in SIMULATION_STOCKS:
                data = SIMULATION_STOCKS[symbol_clean]
                ltp = self._jitter(data["ltp"])
            elif symbol_clean in ("NIFTY50", "NIFTY"):
                data = SIMULATION_INDICES["NIFTY"]
                ltp = self._jitter(data["spot"])
            elif symbol_clean in ("BANKNIFTY", "NIFTYBANK"):
                data = SIMULATION_INDICES["BANKNIFTY"]
                ltp = self._jitter(data["spot"])
            else:
                ltp = self._jitter(1000.0, 0.05)
                data = {"iv": 0.25}

            quotes[inst] = {
                "instrument_token": abs(hash(symbol)) % 10000000,
                "last_price": ltp,
                "ohlc": {
                    "open": self._jitter(ltp, 0.005),
                    "high": self._jitter(ltp, 0.015),
                    "low": self._jitter(ltp, -0.015),
                    "close": self._jitter(ltp, 0.003),
                },
                "volume": random.randint(100000, 5000000),
                "oi": random.randint(10000, 500000),
                "depth": {"buy": [], "sell": []},
                "change": round(random.uniform(-30, 30), 2),
                "price_source": "simulated",
            }
        return quotes

    def _simulated_ltp(self, instruments: list[str]) -> dict:
        """Fetch live LTP where possible, fall back to simulated jitter."""
        result = {}
        for inst in instruments:
            parts = inst.split(":")
            symbol = parts[1] if len(parts) > 1 else parts[0]
            symbol_clean = symbol.replace(" ", "").upper()

            # Try live price first
            live = live_price_service.get_live_spot(symbol_clean)
            if live is not None:
                result[inst] = {
                    "instrument_token": abs(hash(symbol)) % 10000000,
                    "last_price": live,
                    "price_source": "yahoo",
                }
                continue

            # Fallback to hardcoded jitter
            if symbol_clean in SIMULATION_STOCKS:
                ltp = self._jitter(SIMULATION_STOCKS[symbol_clean]["ltp"])
            elif symbol_clean in ("NIFTY50", "NIFTY"):
                ltp = self._jitter(SIMULATION_INDICES["NIFTY"]["spot"])
            elif symbol_clean in ("BANKNIFTY", "NIFTYBANK"):
                ltp = self._jitter(SIMULATION_INDICES["BANKNIFTY"]["spot"])
            else:
                ltp = self._jitter(1000.0, 0.05)

            result[inst] = {
                "instrument_token": abs(hash(symbol)) % 10000000,
                "last_price": ltp,
                "price_source": "simulated",
            }
        return result

    @staticmethod
    def _simulated_margins() -> dict:
        """Simulated margin data."""
        return {
            "equity": {
                "enabled": True,
                "net": 1500000.0,
                "available": {
                    "live_balance": 500000.0,
                    "cash": 500000.0,
                    "collateral": 1000000.0,
                    "intraday_payin": 0,
                    "adhoc_margin": 0,
                },
                "utilised": {
                    "debits": 0,
                    "exposure": 200000.0,
                    "span": 150000.0,
                    "option_premium": 0,
                    "holding_sales": 0,
                    "turnover": 0,
                },
            },
            "commodity": {"enabled": False},
            "simulated": True,
        }

    # ======================================================================
    # SIMULATED OPTION CHAIN
    # ======================================================================

    def generate_simulated_option_chain(
        self,
        symbol: str,
        expiry: str | None = None,
        num_strikes: int = 10,
    ) -> dict:
        """
        Get option chain — tries NSE live data first, then generates
        a realistic chain using Black-Scholes with live spot from Yahoo.

        Returns a dict with:
            - symbol, spot, expiry, dte, price_source
            - strikes: list of {strike, CE: {premium, greeks...}, PE: {premium, greeks...}}
        """
        symbol_upper = symbol.upper()

        # ── Try NSE live option chain first ──
        nse_chain = live_price_service.get_live_option_chain(symbol_upper, expiry, num_strikes)
        if nse_chain and nse_chain.get("strikes"):
            # Enrich with Greeks (NSE doesn't provide them)
            spot = nse_chain["spot"]
            dte = nse_chain["dte"]
            T = dte / 365.0
            for strike_row in nse_chain["strikes"]:
                strike = strike_row["strike"]
                for opt_type in ("CE", "PE"):
                    opt = strike_row.get(opt_type)
                    if not opt:
                        continue
                    iv_decimal = opt.get("iv", 0) / 100.0 if opt.get("iv", 0) > 0 else 0.20
                    greeks = compute_greeks(spot, strike, T, RISK_FREE_RATE, iv_decimal, opt_type)
                    opt["greeks"] = {
                        "delta": round(greeks["delta"], 4),
                        "gamma": round(greeks["gamma"], 6),
                        "theta": round(greeks["theta"], 4),
                        "vega": round(greeks["vega"], 4),
                    }
                    opt["prob_otm"] = round(greeks["prob_otm"], 4)
            logger.info("Using NSE live option chain for %s (%d strikes)", symbol_upper, len(nse_chain["strikes"]))
            return nse_chain

        # ── Fallback: Black-Scholes generation with live spot ──

        # Try live spot price from Yahoo
        live_spot = live_price_service.get_live_spot(symbol_upper)
        spot_source = "yahoo" if live_spot else "simulated"

        # Determine spot price, IV, lot size
        if symbol_upper in SIMULATION_STOCKS:
            data = SIMULATION_STOCKS[symbol_upper]
            spot = live_spot if live_spot else self._jitter(data["ltp"])
            base_iv = data["iv"]
            lot_size = data["lotSize"]
            strike_gap = self._compute_strike_gap(spot, is_index=False)
        elif symbol_upper in SIMULATION_INDICES:
            data = SIMULATION_INDICES[symbol_upper]
            spot = live_spot if live_spot else self._jitter(data["spot"])
            base_iv = data["iv"]
            lot_size = data["lotSize"]
            strike_gap = self._compute_strike_gap(spot, is_index=True)
        else:
            spot = live_spot if live_spot else 1000.0
            base_iv = 0.25
            lot_size = 100
            strike_gap = self._compute_strike_gap(spot, is_index=False)

        # Determine expiry and DTE
        if expiry:
            try:
                expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
            except ValueError:
                expiry_date = self._next_thursday()
        else:
            expiry_date = self._next_thursday()

        dte = max(1, (expiry_date - date.today()).days)
        T = dte / 365.0  # time to expiry in years

        # ATM strike
        atm_strike = round(spot / strike_gap) * strike_gap

        # Generate strikes around ATM
        strikes_data = []
        for i in range(-num_strikes, num_strikes + 1):
            strike = atm_strike + i * strike_gap

            if strike <= 0:
                continue

            # IV smile: higher IV for deep OTM/ITM, lower for ATM
            moneyness = abs(spot - strike) / spot
            iv_smile_adj = 1.0 + moneyness * 1.5  # simple smile
            iv_ce = base_iv * iv_smile_adj * random.uniform(0.95, 1.05)
            iv_pe = base_iv * iv_smile_adj * random.uniform(0.95, 1.05)

            # Ensure minimum IV
            iv_ce = max(0.05, iv_ce)
            iv_pe = max(0.05, iv_pe)

            # Compute Greeks via Black-Scholes
            ce_greeks = compute_greeks(spot, strike, T, RISK_FREE_RATE, iv_ce, "CE")
            pe_greeks = compute_greeks(spot, strike, T, RISK_FREE_RATE, iv_pe, "PE")

            # Simulate bid-ask spread (tighter for ATM, wider for OTM)
            spread_pct = 0.005 + moneyness * 0.02
            ce_premium = round(max(0.05, ce_greeks["price"]), 2)
            pe_premium = round(max(0.05, pe_greeks["price"]), 2)

            # Simulate OI (higher near ATM)
            oi_factor = max(0.1, 1.0 - moneyness * 3)
            ce_oi = int(random.uniform(5000, 200000) * oi_factor)
            pe_oi = int(random.uniform(5000, 200000) * oi_factor)
            ce_volume = int(random.uniform(1000, 50000) * oi_factor)
            pe_volume = int(random.uniform(1000, 50000) * oi_factor)

            strikes_data.append({
                "strike": strike,
                "CE": {
                    "tradingsymbol": f"{symbol_upper}{expiry_date.strftime('%y%b').upper()}{int(strike)}CE",
                    "premium": ce_premium,
                    "bid": round(ce_premium * (1 - spread_pct), 2),
                    "ask": round(ce_premium * (1 + spread_pct), 2),
                    "iv": round(iv_ce * 100, 2),  # as percentage
                    "oi": ce_oi,
                    "volume": ce_volume,
                    "lot_size": lot_size,
                    "greeks": {
                        "delta": round(ce_greeks["delta"], 4),
                        "gamma": round(ce_greeks["gamma"], 6),
                        "theta": round(ce_greeks["theta"], 4),
                        "vega": round(ce_greeks["vega"], 4),
                    },
                    "prob_otm": round(ce_greeks["prob_otm"], 4),
                    "price_source": spot_source,
                },
                "PE": {
                    "tradingsymbol": f"{symbol_upper}{expiry_date.strftime('%y%b').upper()}{int(strike)}PE",
                    "premium": pe_premium,
                    "bid": round(pe_premium * (1 - spread_pct), 2),
                    "ask": round(pe_premium * (1 + spread_pct), 2),
                    "iv": round(iv_pe * 100, 2),
                    "oi": pe_oi,
                    "volume": pe_volume,
                    "lot_size": lot_size,
                    "greeks": {
                        "delta": round(pe_greeks["delta"], 4),
                        "gamma": round(pe_greeks["gamma"], 6),
                        "theta": round(pe_greeks["theta"], 4),
                        "vega": round(pe_greeks["vega"], 4),
                    },
                    "prob_otm": round(pe_greeks["prob_otm"], 4),
                    "price_source": spot_source,
                },
            })

        return {
            "symbol": symbol_upper,
            "spot": round(spot, 2),
            "expiry": expiry_date.isoformat(),
            "dte": dte,
            "lot_size": lot_size,
            "strike_gap": strike_gap,
            "atm_strike": atm_strike,
            "strikes": strikes_data,
            "price_source": spot_source,
        }

    def _live_option_chain(
        self,
        symbol: str,
        expiry: str | None,
        num_strikes: int,
    ) -> dict:
        """Fetch a live option chain from Kite instruments + quotes."""
        try:
            # Fetch NFO instruments
            instruments = self._kite.instruments("NFO")

            # Filter for the symbol
            symbol_upper = symbol.upper()
            target_expiry = None
            if expiry:
                target_expiry = datetime.strptime(expiry, "%Y-%m-%d").date()

            # Find relevant options
            options = [
                i for i in instruments
                if i.get("name", "").upper() == symbol_upper
                and i.get("instrument_type") in ("CE", "PE")
            ]

            if not options:
                logger.warning("No instruments found for %s on NFO. Falling back to simulation.", symbol)
                return self.generate_simulated_option_chain(symbol, expiry, num_strikes)

            # If no expiry specified, find the nearest one
            if not target_expiry:
                expiries = sorted(set(i["expiry"] for i in options if i.get("expiry")))
                today = date.today()
                future_expiries = [e for e in expiries if e >= today]
                target_expiry = future_expiries[0] if future_expiries else expiries[-1]

            # Filter to target expiry
            chain_instruments = [
                i for i in options
                if i.get("expiry") == target_expiry
            ]

            if not chain_instruments:
                return self.generate_simulated_option_chain(symbol, expiry, num_strikes)

            # Get spot price
            spot_symbol = f"NSE:{symbol_upper}" if symbol_upper in SIMULATION_STOCKS else f"NSE:{symbol_upper} 50"
            try:
                spot_quote = self._kite.ltp([spot_symbol])
                spot = list(spot_quote.values())[0]["last_price"]
            except Exception:
                spot = chain_instruments[0].get("last_price", 0)

            # Get all strikes, select num_strikes around ATM
            all_strikes = sorted(set(i["strike"] for i in chain_instruments))
            atm_idx = min(range(len(all_strikes)), key=lambda idx: abs(all_strikes[idx] - spot))
            start = max(0, atm_idx - num_strikes)
            end = min(len(all_strikes), atm_idx + num_strikes + 1)
            selected_strikes = all_strikes[start:end]

            # Fetch quotes for selected instruments
            selected = [
                i for i in chain_instruments
                if i["strike"] in selected_strikes
            ]
            inst_tokens = [f"NFO:{i['tradingsymbol']}" for i in selected]

            # Kite quote API has a limit — batch if needed
            quotes = {}
            batch_size = 200
            for b in range(0, len(inst_tokens), batch_size):
                batch = inst_tokens[b : b + batch_size]
                quotes.update(self._kite.quote(batch))

            # Assemble the chain
            strike_map: dict[float, dict] = {}
            for i in selected:
                s = i["strike"]
                opt_type = i["instrument_type"]
                ts = i["tradingsymbol"]
                q = quotes.get(f"NFO:{ts}", {})

                if s not in strike_map:
                    strike_map[s] = {"strike": s}

                strike_map[s][opt_type] = {
                    "tradingsymbol": ts,
                    "premium": q.get("last_price", 0),
                    "bid": q.get("depth", {}).get("buy", [{}])[0].get("price", 0) if q.get("depth") else 0,
                    "ask": q.get("depth", {}).get("sell", [{}])[0].get("price", 0) if q.get("depth") else 0,
                    "iv": round(q.get("oi", 0), 2),  # IV not directly in quote; placeholder
                    "oi": q.get("oi", 0),
                    "volume": q.get("volume", 0),
                    "lot_size": i.get("lot_size", 1),
                    "simulated": False,
                }

            dte = max(1, (target_expiry - date.today()).days)
            strike_gap = (selected_strikes[1] - selected_strikes[0]) if len(selected_strikes) > 1 else 50
            atm_strike = min(selected_strikes, key=lambda s: abs(s - spot))

            return {
                "symbol": symbol_upper,
                "spot": round(spot, 2),
                "expiry": target_expiry.isoformat(),
                "dte": dte,
                "lot_size": selected[0].get("lot_size", 1) if selected else 1,
                "strike_gap": strike_gap,
                "atm_strike": atm_strike,
                "strikes": [strike_map[s] for s in sorted(strike_map.keys())],
                "simulated": False,
            }

        except Exception as exc:
            logger.error("Live option chain fetch failed: %s — falling back to simulation", exc)
            _log_notification("WARNING", "Option Chain Fallback", f"Using simulation: {exc}")
            return self.generate_simulated_option_chain(symbol, expiry, num_strikes)

    @staticmethod
    def _compute_strike_gap(spot: float, is_index: bool) -> float:
        """Compute a realistic strike interval based on the underlying price."""
        if is_index:
            if spot > 40000:
                return 100
            return 50
        if spot > 5000:
            return 100
        if spot > 2000:
            return 50
        if spot > 500:
            return 20
        if spot > 100:
            return 10
        return 5

    @staticmethod
    def _next_thursday() -> date:
        """Find the next Thursday (standard NSE weekly expiry)."""
        today = date.today()
        days_ahead = 3 - today.weekday()  # Thursday = 3
        if days_ahead <= 0:
            days_ahead += 7
        return today + timedelta(days=days_ahead)


# Module-level singleton removed — use get_kite_for_user(user_id) factory
