"""
Live price service for Yield Engine simulation mode.

Fetches real market data from free sources:
- Yahoo Finance (yfinance): spot/equity prices for NSE stocks & indices
- NSE India API: option chain data (OI, IV, volume, LTP, bid/ask)

All fetches are cached with a configurable TTL to avoid rate limiting.
Falls back gracefully to None when sources are unavailable.
"""

import logging
import time
import threading
from datetime import date, datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class _PriceCache:
    """Thread-safe in-memory cache with per-key TTL."""

    def __init__(self, default_ttl: int = 120):
        self._store: dict[str, tuple[float, object]] = {}  # key -> (expires_at, value)
        self._lock = threading.Lock()
        self._default_ttl = default_ttl

    def get(self, key: str) -> object | None:
        with self._lock:
            entry = self._store.get(key)
            if entry and entry[0] > time.time():
                return entry[1]
            # Expired or missing
            if entry:
                del self._store[key]
            return None

    def set(self, key: str, value: object, ttl: int | None = None) -> None:
        with self._lock:
            self._store[key] = (time.time() + (ttl or self._default_ttl), value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


_cache = _PriceCache(default_ttl=30)

# Yahoo Finance symbol mapping for Indian markets
_YF_STOCK_SUFFIX = ".NS"
_YF_INDEX_MAP = {
    "NIFTY": "^NSEI",
    "NIFTY50": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "NIFTYBANK": "^NSEBANK",
}

# NSE API endpoints
_NSE_OPTION_CHAIN_INDEX = "https://www.nseindia.com/api/option-chain-indices?symbol={}"
_NSE_OPTION_CHAIN_EQUITY = "https://www.nseindia.com/api/option-chain-equities?symbol={}"
_NSE_BASE_URL = "https://www.nseindia.com"

# NSE symbol mapping (indices use different names)
_NSE_INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "NIFTYIT", "FINNIFTY", "MIDCPNIFTY"}


# ---------------------------------------------------------------------------
# Yahoo Finance — Spot Prices
# ---------------------------------------------------------------------------

def _yf_symbol(symbol: str) -> str:
    """Convert an NSE symbol to Yahoo Finance ticker."""
    upper = symbol.upper().replace(" ", "")
    if upper in _YF_INDEX_MAP:
        return _YF_INDEX_MAP[upper]
    return f"{upper}{_YF_STOCK_SUFFIX}"


def fetch_spot_price(symbol: str) -> dict | None:
    """
    Fetch current spot price from Yahoo Finance.

    Returns: {"ltp": float, "open": float, "high": float, "low": float,
              "close": float, "volume": int, "source": "yahoo"} or None
    """
    cache_key = f"spot:{symbol.upper()}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        import yfinance as yf

        ticker = yf.Ticker(_yf_symbol(symbol))
        info = ticker.fast_info

        ltp = getattr(info, "last_price", None)
        if ltp is None or ltp <= 0:
            # Try from history as fallback
            hist = ticker.history(period="1d")
            if hist.empty:
                logger.debug("Yahoo Finance: no data for %s", symbol)
                return None
            ltp = float(hist["Close"].iloc[-1])

        result = {
            "ltp": round(float(ltp), 2),
            "open": round(float(getattr(info, "open", ltp)), 2),
            "high": round(float(getattr(info, "day_high", ltp)), 2),
            "low": round(float(getattr(info, "day_low", ltp)), 2),
            "close": round(float(getattr(info, "previous_close", ltp)), 2),
            "volume": int(getattr(info, "last_volume", 0) or 0),
            "source": "yahoo",
        }
        _cache.set(cache_key, result, ttl=120)
        logger.debug("Yahoo Finance: fetched %s → LTP=%.2f", symbol, ltp)
        return result

    except ImportError:
        logger.warning("yfinance not installed — live spot prices unavailable")
        return None
    except Exception as exc:
        logger.warning("Yahoo Finance fetch failed for %s: %s", symbol, exc)
        return None


