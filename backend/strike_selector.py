"""
Adaptive OTM Strike Selection Module (Yield Engine v3 — Section 4F)

Selects optimal out-of-the-money strikes based on risk profile, IV environment,
DTE, market structure, and portfolio delta. Supports manual override mode.
"""

from models import get_setting, SIMULATION_STOCKS, SIMULATION_INDICES
from black_scholes import compute_greeks, RISK_FREE_RATE


# ---------------------------------------------------------------------------
# Risk profile definitions — target delta ranges for puts and calls
# ---------------------------------------------------------------------------

RISK_PROFILES = {
    "Conservative": {
        "put_delta_min": 0.10,
        "put_delta_max": 0.15,
        "call_delta_min": 0.10,
        "call_delta_max": 0.15,
        "description": "Safer strikes further OTM — lower premium, higher probability of profit",
    },
    "Moderate": {
        "put_delta_min": 0.15,
        "put_delta_max": 0.25,
        "call_delta_min": 0.15,
        "call_delta_max": 0.20,
        "description": "Balanced risk/reward — moderate premium with reasonable safety margin",
    },
    "Aggressive": {
        "put_delta_min": 0.25,
        "put_delta_max": 0.35,
        "call_delta_min": 0.20,
        "call_delta_max": 0.30,
        "description": "Closer-to-ATM strikes — higher premium, lower probability of profit",
    },
}

DELTA_FLOOR = 0.05
DELTA_CEILING = 0.40


# ---------------------------------------------------------------------------
# Core strike selection
# ---------------------------------------------------------------------------

def select_strike(
    symbol: str,
    opt_type: str,
    risk_profile: str,
    iv_rank: float,
    dte: int,
    market_trend: str = "neutral",
    portfolio_delta: float = 0.0,
    consecutive_red_days: int = 0,
) -> dict:
    """
    Select the optimal OTM strike for a given symbol and option type.

    Parameters
    ----------
    symbol          : Underlying ticker (e.g. "NIFTY", "RELIANCE")
    opt_type        : "CE" for call, "PE" for put
    risk_profile    : One of "Conservative", "Moderate", "Aggressive"
    iv_rank         : IV Rank as a percentage 0-100
    dte             : Days to expiry
    market_trend    : "bullish", "bearish", or "neutral"
    portfolio_delta : Current net portfolio delta
    consecutive_red_days : Number of consecutive red (down) days

    Returns
    -------
    dict with keys: target_delta, adjustments (list of str), force_spreads,
                    skip_reason (str or None), rationale (str)
    """

    # --- Manual override mode ---
    override_mode = get_setting("strike_selection_mode")
    if override_mode == "manual":
        manual_delta = get_setting("manual_target_delta")
        if manual_delta is not None:
            target = float(manual_delta)
            target = _clamp_delta(target)
            return {
                "target_delta": target,
                "adjustments": ["Manual override active"],
                "force_spreads": False,
                "skip_reason": None,
                "rationale": _format_rationale(
                    symbol, opt_type, risk_profile, target, ["Manual override active"],
                    force_spreads=False,
                ),
            }

    # --- Validate risk profile ---
    profile = RISK_PROFILES.get(risk_profile)
    if profile is None:
        profile = RISK_PROFILES["Moderate"]
        risk_profile = "Moderate"

    # --- Base delta (midpoint of the profile range) ---
    opt_upper = opt_type.upper()
    if opt_upper in ("PE", "PUT", "P"):
        base_delta = (profile["put_delta_min"] + profile["put_delta_max"]) / 2
    else:
        base_delta = (profile["call_delta_min"] + profile["call_delta_max"]) / 2

    target = base_delta
    adjustments: list[str] = []
    force_spreads = False
    skip_reason = None

    # --- IV Environment adjustment ---
    target, force_spreads, adjustments = _adjust_for_iv(
        target, iv_rank, adjustments,
    )

    # --- DTE adjustment ---
    target, adjustments = _adjust_for_dte(target, dte, adjustments)

    # --- Market structure adjustment ---
    target, adjustments = _adjust_for_market(
        target, opt_upper, market_trend, consecutive_red_days, adjustments,
    )

    # --- Portfolio delta skew ---
    skip_reason, adjustments = _adjust_for_portfolio_delta(
        opt_upper, portfolio_delta, adjustments,
    )

    # --- Clamp ---
    target = _clamp_delta(target)

    rationale = _format_rationale(
        symbol, opt_type, risk_profile, target, adjustments,
        force_spreads=force_spreads, skip_reason=skip_reason,
    )

    return {
        "target_delta": round(target, 4),
        "adjustments": adjustments,
        "force_spreads": force_spreads,
        "skip_reason": skip_reason,
        "rationale": rationale,
    }


