"""
Market data service — fetches real instrument data from Kite when connected.
Provides: available expiries, option chains with real prices, lot sizes.
Falls back to simulation data when Kite is not connected.
"""

import logging
from datetime import date, datetime, timedelta
from functools import lru_cache
import time

logger = logging.getLogger(__name__)

# Cache for instruments (refreshed every 6 hours — instruments don't change intraday)
_instruments_cache = {"data": None, "timestamp": 0}
INSTRUMENTS_CACHE_TTL = 6 * 3600  # 6 hours

# Debug log for diagnosing instrument fetch issues
_last_debug = {"log": []}

def get_debug_log():
    """Return the last instrument fetch debug log."""
    return _last_debug.get("log", [])


def get_nfo_instruments(kite_service):
    """Fetch all NFO instruments from Kite. Cached for 6 hours.
    Returns list of instrument dicts or empty list if not connected."""
    import sys
    now = time.time()
    if _instruments_cache["data"] and (now - _instruments_cache["timestamp"]) < INSTRUMENTS_CACHE_TTL:
        print(f"[MARKET_DATA] Using cached instruments ({len(_instruments_cache['data'])} instruments)", file=sys.stderr, flush=True)
        return _instruments_cache["data"]

    _debug_log = []

    if not kite_service:
        _debug_log.append("No kite_service provided")
        _last_debug["log"] = _debug_log
        return []

    if not kite_service.is_authenticated():
        _debug_log.append(f"Kite not authenticated. simulation={kite_service.is_simulation}, has_token={bool(kite_service._access_token)}")
        _last_debug["log"] = _debug_log
        return []

    try:
        _debug_log.append("Fetching NFO instruments from Kite...")
        instruments = kite_service.get_instruments("NFO")
        if instruments:
            _instruments_cache["data"] = instruments
            _instruments_cache["timestamp"] = now
            _debug_log.append(f"Fetched {len(instruments)} NFO instruments")
        else:
            _debug_log.append("Kite returned 0 instruments")
        _last_debug["log"] = _debug_log
        return instruments
    except Exception as e:
        _debug_log.append(f"FAILED: {type(e).__name__}: {e}")
        _last_debug["log"] = _debug_log
        return _instruments_cache.get("data") or []


def get_available_expiries(kite_service, symbol):
    """Get available expiry dates for a symbol from Kite instruments.
    Returns sorted list of date objects (nearest first)."""
    instruments = get_nfo_instruments(kite_service)
    if not instruments:
        # No instruments available — return empty; simulation mode handles its own fallback
        return []

    symbol_upper = symbol.upper()
    today = date.today()

    # Filter for this symbol's options
    expiries = set()
    for inst in instruments:
        if (inst.get("name", "").upper() == symbol_upper and
            inst.get("instrument_type") in ("CE", "PE") and
            inst.get("expiry")):
            exp = inst["expiry"]
            if isinstance(exp, str):
                exp = datetime.strptime(exp, "%Y-%m-%d").date()
            if exp >= today:
                expiries.add(exp)

    return sorted(expiries)


def get_expiries_within_days(kite_service, symbol, max_days=30):
    """Get all available expiries within max_days from today."""
    expiries = get_available_expiries(kite_service, symbol)
    today = date.today()
    return [exp for exp in expiries if 0 < (exp - today).days <= max_days]


def get_nearest_expiry(kite_service, symbol, min_dte=2):
    """Get the nearest available expiry for a symbol with at least min_dte days.
    On weekends, min_dte=0 so we show next week's options for analysis."""
    expiries = get_available_expiries(kite_service, symbol)
    today = date.today()
    is_weekend = today.weekday() >= 5  # Sat=5, Sun=6

    # On weekends, show all future expiries (traders analyze over weekend)
    effective_min_dte = 0 if is_weekend else min_dte

    for exp in expiries:
        dte = (exp - today).days
        if dte >= effective_min_dte:
            return exp

    # If all expiries are too close, return the last one anyway
    if expiries:
        return expiries[-1]
    return _next_thursday()


def get_lot_size(kite_service, symbol):
    """Get actual lot size from Kite instruments."""
    instruments = get_nfo_instruments(kite_service)
    symbol_upper = symbol.upper()

    for inst in instruments:
        if inst.get("name", "").upper() == symbol_upper and inst.get("lot_size"):
            return inst["lot_size"]

    # Fallback to hardcoded
    from models import SIMULATION_INDICES
    if symbol_upper in SIMULATION_INDICES:
        return SIMULATION_INDICES[symbol_upper]["lotSize"]

    from strategy_engine import _FNO_LOT_SIZES
    return _FNO_LOT_SIZES.get(symbol_upper, 0)


