"""
Black-Scholes pricing, Greeks, and IV solver (Newton-Raphson).
Risk-free rate: 6.5% (Indian government bond proxy).
"""

import math
from scipy_lite import norm_cdf, norm_pdf

RISK_FREE_RATE = 0.065


def norm_cdf_approx(x):
    """Approximation of the cumulative normal distribution."""
    return norm_cdf(x)


def norm_pdf_approx(x):
    """Standard normal probability density function."""
    return norm_pdf(x)


def d1(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return 0.0
    return (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))


def d2(S, K, T, r, sigma):
    return d1(S, K, T, r, sigma) - sigma * math.sqrt(T)


def call_price(S, K, T, r, sigma):
    """European call option price."""
    if T <= 0:
        return max(0, S - K)
    _d1 = d1(S, K, T, r, sigma)
    _d2 = d2(S, K, T, r, sigma)
    return S * norm_cdf_approx(_d1) - K * math.exp(-r * T) * norm_cdf_approx(_d2)


def put_price(S, K, T, r, sigma):
    """European put option price."""
    if T <= 0:
        return max(0, K - S)
    _d1 = d1(S, K, T, r, sigma)
    _d2 = d2(S, K, T, r, sigma)
    return K * math.exp(-r * T) * norm_cdf_approx(-_d2) - S * norm_cdf_approx(-_d1)


def option_price(S, K, T, r, sigma, option_type="CE"):
    """Price a European option."""
    if option_type.upper() in ("CE", "CALL", "C"):
        return call_price(S, K, T, r, sigma)
    return put_price(S, K, T, r, sigma)


def delta(S, K, T, r, sigma, option_type="CE"):
    """Option delta."""
    if T <= 0:
        if option_type.upper() in ("CE", "CALL", "C"):
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    _d1 = d1(S, K, T, r, sigma)
    if option_type.upper() in ("CE", "CALL", "C"):
        return norm_cdf_approx(_d1)
    return norm_cdf_approx(_d1) - 1


def gamma(S, K, T, r, sigma):
    """Option gamma (same for calls and puts)."""
    if T <= 0 or sigma <= 0:
        return 0.0
    _d1 = d1(S, K, T, r, sigma)
    return norm_pdf_approx(_d1) / (S * sigma * math.sqrt(T))


def theta(S, K, T, r, sigma, option_type="CE"):
    """Option theta (per day)."""
    if T <= 0 or sigma <= 0:
        return 0.0
    _d1 = d1(S, K, T, r, sigma)
    _d2 = d2(S, K, T, r, sigma)
    common = -(S * norm_pdf_approx(_d1) * sigma) / (2 * math.sqrt(T))
    if option_type.upper() in ("CE", "CALL", "C"):
        t = common - r * K * math.exp(-r * T) * norm_cdf_approx(_d2)
    else:
        t = common + r * K * math.exp(-r * T) * norm_cdf_approx(-_d2)
    return t / 365  # per day


def vega(S, K, T, r, sigma):
    """Option vega (per 1% change in IV)."""
    if T <= 0 or sigma <= 0:
        return 0.0
    _d1 = d1(S, K, T, r, sigma)
    return S * norm_pdf_approx(_d1) * math.sqrt(T) / 100


def implied_volatility(market_price, S, K, T, r, option_type="CE", max_iter=100, tol=1e-6):
    """
    Newton-Raphson implied volatility solver.
    Returns IV as a decimal (e.g., 0.22 for 22%).
    """
    if T <= 0:
        return 0.0

    # Initial guess
    sigma = 0.3

    for _ in range(max_iter):
        price = option_price(S, K, T, r, sigma, option_type)
        diff = price - market_price

        if abs(diff) < tol:
            return sigma

        v = vega(S, K, T, r, sigma) * 100  # vega is per 1%, we need per 100%
        if abs(v) < 1e-10:
            break

        sigma -= diff / v

        # Keep sigma in reasonable bounds
        sigma = max(0.01, min(5.0, sigma))

    return sigma


def probability_otm(S, K, T, r, sigma, option_type="CE"):
    """Probability that option expires out of the money."""
    if T <= 0:
        if option_type.upper() in ("CE", "CALL", "C"):
            return 0.0 if S > K else 1.0
        return 0.0 if S < K else 1.0
    _d2 = d2(S, K, T, r, sigma)
    if option_type.upper() in ("CE", "CALL", "C"):
        return norm_cdf_approx(-_d2)  # Prob S < K at expiry
    return norm_cdf_approx(_d2)  # Prob S > K at expiry


def compute_greeks(S, K, T, r, sigma, option_type="CE"):
    """Compute all Greeks for an option."""
    return {
        "price": option_price(S, K, T, r, sigma, option_type),
        "delta": delta(S, K, T, r, sigma, option_type),
        "gamma": gamma(S, K, T, r, sigma),
        "theta": theta(S, K, T, r, sigma, option_type),
        "vega": vega(S, K, T, r, sigma),
        "iv": sigma,
        "prob_otm": probability_otm(S, K, T, r, sigma, option_type),
    }