# ---------------------------------------------------------------------------
# Adaptive adjustment helpers
# ---------------------------------------------------------------------------

def _adjust_for_iv(
    target: float, iv_rank: float, adjustments: list[str],
) -> tuple[float, bool, list[str]]:
    """IV Rank adjustments: low IV → move closer ATM, high IV → move further OTM."""
    force_spreads = False
    if iv_rank < 20:
        target += 0.05
        adjustments.append(f"IV Rank {iv_rank:.0f}% < 20% → +0.05 delta (low vol, move closer ATM)")
    elif 20 <= iv_rank <= 50:
        adjustments.append(f"IV Rank {iv_rank:.0f}% in 20-50% → no adjustment")
    elif 50 < iv_rank <= 80:
        target -= 0.05
        adjustments.append(f"IV Rank {iv_rank:.0f}% in 50-80% → -0.05 delta (elevated vol, move further OTM)")
    else:
        force_spreads = True
        target -= 0.05
        adjustments.append(
            f"IV Rank {iv_rank:.0f}% > 80% → force spreads only, -0.05 delta (extreme vol environment)"
        )
    return target, force_spreads, adjustments


def _adjust_for_dte(
    target: float, dte: int, adjustments: list[str],
) -> tuple[float, list[str]]:
    """DTE adjustments: short DTE → move further OTM, long DTE → move closer ATM."""
    if dte <= 4:
        target -= 0.03
        adjustments.append(f"DTE {dte} (3-4 days) → -0.03 delta (short expiry, reduce gamma risk)")
    elif dte >= 14:
        target += 0.03
        adjustments.append(f"DTE {dte} (14+ days) → +0.03 delta (longer expiry, can accept more delta)")
    else:
        adjustments.append(f"DTE {dte} (weekly) → no DTE adjustment")
    return target, adjustments


def _adjust_for_market(
    target: float,
    opt_upper: str,
    market_trend: str,
    consecutive_red_days: int,
    adjustments: list[str],
) -> tuple[float, list[str]]:
    """Market structure adjustments: bearish conditions push puts further OTM."""
    if market_trend == "bearish" and opt_upper in ("PE", "PUT", "P"):
        target -= 0.05
        adjustments.append("Bearish trend + selling puts → -0.05 delta (extra cushion for downside)")

    if consecutive_red_days >= 3:
        target -= 0.05
        adjustments.append(
            f"{consecutive_red_days} consecutive red days → -0.05 delta (market stress, widen safety margin)"
        )

    return target, adjustments


def _adjust_for_portfolio_delta(
    opt_upper: str,
    portfolio_delta: float,
    adjustments: list[str],
) -> tuple[str | None, list[str]]:
    """Portfolio delta skew: avoid adding to an already-skewed book."""
    skip_reason = None

    if portfolio_delta > 0.3 and opt_upper in ("PE", "PUT", "P"):
        skip_reason = (
            f"Portfolio net delta {portfolio_delta:+.2f} > +0.30 — "
            "skipping puts, prefer selling calls to neutralise upside skew"
        )
        adjustments.append(skip_reason)
    elif portfolio_delta < -0.1 and opt_upper in ("CE", "CALL", "C"):
        skip_reason = (
            f"Portfolio net delta {portfolio_delta:+.2f} < -0.10 — "
            "skipping calls, prefer selling puts to neutralise downside skew"
        )
        adjustments.append(skip_reason)

    return skip_reason, adjustments