def get_option_chain_live(kite_service, symbol, expiry_date, num_strikes=10):
    """Fetch a real option chain from Kite with actual market prices.

    Returns dict: {
        "spot": float,
        "expiry": "YYYY-MM-DD",
        "expiry_display": "03 APR",
        "dte": int,
        "lot_size": int,
        "strike_gap": float,
        "strikes": [
            {
                "strike": float,
                "CE": {"premium": float, "oi": int, "volume": int, "bid": float, "ask": float},
                "PE": {"premium": float, "oi": int, "volume": int, "bid": float, "ask": float},
            }
        ]
    } or None if unable to fetch.
    """
    if not kite_service or not kite_service.is_authenticated():
        return None

    instruments = get_nfo_instruments(kite_service)
    if not instruments:
        return None

    symbol_upper = symbol.upper()
    today = date.today()

    # Normalize expiry_date to date object for comparison
    if isinstance(expiry_date, str):
        from datetime import datetime as dt
        expiry_date = dt.strptime(expiry_date, "%Y-%m-%d").date()

    # Filter instruments for this symbol + expiry + options only
    # Kite returns expiry as datetime.date — normalize both sides
    chain_instruments = []
    for i in instruments:
        if i.get("name", "").upper() != symbol_upper:
            continue
        if i.get("instrument_type") not in ("CE", "PE"):
            continue
        inst_expiry = i.get("expiry")
        if isinstance(inst_expiry, str):
            try:
                inst_expiry = date.fromisoformat(inst_expiry)
            except Exception:
                continue
        if inst_expiry == expiry_date:
            chain_instruments.append(i)

    # Debug: if no match, log what expiries exist
    if not chain_instruments:
        # Find what expiries actually exist for this symbol
        available = set()
        for i in instruments:
            if i.get("name", "").upper() == symbol_upper and i.get("instrument_type") in ("CE", "PE"):
                exp = i.get("expiry")
                if exp:
                    available.add(str(exp))
        _last_debug["chain_miss"] = {
            "symbol": symbol_upper,
            "requested_expiry": str(expiry_date),
            "expiry_type": type(expiry_date).__name__,
            "available_expiries": sorted(list(available))[:5],
            "sample_inst_expiry_type": type(instruments[0].get("expiry")).__name__ if instruments else "N/A",
        }
        return None

    lot_size = chain_instruments[0].get("lot_size", 1)

    # Get spot price — use correct Kite index quote names
    try:
        _INDEX_QUOTE_NAMES = {
            "NIFTY": "NSE:NIFTY 50",
            "BANKNIFTY": "NSE:NIFTY BANK",
        }
        spot_key = _INDEX_QUOTE_NAMES.get(symbol_upper, f"NSE:{symbol_upper}")
        spot_data = kite_service.get_ltp([spot_key])
        spot = list(spot_data.values())[0]["last_price"]
    except Exception:
        import live_price_service
        spot = live_price_service.get_live_spot(symbol_upper) or 0

    if not spot:
        return None

    # Get all strikes, select around ATM
    all_strikes = sorted(set(i["strike"] for i in chain_instruments))
    if not all_strikes:
        return None

    atm_idx = min(range(len(all_strikes)), key=lambda i: abs(all_strikes[i] - spot))
    start = max(0, atm_idx - num_strikes)
    end = min(len(all_strikes), atm_idx + num_strikes + 1)
    selected_strikes = set(all_strikes[start:end])

    # Filter to selected strikes
    selected = [i for i in chain_instruments if i["strike"] in selected_strikes]

    # Fetch quotes for all selected instruments
    inst_tokens = [f"NFO:{i['tradingsymbol']}" for i in selected]
    quotes = {}
    batch_size = 200
    try:
        for b in range(0, len(inst_tokens), batch_size):
            batch = inst_tokens[b:b + batch_size]
            quotes.update(kite_service.get_quote(batch))
    except Exception as e:
        logger.error("Failed to fetch option quotes: %s", e)
        return None

    # Build the chain
    strike_map = {}
    for i in selected:
        s = i["strike"]
        opt_type = i["instrument_type"]
        ts = i["tradingsymbol"]
        q = quotes.get(f"NFO:{ts}", {})

        if s not in strike_map:
            strike_map[s] = {"strike": s}

        depth_buy = q.get("depth", {}).get("buy", [{}])
        depth_sell = q.get("depth", {}).get("sell", [{}])

        strike_map[s][opt_type] = {
            "tradingsymbol": ts,
            "premium": q.get("last_price", 0),
            "bid": depth_buy[0].get("price", 0) if depth_buy else 0,
            "ask": depth_sell[0].get("price", 0) if depth_sell else 0,
            "oi": q.get("oi", 0),
            "volume": q.get("volume", 0),
            "lot_size": lot_size,
        }

    strike_gap = (all_strikes[1] - all_strikes[0]) if len(all_strikes) > 1 else 50
    dte = max(1, (expiry_date - today).days)

    return {
        "spot": round(spot, 2),
        "expiry": expiry_date.isoformat(),
        "expiry_display": expiry_date.strftime("%d %b").upper(),
        "dte": dte,
        "lot_size": lot_size,
        "strike_gap": strike_gap,
        "strikes": [strike_map[s] for s in sorted(strike_map.keys())],
    }


