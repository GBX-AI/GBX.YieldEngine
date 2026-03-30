"""
Global Market Sentiment Engine for Yield Engine.

Produces a daily sentiment signal (GREEN/YELLOW/RED) by combining:
- Gift Nifty (from Kite) — pre-market direction
- India VIX level and direction
- NIFTY Futures premium/discount vs spot
- Global indices overnight movement (Yahoo Finance)

The sentiment signal is used by the Scanner to:
- Adjust position sizing (RED = minimum or skip)
- Show morning briefing card on Dashboard
- Gate new position entries
"""

import time
import logging
from datetime import datetime, date, timezone, timedelta

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
_sentiment_cache = {"data": None, "timestamp": 0}
SENTIMENT_CACHE_TTL = 300  # 5 minutes


def get_sentiment(kite_service=None):
    """
    Get current market sentiment combining all factors.

    Returns:
    {
        "signal": "GREEN" | "YELLOW" | "RED",
        "color": "#6ee7b7" | "#fcd34d" | "#f87171",
        "summary": "Markets positive — Gift Nifty up, VIX low",
        "factors": [
            { "name": "Gift Nifty", "value": "22,332 (-1.83%)", "signal": "RED", "weight": 30 },
            { "name": "India VIX", "value": "14.5", "signal": "GREEN", "weight": 25 },
            ...
        ],
        "gift_nifty": { "value": 22332, "change_pct": -1.83, "prev_close": 22750 },
        "vix": { "value": 14.5, "signal": "NORMAL" },
        "global_indices": { ... },
        "nifty_futures": { "premium_pct": 0.15 },
        "score": 45,  // 0-100, higher = more bullish
        "fetched_at": "2026-03-30T12:43:00+05:30"
    }
    """
    now = time.time()
    if _sentiment_cache["data"] and (now - _sentiment_cache["timestamp"]) < SENTIMENT_CACHE_TTL:
        return _sentiment_cache["data"]

    factors = []
    score = 50  # Start neutral

    # ── 1. NIFTY Direction (Gift Nifty proxy — futures vs previous close) ──
    nifty_direction = _fetch_nifty_direction(kite_service)
    if nifty_direction:
        change_pct = nifty_direction.get("change_pct", 0)
        if change_pct >= 0.5:
            signal = "GREEN"
            score += 15
        elif change_pct <= -1.0:
            signal = "RED"
            score -= 25
        elif change_pct <= -0.5:
            signal = "RED"
            score -= 15
        else:
            signal = "YELLOW"

        factors.append({
            "name": "NIFTY Direction",
            "value": f"{nifty_direction['value']:,.1f} ({change_pct:+.2f}%)",
            "signal": signal,
            "weight": 30,
            "details": nifty_direction.get("details"),
        })
    else:
        factors.append({"name": "NIFTY Direction", "value": "Unavailable", "signal": "YELLOW", "weight": 0})

    # ── 2. India VIX ──
    vix_data = _fetch_vix(kite_service)
    if vix_data:
        vix_val = vix_data.get("value", 0)
        if vix_val >= 20:
            vix_signal = "RED"
            score -= 15
        elif vix_val >= 16:
            vix_signal = "YELLOW"
            score -= 5
        elif vix_val >= 12:
            vix_signal = "GREEN"
            score += 5
        else:
            vix_signal = "GREEN"
            score += 10

        factors.append({
            "name": "India VIX",
            "value": f"{vix_val:.1f}",
            "signal": vix_signal,
            "weight": 25,
        })
    else:
        factors.append({"name": "India VIX", "value": "Unavailable", "signal": "YELLOW", "weight": 0})

    # ── 3. NIFTY Futures Premium/Discount ──
    futures_data = _fetch_nifty_futures(kite_service)
    if futures_data:
        premium_pct = futures_data.get("premium_pct", 0)
        if premium_pct >= 0.1:
            fut_signal = "GREEN"
            score += 5
        elif premium_pct <= -0.1:
            fut_signal = "RED"
            score -= 5
        else:
            fut_signal = "YELLOW"

        factors.append({
            "name": "NIFTY Futures",
            "value": f"{premium_pct:+.2f}% {'premium' if premium_pct >= 0 else 'discount'}",
            "signal": fut_signal,
            "weight": 15,
        })

    # ── 4. Global Indices (Yahoo Finance) ──
    global_data = _fetch_global_indices()
    if global_data:
        avg_change = sum(g["change_pct"] for g in global_data.values()) / len(global_data) if global_data else 0
        if avg_change >= 0.5:
            global_signal = "GREEN"
            score += 15
        elif avg_change <= -0.5:
            global_signal = "RED"
            score -= 15
        else:
            global_signal = "YELLOW"

        factors.append({
            "name": "Global Markets",
            "value": f"Avg {avg_change:+.2f}%",
            "signal": global_signal,
            "weight": 30,
            "details": {k: f"{v['change_pct']:+.2f}%" for k, v in global_data.items()},
        })

    # ── 5. US Economic Events ──
    try:
        import us_events
        event_data = us_events.get_event_warnings()

        if event_data and event_data.get("has_warning"):
            # Event warnings override score
            if event_data["warning_level"] == "RED":
                score -= 30  # Strong negative override
                factors.append({
                    "name": "US Event WARNING",
                    "value": event_data["warnings"][0]["message"] if event_data["warnings"] else "High-impact US event today",
                    "signal": "RED",
                    "weight": 40,
                })
            elif event_data["warning_level"] == "YELLOW":
                score -= 10
                factors.append({
                    "name": "US Event Alert",
                    "value": event_data["warnings"][0]["message"] if event_data["warnings"] else "High-impact US event approaching",
                    "signal": "YELLOW",
                    "weight": 20,
                })

            # Add recent surprise readings
            for surprise in event_data.get("recent_surprises", [])[:2]:
                if surprise["severity"] == "HIGH":
                    s_signal = "GREEN" if surprise["surprise_direction"] == "POSITIVE" else "RED" if surprise["surprise_direction"] == "NEGATIVE" else "YELLOW"
                    if s_signal == "RED":
                        score -= 10
                    elif s_signal == "GREEN":
                        score += 5
                    factors.append({
                        "name": f"US {surprise['indicator']}",
                        "value": surprise["interpretation"],
                        "signal": s_signal,
                        "weight": 15,
                    })
    except Exception:
        pass  # US events are optional — don't crash sentiment

    # ── Determine overall signal ──
    score = max(0, min(100, score))
    if score >= 65:
        overall_signal = "GREEN"
        color = "#6ee7b7"
        summary = "Markets positive"
    elif score >= 40:
        overall_signal = "YELLOW"
        color = "#fcd34d"
        summary = "Mixed signals — proceed with caution"
    else:
        overall_signal = "RED"
        color = "#f87171"
        summary = "Markets negative — reduce exposure or skip"

    # Build summary details
    detail_parts = []
    if nifty_direction:
        direction = "up" if nifty_direction.get("change_pct", 0) >= 0 else "down"
        detail_parts.append(f"NIFTY {direction} {abs(nifty_direction.get('change_pct', 0)):.1f}%")
    if vix_data:
        detail_parts.append(f"VIX at {vix_data['value']:.1f}")
    if global_data:
        detail_parts.append(f"Global avg {avg_change:+.1f}%")
    if detail_parts:
        summary += " — " + ", ".join(detail_parts)

    # Get US events data for the result
    us_event_warnings = None
    try:
        import us_events
        us_event_warnings = us_events.get_event_warnings()
    except Exception:
        pass

    result = {
        "signal": overall_signal,
        "color": color,
        "summary": summary,
        "score": score,
        "factors": factors,
        "nifty_direction": nifty_direction,
        "vix": vix_data,
        "global_indices": global_data,
        "nifty_futures": futures_data,
        "us_events": us_event_warnings,
        "fetched_at": datetime.now(_IST).isoformat(),
    }

    _sentiment_cache["data"] = result
    _sentiment_cache["timestamp"] = now
    return result


