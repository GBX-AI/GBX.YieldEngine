"""
US Economic Events Engine for Yield Engine.

Fetches high-impact US economic data from Alpha Vantage:
- Non-Farm Payrolls (NFP)
- Unemployment Rate
- CPI Inflation
- Federal Funds Rate

Provides:
- Upcoming event warnings (48-hour window)
- Post-event surprise readings
- Sentiment override signals (RED on event day)
"""

import os
import time
import logging
import requests
from datetime import datetime, date, timedelta, timezone

logger = logging.getLogger(__name__)

ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
_IST = timezone(timedelta(hours=5, minutes=30))

# Cache for economic data (refresh every 6 hours)
_econ_cache = {}
ECON_CACHE_TTL = 6 * 3600

# Known high-impact US events and their typical schedule
# NFP: First Friday of each month
# CPI: ~12th of each month
# Unemployment: Same day as NFP
# Fed Rate: 8 FOMC meetings per year (roughly every 6 weeks)

TRACKED_INDICATORS = {
    "NONFARM_PAYROLL": {
        "name": "Non-Farm Payrolls (NFP)",
        "short_name": "NFP",
        "impact": "HIGH",
        "surprise_threshold": 50000,  # 50K jobs
        "unit": "thousands",
    },
    "UNEMPLOYMENT": {
        "name": "Unemployment Rate",
        "short_name": "Unemployment",
        "impact": "HIGH",
        "surprise_threshold": 0.2,  # 0.2% deviation
        "unit": "percent",
    },
    "CPI": {
        "name": "CPI Inflation",
        "short_name": "CPI",
        "impact": "HIGH",
        "surprise_threshold": 0.2,  # 0.2% deviation
        "unit": "percent",
    },
    "FEDERAL_FUNDS_RATE": {
        "name": "Federal Funds Rate",
        "short_name": "Fed Rate",
        "impact": "HIGH",
        "surprise_threshold": 0.25,  # 25 bps
        "unit": "percent",
    },
}


def _fetch_alpha_vantage(function_name, interval="monthly"):
    """Fetch economic indicator from Alpha Vantage."""
    cache_key = f"{function_name}_{interval}"
    now = time.time()

    if cache_key in _econ_cache and (now - _econ_cache[cache_key]["timestamp"]) < ECON_CACHE_TTL:
        return _econ_cache[cache_key]["data"]

    key = ALPHA_VANTAGE_KEY
    if not key:
        return None

    try:
        url = f"https://www.alphavantage.co/query?function={function_name}&interval={interval}&apikey={key}"
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            return None

        data = resp.json()
        if "data" not in data and "Error" not in str(data):
            # Some endpoints return different structures
            return data

        _econ_cache[cache_key] = {"data": data, "timestamp": now}
        return data
    except Exception as e:
        logger.debug("Alpha Vantage fetch failed for %s: %s", function_name, e)
        return None


def get_latest_readings():
    """Get the most recent readings for all tracked indicators.

    Returns dict: {
        "NFP": { "date": "2026-03-07", "value": 275000, "previous": 250000, "name": "...", ... },
        "Unemployment": { ... },
        ...
    }
    """
    readings = {}

    for av_function, meta in TRACKED_INDICATORS.items():
        try:
            data = _fetch_alpha_vantage(av_function)
            if not data:
                continue

            # Alpha Vantage returns "data" array with date/value pairs
            entries = data.get("data", [])
            if not entries:
                continue

            # Get latest and previous
            latest = entries[0] if len(entries) > 0 else None
            previous = entries[1] if len(entries) > 1 else None

            if latest:
                readings[meta["short_name"]] = {
                    "name": meta["name"],
                    "short_name": meta["short_name"],
                    "date": latest.get("date", ""),
                    "value": _parse_value(latest.get("value", "0")),
                    "previous_value": _parse_value(previous.get("value", "0")) if previous else None,
                    "previous_date": previous.get("date", "") if previous else None,
                    "impact": meta["impact"],
                    "unit": meta["unit"],
                }
        except Exception:
            continue

    return readings


def _parse_value(val):
    """Parse a value string to float."""
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0


