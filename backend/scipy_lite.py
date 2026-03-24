"""
Lightweight normal distribution functions to avoid scipy dependency.
Uses Abramowitz & Stegun approximation for CDF.
"""

import math


def norm_pdf(x):
    """Standard normal probability density function."""
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def norm_cdf(x):
    """
    Cumulative distribution function for standard normal.
    Abramowitz & Stegun approximation (error < 7.5e-8).
    """
    if x < -8.0:
        return 0.0
    if x > 8.0:
        return 1.0

    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911

    sign = 1
    if x < 0:
        sign = -1
    x_abs = abs(x) / math.sqrt(2)

    t = 1.0 / (1.0 + p * x_abs)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x_abs * x_abs)

    return 0.5 * (1.0 + sign * y)