def _clamp_delta(delta: float) -> float:
    """Clamp target delta within allowed bounds."""
    return max(DELTA_FLOOR, min(DELTA_CEILING, delta))


# ---------------------------------------------------------------------------
# Option chain helpers
# ---------------------------------------------------------------------------

def find_closest_delta(
    option_chain: list[dict],
    target_delta: float,
    opt_type: str = "PE",
) -> dict | None:
    """
    Find the strike in *option_chain* whose absolute delta is closest to
    *target_delta*.

    Each entry in option_chain must have at minimum:
        {"strike": float, "delta": float, ...}

    For puts, delta values are typically negative; we compare absolute values.

    Returns the best-matching chain entry, or None if the chain is empty.
    """
    if not option_chain:
        return None

    best = None
    best_diff = float("inf")

    for entry in option_chain:
        entry_delta = abs(entry.get("delta", 0))
        diff = abs(entry_delta - target_delta)
        if diff < best_diff:
            best_diff = diff
            best = entry

    return best


def find_strike_by_greeks(
    spot: float,
    strikes: list[float],
    dte: int,
    iv: float,
    opt_type: str,
    target_delta: float,
) -> dict | None:
    """
    When a live option chain is unavailable, compute greeks via Black-Scholes
    for each candidate strike and pick the one closest to the target delta.

    Returns dict with keys: strike, delta, gamma, theta, vega, price, diff
    """
    if not strikes:
        return None

    T = max(dte / 365.0, 1 / 365.0)
    best = None
    best_diff = float("inf")

    for K in strikes:
        greeks = compute_greeks(spot, K, T, RISK_FREE_RATE, iv, opt_type)
        entry_delta = abs(greeks.get("delta", 0))
        diff = abs(entry_delta - target_delta)
        if diff < best_diff:
            best_diff = diff
            best = {
                "strike": K,
                "delta": greeks["delta"],
                "gamma": greeks["gamma"],
                "theta": greeks["theta"],
                "vega": greeks["vega"],
                "price": greeks["price"],
                "diff": round(diff, 6),
            }

    return best


# ---------------------------------------------------------------------------
# Alternatives generator
# ---------------------------------------------------------------------------

def generate_alternatives(
    symbol: str,
    opt_type: str,
    iv_rank: float,
    dte: int,
    market_trend: str = "neutral",
    portfolio_delta: float = 0.0,
    consecutive_red_days: int = 0,
    option_chain: list[dict] | None = None,
) -> dict:
    """
    Generate Conservative / Moderate / Aggressive strike alternatives
    side-by-side for comparison.

    Returns
    -------
    dict keyed by profile name, each value containing the select_strike()
    result plus the matched chain entry (if option_chain provided).
    """
    alternatives = {}

    for profile_name in ("Conservative", "Moderate", "Aggressive"):
        result = select_strike(
            symbol=symbol,
            opt_type=opt_type,
            risk_profile=profile_name,
            iv_rank=iv_rank,
            dte=dte,
            market_trend=market_trend,
            portfolio_delta=portfolio_delta,
            consecutive_red_days=consecutive_red_days,
        )

        matched_strike = None
        if option_chain and result["skip_reason"] is None:
            matched_strike = find_closest_delta(
                option_chain, result["target_delta"], opt_type,
            )

        alternatives[profile_name] = {
            **result,
            "matched_strike": matched_strike,
        }

    return alternatives


# ---------------------------------------------------------------------------
# Rationale formatting
# ---------------------------------------------------------------------------