def fetch_spot_prices_batch(symbols: list[str]) -> dict[str, dict]:
    """
    Fetch spot prices for multiple symbols in a single HTTP call via yf.download().

    Returns: {symbol: {ltp, open, high, low, close, volume, source}} for successful fetches.
    """
    results = {}

    # Check cache first, collect misses
    misses = []
    for sym in symbols:
        upper = sym.upper()
        cached = _cache.get(f"spot:{upper}")
        if cached is not None:
            results[upper] = cached
        else:
            misses.append(upper)

    if not misses:
        return results

    try:
        import yfinance as yf

        yf_symbols = [_yf_symbol(s) for s in misses]
        sym_map = dict(zip(yf_symbols, misses))  # YF ticker -> original symbol

        # Single HTTP call for all tickers — much faster than per-ticker fast_info
        df = yf.download(
            yf_symbols,
            period="2d",
            interval="1d",
            progress=False,
            threads=True,
            group_by="ticker" if len(yf_symbols) > 1 else "column",
        )

        if df.empty:
            return results

        for yf_sym, orig_sym in sym_map.items():
            try:
                if len(yf_symbols) == 1:
                    ticker_df = df
                else:
                    ticker_df = df[yf_sym] if yf_sym in df.columns.get_level_values(0) else None

                if ticker_df is None or ticker_df.empty:
                    continue

                # Use the last available row
                last = ticker_df.dropna(subset=["Close"]).iloc[-1] if "Close" in ticker_df.columns else None
                if last is None:
                    continue

                ltp = float(last["Close"])
                if ltp <= 0:
                    continue

                # Previous close for day change calculation
                prev_close = float(ticker_df.iloc[-2]["Close"]) if len(ticker_df) > 1 else ltp

                result = {
                    "ltp": round(ltp, 2),
                    "open": round(float(last.get("Open", ltp)), 2),
                    "high": round(float(last.get("High", ltp)), 2),
                    "low": round(float(last.get("Low", ltp)), 2),
                    "close": round(prev_close, 2),
                    "volume": int(last.get("Volume", 0) or 0),
                    "source": "yahoo",
                }
                results[orig_sym] = result
                _cache.set(f"spot:{orig_sym}", result, ttl=120)
            except Exception as exc:
                logger.debug("Yahoo batch: failed for %s: %s", orig_sym, exc)

    except ImportError:
        logger.warning("yfinance not installed")
    except Exception as exc:
        logger.warning("Yahoo batch fetch failed: %s", exc)

    return results


# ---------------------------------------------------------------------------
# NSE India — Option Chain
# ---------------------------------------------------------------------------

def _nse_session():
    """
    Create a requests session with NSE-compatible headers.
    NSE requires a valid session cookie from the homepage before API calls work.
    """
    import requests

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.nseindia.com/option-chain",
    })
    # Warm up — get cookies from NSE homepage
    try:
        session.get(_NSE_BASE_URL, timeout=10)
    except Exception:
        pass  # Proceed anyway; cookies may still work
    return session


_nse_session_cache: dict[str, object] = {"session": None, "created": 0}
_nse_session_lock = threading.Lock()


def _get_nse_session():
    """Get or create a cached NSE session (refreshed every 5 minutes)."""
    with _nse_session_lock:
        now = time.time()
        if _nse_session_cache["session"] is None or now - _nse_session_cache["created"] > 300:
            _nse_session_cache["session"] = _nse_session()
            _nse_session_cache["created"] = now
        return _nse_session_cache["session"]


def fetch_nse_option_chain(symbol: str) -> dict | None:
    """
    Fetch option chain from NSE India API.

    Returns the raw NSE option chain response with records containing:
    - strikePrice, expiryDate, openInterest, changeinOpenInterest,
      totalTradedVolume, impliedVolatility, lastPrice, bidprice, askPrice,
      bidQty, askQty, etc.

    Returns None on failure.
    """
    upper = symbol.upper()
    cache_key = f"nse_chain:{upper}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        import requests

        session = _get_nse_session()

        if upper in _NSE_INDEX_SYMBOLS:
            url = _NSE_OPTION_CHAIN_INDEX.format(upper)
        else:
            url = _NSE_OPTION_CHAIN_EQUITY.format(upper)

        resp = session.get(url, timeout=15)

        if resp.status_code == 401 or resp.status_code == 403:
            # Session expired — refresh and retry once
            with _nse_session_lock:
                _nse_session_cache["session"] = _nse_session()
                _nse_session_cache["created"] = time.time()
            session = _nse_session_cache["session"]
            resp = session.get(url, timeout=15)

        if resp.status_code != 200:
            logger.warning("NSE API returned %d for %s", resp.status_code, upper)
            return None

        data = resp.json()
        if "records" not in data:
            logger.warning("NSE API response missing 'records' for %s", upper)
            return None

        # Cache for 60s (NSE data updates ~every minute during market hours)
        _cache.set(cache_key, data, ttl=60)
        logger.debug("NSE: fetched option chain for %s (%d records)",
                      upper, len(data["records"].get("data", [])))
        return data

    except ImportError:
        logger.warning("requests not installed — NSE option chain unavailable")
        return None
    except Exception as exc:
        logger.warning("NSE option chain fetch failed for %s: %s", upper, exc)
        return None