def get_surprise_readings():
    """Calculate surprise factor for each indicator.

    Returns list of surprise alerts:
    [
        {
            "indicator": "NFP",
            "actual": 275000,
            "previous": 250000,
            "change": 25000,
            "surprise_direction": "POSITIVE" | "NEGATIVE" | "NEUTRAL",
            "severity": "HIGH" | "LOW",
            "interpretation": "US jobs came in 25K above previous reading — positive signal",
        }
    ]
    """
    readings = get_latest_readings()
    surprises = []

    for key, reading in readings.items():
        actual = reading.get("value", 0)
        previous = reading.get("previous_value")
        if previous is None or previous == 0:
            continue

        change = actual - previous
        meta = None
        for _, m in TRACKED_INDICATORS.items():
            if m["short_name"] == key:
                meta = m
                break

        if not meta:
            continue

        threshold = meta["surprise_threshold"]

        # Determine surprise direction
        if key == "NFP":
            # NFP: higher is positive (more jobs)
            if change > threshold:
                direction = "POSITIVE"
                severity = "HIGH"
                interp = f"US jobs added {change/1000:.0f}K above previous — positive for markets"
            elif change < -threshold:
                direction = "NEGATIVE"
                severity = "HIGH"
                interp = f"US jobs {abs(change)/1000:.0f}K below previous — negative signal for Indian markets"
            else:
                direction = "NEUTRAL"
                severity = "LOW"
                interp = f"US jobs in line with expectations ({change/1000:+.0f}K change)"

        elif key == "Unemployment":
            # Unemployment: lower is positive
            if change < -threshold:
                direction = "POSITIVE"
                severity = "HIGH"
                interp = f"US unemployment dropped {abs(change):.1f}% — positive signal"
            elif change > threshold:
                direction = "NEGATIVE"
                severity = "HIGH"
                interp = f"US unemployment rose {change:.1f}% — negative for markets"
            else:
                direction = "NEUTRAL"
                severity = "LOW"
                interp = f"US unemployment stable ({change:+.1f}% change)"

        elif key == "CPI":
            # CPI: lower is generally positive (less inflation pressure)
            if change > threshold:
                direction = "NEGATIVE"
                severity = "HIGH"
                interp = f"US inflation rose {change:.1f}% — hawkish Fed risk, negative for markets"
            elif change < -threshold:
                direction = "POSITIVE"
                severity = "HIGH"
                interp = f"US inflation dropped {abs(change):.1f}% — dovish signal, positive"
            else:
                direction = "NEUTRAL"
                severity = "LOW"
                interp = f"US inflation stable ({change:+.1f}% change)"

        elif key == "Fed Rate":
            # Fed Rate: cut is positive, hike is negative
            if change < -threshold:
                direction = "POSITIVE"
                severity = "HIGH"
                interp = f"Fed cut rates by {abs(change)*100:.0f} bps — positive for markets"
            elif change > threshold:
                direction = "NEGATIVE"
                severity = "HIGH"
                interp = f"Fed hiked rates by {change*100:.0f} bps — negative for markets"
            else:
                direction = "NEUTRAL"
                severity = "LOW"
                interp = f"Fed held rates steady"
        else:
            continue

        surprises.append({
            "indicator": key,
            "name": reading["name"],
            "actual": actual,
            "previous": previous,
            "date": reading["date"],
            "change": round(change, 2),
            "surprise_direction": direction,
            "severity": severity,
            "interpretation": interp,
            "unit": reading["unit"],
        })

    return surprises


def get_event_warnings():
    """Check for upcoming high-impact US events.

    Returns warnings for the morning briefing card:
    {
        "has_warning": bool,
        "warning_level": "RED" | "YELLOW" | "NONE",
        "warnings": [
            {
                "event": "NFP",
                "message": "Non-Farm Payrolls release scheduled this Friday",
                "recommendation": "Reduce lot sizes or skip new positions this week",
            }
        ],
        "recent_surprises": [...],  # post-event readings
    }
    """
    today = date.today()
    warnings = []

    # Check if NFP is upcoming (first Friday of the month)
    first_friday = _get_first_friday(today.year, today.month)
    days_to_nfp = (first_friday - today).days

    if 0 <= days_to_nfp <= 2:
        if days_to_nfp == 0:
            warnings.append({
                "event": "NFP",
                "level": "RED",
                "message": "Non-Farm Payrolls releasing TODAY — high volatility expected tonight",
                "recommendation": "Do NOT open new positions. US jobs data will move markets significantly.",
            })
        else:
            warnings.append({
                "event": "NFP",
                "level": "YELLOW",
                "message": f"Non-Farm Payrolls in {days_to_nfp} day(s) — elevated risk",
                "recommendation": "Reduce lot sizes to minimum. Consider skipping new positions.",
            })

    # Check if CPI is upcoming (~12th of month)
    cpi_day = _estimate_cpi_date(today.year, today.month)
    days_to_cpi = (cpi_day - today).days

    if 0 <= days_to_cpi <= 2:
        if days_to_cpi == 0:
            warnings.append({
                "event": "CPI",
                "level": "RED",
                "message": "CPI Inflation data releasing TODAY",
                "recommendation": "Avoid new positions. Inflation data impacts Fed rate expectations.",
            })
        else:
            warnings.append({
                "event": "CPI",
                "level": "YELLOW",
                "message": f"CPI data in {days_to_cpi} day(s)",
                "recommendation": "Use minimum lot sizes for new positions.",
            })

    # Get recent surprise readings
    surprises = get_surprise_readings()
    high_impact_surprises = [s for s in surprises if s["severity"] == "HIGH"]

    # Determine overall warning level
    warning_level = "NONE"
    if any(w["level"] == "RED" for w in warnings):
        warning_level = "RED"
    elif warnings or high_impact_surprises:
        warning_level = "YELLOW"

    return {
        "has_warning": len(warnings) > 0 or len(high_impact_surprises) > 0,
        "warning_level": warning_level,
        "warnings": warnings,
        "recent_surprises": surprises,
        "latest_readings": get_latest_readings(),
    }


def _get_first_friday(year, month):
    """Get the first Friday of a month."""
    d = date(year, month, 1)
    while d.weekday() != 4:  # Friday = 4
        d += timedelta(days=1)
    return d


def _estimate_cpi_date(year, month):
    """Estimate CPI release date (~12th of month, Tuesday/Wednesday)."""
    d = date(year, month, 12)
    # CPI is usually released on Tuesday or Wednesday around 12th
    while d.weekday() > 4:  # Skip weekends
        d += timedelta(days=1)
    return d