def _format_rationale(
    symbol: str,
    opt_type: str,
    risk_profile: str,
    target_delta: float,
    adjustments: list[str],
    force_spreads: bool = False,
    skip_reason: str | None = None,
) -> str:
    """Build a human-readable explanation of why this strike was selected."""
    opt_label = "PUT" if opt_type.upper() in ("PE", "PUT", "P") else "CALL"
    lines = [
        f"Strike Selection for {symbol} {opt_label} ({risk_profile} profile)",
        f"{'=' * 60}",
        f"Target delta: {target_delta:.4f}",
    ]

    if adjustments:
        lines.append("")
        lines.append("Adjustments applied:")
        for i, adj in enumerate(adjustments, 1):
            lines.append(f"  {i}. {adj}")

    if force_spreads:
        lines.append("")
        lines.append("⚠  SPREADS ONLY — extreme IV environment detected. "
                      "Naked selling disabled; use defined-risk spreads.")

    if skip_reason:
        lines.append("")
        lines.append(f"⛔  SKIP — {skip_reason}")

    profile_desc = RISK_PROFILES.get(risk_profile, {}).get("description", "")
    if profile_desc:
        lines.append("")
        lines.append(f"Profile note: {profile_desc}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Convenience: find strike price from spot/delta/iv (used by strategy_engine)
# ---------------------------------------------------------------------------

def select_strike_price(
    spot: float,
    option_type: str,
    target_delta: float,
    iv: float,
    dte: int,
    step: float = 50.0,
) -> float:
    """
    Find the nearest strike price that matches the target delta.
    Returns a rounded strike price (not a dict).

    This is a simpler interface used by strategy_engine when it already
    knows the target delta and just needs the strike price.
    """
    T = max(dte / 365.0, 1 / 365.0)

    # Generate candidate strikes around spot
    num_strikes = 20
    if option_type.upper() in ("PE", "PUT", "P"):
        # For puts, look below spot
        candidates = [round((spot - i * step) / step) * step for i in range(num_strikes)]
    else:
        # For calls, look above spot
        candidates = [round((spot + i * step) / step) * step for i in range(num_strikes)]

    candidates = [k for k in candidates if k > 0]

    best = find_strike_by_greeks(spot, candidates, dte, iv, option_type, target_delta)
    if best:
        return best["strike"]

    # Fallback: simple OTM percentage
    otm_pct = target_delta * 0.5  # rough approximation
    if option_type.upper() in ("PE", "PUT", "P"):
        return round((spot * (1 - otm_pct)) / step) * step
    return round((spot * (1 + otm_pct)) / step) * step


def generate_strike_alternatives(
    spot: float,
    option_type: str,
    iv: float,
    dte: int,
    step: float = 50.0,
) -> dict:
    """
    Simple convenience wrapper for strategy_engine.
    Returns Conservative / Moderate / Aggressive strike alternatives
    with price and probability for each.
    """
    T = max(dte / 365.0, 1 / 365.0)
    profiles = {
        "conservative": {"CE": 0.12, "PE": 0.12},
        "moderate": {"CE": 0.17, "PE": 0.20},
        "aggressive": {"CE": 0.25, "PE": 0.30},
    }

    opt_key = "CE" if option_type.upper() in ("CE", "CALL", "C") else "PE"
    result = {}

    for profile_name, deltas in profiles.items():
        target = deltas[opt_key]
        strike = select_strike_price(spot, option_type, target, iv, dte, step)
        greeks = compute_greeks(spot, strike, T, RISK_FREE_RATE, iv, option_type)
        otm_pct = abs(strike - spot) / spot

        result[profile_name] = {
            "strike": strike,
            "premium": round(greeks["price"], 2),
            "delta": round(abs(greeks["delta"]), 3),
            "prob_otm": round(greeks["prob_otm"] * 100, 1),
            "otm_pct": round(otm_pct * 100, 1),
        }

    return result