def parse_nse_option_chain(
    nse_data: dict,
    symbol: str,
    target_expiry: str | None = None,
    num_strikes: int = 10,
) -> dict | None:
    """
    Parse raw NSE option chain data into YieldEngine's format.

    Args:
        nse_data: raw response from fetch_nse_option_chain()
        symbol: underlying symbol
        target_expiry: expiry in YYYY-MM-DD format (None = nearest)
        num_strikes: number of strikes above/below ATM

    Returns: parsed chain dict compatible with kite_service format, or None
    """
    try:
        records = nse_data.get("records", {})
        all_data = records.get("data", [])

        if not all_data:
            return None

        # Get underlying spot price
        spot = records.get("underlyingValue", 0)
        if not spot:
            # Try from filtered data
            filtered = nse_data.get("filtered", {})
            spot = filtered.get("underlyingValue", 0)

        if not spot:
            return None

        # Get available expiries
        expiry_dates = sorted(set(records.get("expiryDates", [])))

        if target_expiry:
            # Match the target expiry
            chosen_expiry = target_expiry
            # NSE returns dates as "DD-Mon-YYYY" — need to match
            chosen_expiry_nse = None
            try:
                dt = datetime.strptime(target_expiry, "%Y-%m-%d")
                chosen_expiry_nse = dt.strftime("%d-%b-%Y")
            except ValueError:
                chosen_expiry_nse = target_expiry
        else:
            # Pick nearest expiry
            if not expiry_dates:
                return None
            chosen_expiry_nse = expiry_dates[0]
            try:
                dt = datetime.strptime(chosen_expiry_nse, "%d-%b-%Y")
                chosen_expiry = dt.strftime("%Y-%m-%d")
            except ValueError:
                chosen_expiry = chosen_expiry_nse

        # Filter records for chosen expiry
        chain_records = [
            r for r in all_data
            if r.get("expiryDate") == chosen_expiry_nse
        ]

        if not chain_records:
            return None

        # Get all strikes, find ATM, select range
        all_strikes = sorted(set(r.get("strikePrice", 0) for r in chain_records))
        if not all_strikes:
            return None

        atm_strike = min(all_strikes, key=lambda s: abs(s - spot))
        atm_idx = all_strikes.index(atm_strike)
        start = max(0, atm_idx - num_strikes)
        end = min(len(all_strikes), atm_idx + num_strikes + 1)
        selected_strikes = set(all_strikes[start:end])

        # Parse strike data
        strike_gap = (all_strikes[1] - all_strikes[0]) if len(all_strikes) > 1 else 50
        strikes_data = {}

        for r in chain_records:
            strike = r.get("strikePrice", 0)
            if strike not in selected_strikes:
                continue

            if strike not in strikes_data:
                strikes_data[strike] = {"strike": strike}

            for opt_type, nse_key in [("CE", "CE"), ("PE", "PE")]:
                opt = r.get(nse_key)
                if not opt:
                    continue

                strikes_data[strike][opt_type] = {
                    "tradingsymbol": opt.get("identifier", ""),
                    "premium": opt.get("lastPrice", 0),
                    "bid": opt.get("bidprice", 0),
                    "ask": opt.get("askPrice", 0),
                    "iv": round(opt.get("impliedVolatility", 0), 2),
                    "oi": opt.get("openInterest", 0),
                    "oi_change": opt.get("changeinOpenInterest", 0),
                    "volume": opt.get("totalTradedVolume", 0),
                    "lot_size": records.get("lotSize", 1) if isinstance(records.get("lotSize"), int) else 1,
                    "price_source": "nse",
                }

        # Compute DTE
        try:
            expiry_date = datetime.strptime(chosen_expiry, "%Y-%m-%d").date()
            dte = max(1, (expiry_date - date.today()).days)
        except ValueError:
            dte = 7

        return {
            "symbol": symbol.upper(),
            "spot": round(spot, 2),
            "expiry": chosen_expiry,
            "dte": dte,
            "lot_size": records.get("lotSize", 1) if isinstance(records.get("lotSize"), int) else 1,
            "strike_gap": strike_gap,
            "atm_strike": atm_strike,
            "strikes": [strikes_data[s] for s in sorted(strikes_data.keys())],
            "price_source": "nse",
        }

    except Exception as exc:
        logger.warning("Failed to parse NSE option chain for %s: %s", symbol, exc)
        return None


# ---------------------------------------------------------------------------
# Public API — unified access
# ---------------------------------------------------------------------------

def get_live_spot(symbol: str) -> float | None:
    """Get live spot price for a symbol. Returns LTP or None."""
    result = fetch_spot_price(symbol)
    return result["ltp"] if result else None


def get_live_quote(symbol: str) -> dict | None:
    """Get live quote (full OHLCV) for a symbol. Returns dict or None."""
    return fetch_spot_price(symbol)


def get_live_option_chain(
    symbol: str,
    expiry: str | None = None,
    num_strikes: int = 10,
) -> dict | None:
    """
    Get live option chain from NSE.
    Returns parsed chain dict or None.
    """
    nse_data = fetch_nse_option_chain(symbol)
    if nse_data is None:
        return None
    return parse_nse_option_chain(nse_data, symbol, expiry, num_strikes)


def is_available() -> bool:
    """Check if live price service has any working source."""
    try:
        import yfinance
        return True
    except ImportError:
        return False


def get_price_source_status() -> dict:
    """Return status of each price source for the /api/status endpoint."""
    yahoo_ok = False
    nse_ok = False

    try:
        import yfinance
        yahoo_ok = True
    except ImportError:
        pass

    try:
        import requests
        nse_ok = True
    except ImportError:
        pass

    return {
        "yahoo_finance": "available" if yahoo_ok else "unavailable",
        "nse_api": "available" if nse_ok else "unavailable",
        "spot_source": "yahoo" if yahoo_ok else "simulated",
        "option_chain_source": "nse" if nse_ok else "simulated",
    }
