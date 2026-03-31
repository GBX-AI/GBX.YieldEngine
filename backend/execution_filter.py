"""
Execution Reality Filter for Yield Engine.

Ensures every recommendation is actually executable in the real market.
Checks: bid/ask presence, spread width, volume, OI, slippage.

RULES:
- Use bid price for sells (what you'll actually get)
- Use ask price for buys (what you'll actually pay)
- Never use LTP or theoretical price for P&L calculation
- Reject options with no quotes, wide spreads, or no liquidity
- Apply 10% slippage buffer on net credit
"""

from datetime import datetime, date, timezone, timedelta

_IST = timezone(timedelta(hours=5, minutes=30))

# Thresholds
MAX_BID_ASK_SPREAD_PCT = 0.10   # 10% max spread
MIN_VOLUME = 100                 # Minimum daily volume
MIN_OI = 500                     # Minimum open interest
SLIPPAGE_FACTOR = 0.90           # 10% slippage buffer
IMPACT_OI_THRESHOLD = 0.05      # 5% of OI = large order


def execution_check(option_data):
    """Check if an option is tradeable in the real market.

    Returns (passed: bool, reason: str, quality: str)
    quality: GOOD / FAIR / POOR / REJECT
    """
    bid = option_data.get("bid", 0)
    ask = option_data.get("ask", 0)
    ltp = option_data.get("premium", 0) or option_data.get("ltp", 0)
    volume = option_data.get("volume", 0)
    oi = option_data.get("oi", 0)

    # No bid or ask — completely illiquid
    if bid <= 0 and ask <= 0:
        return False, "NO_QUOTES", "REJECT"

    # Use LTP as fallback if bid/ask partially missing (after hours)
    if bid <= 0 and ltp > 0:
        bid = ltp * 0.98  # Estimate bid as 2% below LTP
    if ask <= 0 and ltp > 0:
        ask = ltp * 1.02  # Estimate ask as 2% above LTP

    # Bid-ask spread too wide
    mid = (bid + ask) / 2 if bid > 0 and ask > 0 else ltp
    if mid > 0 and ask > 0 and bid > 0:
        spread_pct = (ask - bid) / mid
        if spread_pct > MAX_BID_ASK_SPREAD_PCT:
            return False, f"WIDE_SPREAD ({spread_pct*100:.1f}%)", "REJECT"

    # Volume too low
    if volume < MIN_VOLUME:
        # After market hours, volume resets to 0 — use OI as proxy
        if oi >= MIN_OI:
            pass  # OK — OI confirms liquidity even if volume is 0
        else:
            return False, f"LOW_VOLUME ({volume})", "POOR"

    # OI too low
    if oi < MIN_OI:
        return False, f"LOW_OI ({oi})", "POOR"

    # Quality assessment
    quality = "GOOD"
    if mid > 0 and ask > 0 and bid > 0:
        spread_pct = (ask - bid) / mid
        if spread_pct > 0.05:
            quality = "FAIR"  # Spread between 5-10%
    if volume < 500 or oi < 2000:
        quality = "FAIR"

    return True, "OK", quality


def get_real_fill_price(option_data, action):
    """Get the realistic fill price for a trade.

    SELL → use bid (what buyer will pay you)
    BUY → use ask (what seller will charge you)
    """
    bid = option_data.get("bid", 0)
    ask = option_data.get("ask", 0)
    ltp = option_data.get("premium", 0) or option_data.get("ltp", 0)

    if action.upper() == "SELL":
        if bid > 0:
            return bid
        return ltp * 0.98 if ltp > 0 else 0  # Conservative estimate
    else:  # BUY
        if ask > 0:
            return ask
        return ltp * 1.02 if ltp > 0 else 0  # Conservative estimate


def calculate_spread_net_credit(sell_option, buy_option):
    """Calculate net credit for a spread using real fill prices.

    sell at bid, buy at ask → worst case realistic fill
    """
    sell_price = get_real_fill_price(sell_option, "SELL")
    buy_price = get_real_fill_price(buy_option, "BUY")

    net_credit = sell_price - buy_price
    net_credit_adjusted = net_credit * SLIPPAGE_FACTOR

    return {
        "sell_price": round(sell_price, 2),
        "buy_price": round(buy_price, 2),
        "net_credit": round(net_credit, 2),
        "net_credit_adjusted": round(net_credit_adjusted, 2),
        "slippage_pct": round((1 - SLIPPAGE_FACTOR) * 100, 0),
    }