def _fetch_nifty_direction(kite_service):
    """Fetch NIFTY spot change from previous close — proxy for Gift Nifty.
    Gift Nifty is not available via Kite API, but NIFTY spot change
    gives the same directional signal."""
    if not kite_service or not kite_service.is_authenticated():
        return None

    try:
        data = kite_service.get_quote(["NSE:NIFTY 50"])
        if not data:
            return None

        q = list(data.values())[0]
        ltp = q.get("last_price", 0)
        prev_close = q.get("ohlc", {}).get("close", 0)
        open_price = q.get("ohlc", {}).get("open", 0)
        day_high = q.get("ohlc", {}).get("high", 0)
        day_low = q.get("ohlc", {}).get("low", 0)

        if not ltp or not prev_close:
            return None

        change_pct = ((ltp - prev_close) / prev_close) * 100
        gap_pct = ((open_price - prev_close) / prev_close) * 100 if open_price and prev_close else 0

        return {
            "value": ltp,
            "prev_close": prev_close,
            "change_pct": round(change_pct, 2),
            "gap_pct": round(gap_pct, 2),
            "open": open_price,
            "high": day_high,
            "low": day_low,
            "source": "kite",
            "details": {
                "Spot": f"{ltp:,.1f}",
                "Prev Close": f"{prev_close:,.1f}",
                "Gap": f"{gap_pct:+.2f}%",
                "Day Range": f"{day_low:,.1f} — {day_high:,.1f}",
            },
        }
    except Exception:
        return None


