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

# Symbols that have weekly expiry (every Thursday) — as of Nov 2024
# NSE discontinued weekly expiry for BANKNIFTY, FINNIFTY, MIDCPNIFTY
# Only NIFTY retains weekly Thursday expiry on NSE
WEEKLY_EXPIRY_SYMBOLS = {"NIFTY"}

# All other F&O stocks have monthly expiry (last Thursday of month)

# Cache for instruments (refreshed every 6 hours — instruments don't change intraday)
_instruments_cache = {"data": None, "timestamp": 0}
INSTRUMENTS_CACHE_TTL = 6 * 3600  # 6 hours


def get_nfo_instruments(kite_service):
    """Fetch all NFO instruments from Kite. Cached for 6 hours.
    Returns list of instrument dicts or empty list if not connected."""
    import sys
    now = time.time()
    if _instruments_cache["data"] and (now - _instruments_cache["timestamp"]) < INSTRUMENTS_CACHE_TTL:
        print(f"[MARKET_DATA] Using cached instruments ({len(_instruments_cache['data'])} instruments)", file=sys.stderr, flush=True)
        return _instruments_cache["data"]

    if not kite_service:
        print("[MARKET_DATA] No kite_service provided", file=sys.stderr, flush=True)
        return []

    if not kite_service.is_authenticated():
        print(f"[MARKET_DATA] Kite not authenticated. simulation_mode={kite_service.is_simulation}, has_token={bool(kite_service._access_token)}", file=sys.stderr, flush=True)
        return []

    try:
        print("[MARKET_DATA] Fetching NFO instruments from Kite...", file=sys.stderr, flush=True)
        instruments = kite_service.get_instruments("NFO")
        if instruments:
            _instruments_cache["data"] = instruments
            _instruments_cache["timestamp"] = now
            print(f"[MARKET_DATA] Fetched {len(instruments)} NFO instruments from Kite", file=sys.stderr, flush=True)
        else:
            print("[MARKET_DATA] Kite returned 0 instruments", file=sys.stderr, flush=True)
        return instruments
    except Exception as e:
        print(f"[MARKET_DATA] Failed to fetch instruments: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return _instruments_cache.get("data") or []


def get_available_expiries(kite_service, symbol):
    """Get available expiry dates for a symbol from Kite instruments.
    Returns sorted list of date objects (nearest first)."""
    instruments = get_nfo_instruments(kite_service)
    if not instruments:
        # Fallback: generate expected expiries
        return _fallback_expiries(symbol)

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


def get_nearest_expiry(kite_service, symbol):
    """Get the nearest available expiry for a symbol."""
    expiries = get_available_expiries(kite_service, symbol)
    if expiries:
        return expiries[0]
    return _fallback_expiries(symbol)[0]


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

    # Filter instruments for this symbol + expiry + options only
    chain_instruments = [
        i for i in instruments
        if i.get("name", "").upper() == symbol_upper
        and i.get("instrument_type") in ("CE", "PE")
        and i.get("expiry") == expiry_date
    ]

    if not chain_instruments:
        return None

    lot_size = chain_instruments[0].get("lot_size", 1)

    # Get spot price
    try:
        if symbol_upper in WEEKLY_EXPIRY_SYMBOLS:
            spot_key = f"NSE:{symbol_upper} 50" if symbol_upper == "NIFTY" else f"NSE:{symbol_upper}"
        else:
            spot_key = f"NSE:{symbol_upper}"
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
    """Generate expected expiry dates when Kite is not connected."""
    today = date.today()
    symbol_upper = symbol.upper()

    if symbol_upper in WEEKLY_EXPIRY_SYMBOLS:
        # Weekly: next 4 Thursdays
        expiries = []
        d = today
        for _ in range(30):
            d += timedelta(days=1)
            if d.weekday() == 3:  # Thursday
                expiries.append(d)
                if len(expiries) >= 4:
                    break
        return expiries
    else:
        # Monthly: last Thursday of next 3 months
        expiries = []
        for month_offset in range(3):
            year = today.year
            month = today.month + month_offset
            if month > 12:
                month -= 12
                year += 1
            # Find last Thursday of month
            if month == 12:
                next_month_first = date(year + 1, 1, 1)
            else:
                next_month_first = date(year, month + 1, 1)
            last_day = next_month_first - timedelta(days=1)
            # Walk back to Thursday
            while last_day.weekday() != 3:
                last_day -= timedelta(days=1)
            if last_day >= today:
                expiries.append(last_day)
        return expiries if expiries else [_next_thursday()]


def _next_thursday():
    today = date.today()
    days_ahead = 3 - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return today + timedelta(days=days_ahead)