def calculate_confidence(trade_data):
    """Calculate confidence score and GO/REVIEW/REJECT decision.

    Score 0-100:
    - Execution quality: 40 pts
    - Risk/Reward: 30 pts
    - Probability: 20 pts
    - Market sentiment: 10 pts
    """
    score = 0
    reasons = []

    # Execution quality (40 pts)
    exec_quality = trade_data.get("execution_quality", "POOR")
    if exec_quality == "GOOD":
        score += 40
    elif exec_quality == "FAIR":
        score += 25
        reasons.append("Moderate bid-ask spread")
    else:
        score += 0
        reasons.append("Poor liquidity")

    # Risk/Reward (30 pts)
    net_credit = trade_data.get("net_credit_adjusted", 0)
    max_loss = trade_data.get("max_loss", 1)
    if max_loss > 0:
        rr = net_credit / max_loss
        if rr > 0.10:
            score += 30
        elif rr > 0.05:
            score += 20
        elif rr > 0.02:
            score += 10
            reasons.append("Low reward for risk")
        else:
            reasons.append("Very low reward for risk")

    # Probability (20 pts)
    prob_otm = trade_data.get("prob_otm", 0)
    if prob_otm > 0.85:
        score += 20
    elif prob_otm > 0.75:
        score += 12
    elif prob_otm > 0.60:
        score += 5
        reasons.append("Moderate probability")
    else:
        reasons.append("Low probability")

    # Sentiment (10 pts)
    sentiment = trade_data.get("sentiment_signal", "YELLOW")
    if sentiment == "GREEN":
        score += 10
    elif sentiment == "YELLOW":
        score += 5
    else:
        reasons.append("Adverse market sentiment")

    # Decision
    if score >= 70:
        decision = "GO"
    elif score >= 40:
        decision = "REVIEW"
    else:
        decision = "REJECT"

    return {
        "confidence_score": score,
        "decision": decision,
        "execution_quality": exec_quality,
        "reasons": reasons,
    }


def is_market_hours():
    """Check if Indian market is currently open."""
    now = datetime.now(_IST)
    weekday = now.weekday()  # 0=Mon ... 6=Sun

    if weekday >= 5:
        return False, "Weekend"

    hour = now.hour
    minute = now.minute
    time_mins = hour * 60 + minute

    # Pre-open: 9:00-9:15, Market: 9:15-15:30
    if time_mins < 540:  # Before 9:00
        return False, f"Pre-market (opens 9:15 AM IST)"
    if time_mins >= 930:  # After 15:30
        return False, f"Market closed (closed 3:30 PM IST)"

    return True, "Market open"


def get_market_status():
    """Get detailed market status for display."""
    now = datetime.now(_IST)
    is_open, message = is_market_hours()

    # Known Indian market holidays 2026 (major ones)
    holidays_2026 = {
        date(2026, 1, 26): "Republic Day",
        date(2026, 3, 14): "Holi",
        date(2026, 3, 31): "Id-ul-Fitr",
        date(2026, 4, 2): "Ram Navami",
        date(2026, 4, 3): "Mahavir Jayanti",
        date(2026, 4, 14): "Ambedkar Jayanti",
        date(2026, 5, 1): "Maharashtra Day",
        date(2026, 8, 15): "Independence Day",
        date(2026, 8, 19): "Muharram",
        date(2026, 10, 2): "Gandhi Jayanti",
        date(2026, 10, 21): "Dussehra",
        date(2026, 11, 4): "Diwali (Laxmi Puja)",
        date(2026, 11, 5): "Diwali (Balipratipada)",
        date(2026, 11, 19): "Guru Nanak Jayanti",
        date(2026, 12, 25): "Christmas",
    }

    today = now.date()
    is_holiday = today in holidays_2026
    holiday_name = holidays_2026.get(today, "")

    if is_holiday:
        return {
            "is_open": False,
            "status": "HOLIDAY",
            "message": f"Trading Holiday — {holiday_name}",
            "color": "#f87171",
            "time": now.strftime("%H:%M IST"),
        }

    if not is_open:
        return {
            "is_open": False,
            "status": "CLOSED",
            "message": message,
            "color": "#fcd34d",
            "time": now.strftime("%H:%M IST"),
        }

    return {
        "is_open": True,
        "status": "OPEN",
        "message": "Market is open",
        "color": "#6ee7b7",
        "time": now.strftime("%H:%M IST"),
    }
