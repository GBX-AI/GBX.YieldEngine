"""
India VIX service — fetches VIX and provides market condition signals.
Used by strategy engine to adjust strike selection and signal environment quality.
"""

import logging
import time

logger = logging.getLogger(__name__)

# Cache VIX for 5 minutes
_vix_cache = {"value": None, "timestamp": 0}
VIX_CACHE_TTL = 300  # 5 minutes


def get_india_vix(kite_service=None) -> float | None:
    """
    Fetch India VIX value.
    Priority: 1. Kite API  2. NSE API  3. Cached value  4. None
    """
    now = time.time()
    if _vix_cache["value"] and (now - _vix_cache["timestamp"]) < VIX_CACHE_TTL:
        return _vix_cache["value"]

    # Try Kite first
    if kite_service and kite_service.is_authenticated():
        try:
            data = kite_service.get_ltp(["NSE:INDIA VIX"])
            vix = list(data.values())[0]["last_price"]
            if vix and vix > 0:
                _vix_cache["value"] = round(vix, 2)
                _vix_cache["timestamp"] = now
                return _vix_cache["value"]
        except Exception as e:
            logger.debug("VIX from Kite failed: %s", e)

    # Try NSE API
    try:
        import requests
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        resp = requests.get("https://www.nseindia.com/api/allIndices", headers=headers, timeout=10)
        if resp.status_code == 200:
            for idx in resp.json().get("data", []):
                if "VIX" in idx.get("index", "").upper():
                    vix = idx.get("last", 0)
                    if vix and vix > 0:
                        _vix_cache["value"] = round(vix, 2)
                        _vix_cache["timestamp"] = now
                        return _vix_cache["value"]
    except Exception as e:
        logger.debug("VIX from NSE failed: %s", e)

    # Return cached or None
    return _vix_cache.get("value")


def get_vix_signal(vix: float | None) -> dict:
    """
    Interpret VIX level as a market signal for option selling.

    Returns dict with signal, color, label, and recommendation.
    """
    if vix is None:
        return {
            "vix": None,
            "signal": "UNKNOWN",
            "color": "gray",
            "label": "VIX unavailable",
            "recommendation": "Proceed with default settings",
        }

    if vix >= 18:
        return {
            "vix": vix,
            "signal": "HIGH",
            "color": "green",
            "label": f"VIX {vix} — Rich premiums",
            "recommendation": "Elevated volatility — good environment for selling premium. Use wider OTM strikes.",
        }
    if vix >= 14:
        return {
            "vix": vix,
            "signal": "NORMAL",
            "color": "yellow",
            "label": f"VIX {vix} — Normal",
            "recommendation": "Standard volatility environment. Use default strike selection.",
        }
    if vix >= 11:
        return {
            "vix": vix,
            "signal": "LOW",
            "color": "orange",
            "label": f"VIX {vix} — Thin premiums",
            "recommendation": "Low volatility — premiums are thin. Consider closer strikes or smaller positions.",
        }
    return {
        "vix": vix,
        "signal": "VERY_LOW",
        "color": "red",
        "label": f"VIX {vix} — Very low",
        "recommendation": "Very low premiums — consider sitting out or only covered calls.",
    }


def get_vix_adjusted_delta_target(vix: float | None, base_delta: float = 0.16) -> float:
    """
    Adjust target delta for OTM strike selection based on VIX.
    Higher VIX → wider strikes (lower delta) still pay well.
    Lower VIX → closer strikes needed to collect meaningful premium.
    """
    if vix is None:
        return base_delta

    if vix >= 18:
        return 0.12  # Go wider — premiums are rich enough further OTM
    if vix >= 14:
        return base_delta  # Standard
    if vix >= 11:
        return 0.20  # Go closer — need to collect enough premium
    return 0.25  # Very close — thin premiums, only if confident