def _fetch_vix(kite_service):
    """Fetch India VIX."""
    import vix_service
    vix_val = vix_service.get_india_vix(kite_service)
    if vix_val:
        signal_data = vix_service.get_vix_signal(vix_val)
        return {
            "value": vix_val,
            "signal": signal_data.get("signal", "UNKNOWN"),
            "label": signal_data.get("label", ""),
        }
    return None


def _fetch_nifty_futures(kite_service):
    """Fetch NIFTY futures premium/discount vs spot."""
    if not kite_service or not kite_service.is_authenticated():
        return None

    try:
        # Get spot
        spot_data = kite_service.get_ltp(["NSE:NIFTY 50"])
        spot = list(spot_data.values())[0]["last_price"]

        # Get current month futures — find from instruments
        import market_data
        instruments = market_data.get_nfo_instruments(kite_service)
        today = date.today()

        # Find nearest NIFTY FUT
        nifty_futs = [
            i for i in instruments
            if i.get("name", "").upper() == "NIFTY"
            and i.get("instrument_type") == "FUT"
            and i.get("expiry", date.min) >= today
        ]
        if not nifty_futs:
            return None

        nifty_futs.sort(key=lambda i: i.get("expiry", date.max))
        nearest_fut = nifty_futs[0]

        fut_data = kite_service.get_ltp([f"NFO:{nearest_fut['tradingsymbol']}"])
        fut_price = list(fut_data.values())[0]["last_price"]

        premium = fut_price - spot
        premium_pct = (premium / spot) * 100

        return {
            "futures_price": round(fut_price, 2),
            "spot_price": round(spot, 2),
            "premium": round(premium, 2),
            "premium_pct": round(premium_pct, 2),
            "tradingsymbol": nearest_fut["tradingsymbol"],
        }
    except Exception:
        return None


def _fetch_global_indices():
    """Fetch global indices overnight changes from Yahoo Finance."""
    try:
        import yfinance as yf

        symbols = {
            "S&P 500": "^GSPC",
            "Nasdaq": "^IXIC",
            "Dow Jones": "^DJI",
            "Nikkei 225": "^N225",
            "Hang Seng": "^HSI",
        }

        results = {}
        tickers = yf.Tickers(" ".join(symbols.values()))

        for name, sym in symbols.items():
            try:
                ticker = tickers.tickers.get(sym)
                if not ticker:
                    continue
                info = ticker.fast_info
                ltp = getattr(info, "last_price", None)
                prev = getattr(info, "previous_close", None)
                if ltp and prev and prev > 0:
                    change_pct = ((ltp - prev) / prev) * 100
                    results[name] = {
                        "value": round(ltp, 2),
                        "prev_close": round(prev, 2),
                        "change_pct": round(change_pct, 2),
                    }
            except Exception:
                continue

        return results if results else None
    except ImportError:
        return None
    except Exception:
        return None