def has_fno_options(kite_service, symbol):
    """Check if a symbol has F&O options available."""
    instruments = get_nfo_instruments(kite_service)
    if not instruments:
        # Fallback: check hardcoded lists
        from strategy_engine import _FNO_LOT_SIZES
        from models import SIMULATION_STOCKS, SIMULATION_INDICES
        return symbol.upper() in _FNO_LOT_SIZES or symbol.upper() in SIMULATION_STOCKS or symbol.upper() in SIMULATION_INDICES

    return any(i.get("name", "").upper() == symbol.upper() and i.get("instrument_type") in ("CE", "PE") for i in instruments)


def _fallback_expiries(symbol):
    """Fallback when Kite is not connected — return empty list.
    Simulation mode handles its own expiry generation."""
    return []


# ─── RSI Calculation ──────────────────────────────────────────────────────────

_rsi_cache = {"data": {}, "timestamp": 0}
RSI_CACHE_TTL = 3600  # 1 hour — RSI doesn't change intraday much


def calculate_rsi(kite_service, symbol, period=14):
    """Calculate RSI for a symbol using Kite historical data (daily candles).
    Returns RSI value (0-100) or None if unavailable."""
    import time as _time

    cache_key = f"{symbol}_{period}"
    now = _time.time()
    if cache_key in _rsi_cache["data"] and (now - _rsi_cache["timestamp"]) < RSI_CACHE_TTL:
        return _rsi_cache["data"][cache_key]

    if not kite_service or not kite_service.is_authenticated():
        return None

    try:
        # Need instrument_token for historical data
        instruments = get_nfo_instruments(kite_service)

        # For stocks, get equity instrument token from NSE
        # Try kite.ltp first to check the symbol works
        quote_key = f"NSE:{symbol}"
        if symbol == "NIFTY":
            quote_key = "NSE:NIFTY 50"
        elif symbol in ("BANKNIFTY", "NIFTYBANK"):
            quote_key = "NSE:NIFTY BANK"

        quote = kite_service.get_quote([quote_key])
        if not quote:
            return None

        q = list(quote.values())[0]
        instrument_token = q.get("instrument_token")
        if not instrument_token:
            return None

        # Fetch last 30 days of daily candles (need period+1 extra for calculation)
        from_date = (date.today() - timedelta(days=45)).strftime("%Y-%m-%d")
        to_date = date.today().strftime("%Y-%m-%d")

        history = kite_service._kite.historical_data(
            instrument_token, from_date, to_date, "day"
        )

        if not history or len(history) < period + 1:
            return None

        # Extract closing prices
        closes = [candle["close"] for candle in history]

        # Calculate RSI
        gains = []
        losses = []
        for i in range(1, len(closes)):
            change = closes[i] - closes[i - 1]
            gains.append(max(change, 0))
            losses.append(max(-change, 0))

        if len(gains) < period:
            return None

        # Initial averages
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Smoothed averages (Wilder's method)
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = round(100 - (100 / (1 + rs)), 1)

        _rsi_cache["data"][cache_key] = rsi
        _rsi_cache["timestamp"] = now
        return rsi

    except Exception:
        return None


def get_rsi_signal(rsi):
    """Interpret RSI value for option selling.
    Returns dict with signal, action, and option_type."""
    if rsi is None:
        return None
    if rsi >= 70:
        return {
            "rsi": rsi,
            "signal": "OVERBOUGHT",
            "action": "SELL",
            "option_type": "CE",
            "label": f"RSI {rsi} — Overbought, sell calls",
        }
    if rsi <= 30:
        return {
            "rsi": rsi,
            "signal": "OVERSOLD",
            "action": "SELL",
            "option_type": "PE",
            "label": f"RSI {rsi} — Oversold, sell puts",
        }
    return {
        "rsi": rsi,
        "signal": "NEUTRAL",
        "action": None,
        "option_type": None,
        "label": f"RSI {rsi} — Neutral range",
    }


def _next_thursday():
    today = date.today()
    days_ahead = 3 - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return today + timedelta(days=days_ahead)


# ─── Stock Screening for Strangle Strategy ────────────────────────────────────

def get_52_week_high_low(kite_service, symbol):
    """Get 52-week high and low for a symbol from Kite quote.
    Returns {"high": float, "low": float, "current": float} or None."""
    if not kite_service or not kite_service.is_authenticated():
        return None
    try:
        key = f"NSE:{symbol}"
        if symbol == "NIFTY":
            key = "NSE:NIFTY 50"
        elif symbol in ("BANKNIFTY", "NIFTYBANK"):
            key = "NSE:NIFTY BANK"
        quote = kite_service.get_quote([key])
        q = list(quote.values())[0]
        ohlc = q.get("ohlc", {})
        return {
            "high": q.get("week_52_high", ohlc.get("high", 0)),
            "low": q.get("week_52_low", ohlc.get("low", 0)),
            "current": q.get("last_price", 0),
        }
    except Exception:
        return None


def is_near_52_week_high(kite_service, symbol, threshold_pct=5):
    """Check if stock is within threshold_pct% of 52-week high. Returns True if too close."""
    data = get_52_week_high_low(kite_service, symbol)
    if not data or not data["high"]:
        return False  # Can't check, allow
    distance_pct = ((data["high"] - data["current"]) / data["high"]) * 100
    return distance_pct <= threshold_pct


def get_fno_stock_list(kite_service):
    """Get list of unique F&O eligible stock symbols from Kite instruments."""
    instruments = get_nfo_instruments(kite_service)
    if not instruments:
        return []
    stocks = set()
    for inst in instruments:
        name = inst.get("name", "").upper()
        inst_type = inst.get("instrument_type", "")
        if inst_type in ("CE", "PE") and name and name not in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
            stocks.add(name)
    return sorted(stocks)


def get_strangle_chain(kite_service, symbol, expiry_date, spot, otm_pct=0.02):
    """Find OTM call and put strikes for a strangle.

    Args:
        otm_pct: how far OTM (0.02 = 2% from spot)

    Returns dict with CE and PE data or None.
    """
    chain = get_option_chain_live(kite_service, symbol, expiry_date, num_strikes=15)
    if not chain or not chain.get("strikes"):
        return None

    target_ce_strike = spot * (1 + otm_pct)  # Call above spot
    target_pe_strike = spot * (1 - otm_pct)  # Put below spot

    best_ce = None
    best_pe = None

    for s in chain["strikes"]:
        strike = s["strike"]
        ce = s.get("CE")
        pe = s.get("PE")

        # Find nearest CE strike above target
        if ce and strike >= target_ce_strike and ce.get("premium", 0) > 0:
            if ce.get("oi", 0) >= 100:
                if best_ce is None or strike < best_ce["strike"]:
                    best_ce = {**ce, "strike": strike}

        # Find nearest PE strike below target
        if pe and strike <= target_pe_strike and pe.get("premium", 0) > 0:
            if pe.get("oi", 0) >= 100:
                if best_pe is None or strike > best_pe["strike"]:
                    best_pe = {**pe, "strike": strike}

    if not best_ce or not best_pe:
        return None

    return {
        "spot": chain["spot"],
        "lot_size": chain["lot_size"],
        "expiry": chain["expiry"],
        "expiry_display": chain["expiry_display"],
        "dte": chain["dte"],
        "ce": best_ce,
        "pe": best_pe,
        "combined_premium": best_ce["premium"] + best_pe["premium"],
        "combined_premium_pct": round(((best_ce["premium"] + best_pe["premium"]) / spot) * 100, 2),
        "ce_premium_pct": round((best_ce["premium"] / best_ce["strike"]) * 100, 2),
        "pe_premium_pct": round((best_pe["premium"] / best_pe["strike"]) * 100, 2),
    }
