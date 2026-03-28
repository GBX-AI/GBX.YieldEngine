"""
Statutory & brokerage charges calculator for NSE F&O options trades.
All rates per Zerodha's published fee structure (updated April 2024).

Computes true net P&L after all charges for any options strategy.
"""


class ChargesEngine:
    # Zerodha brokerage
    BROKERAGE_PER_ORDER = 20  # Flat ₹20 per executed order

    # STT (Securities Transaction Tax) — updated April 2024
    STT_SELL_RATE = 0.000625     # 0.0625% on sell side premium (options)
    STT_EXERCISE_RATE = 0.00125  # 0.125% on intrinsic value if ITM at expiry

    # Exchange transaction charges (NSE)
    NSE_EXCHANGE_RATE = 0.0495 / 100  # ₹0.0495 per ₹100 = 0.0495%

    # SEBI turnover fee
    SEBI_RATE = 10 / 10_000_000  # ₹10 per crore

    # GST — 18% on (brokerage + exchange charges + SEBI charges)
    GST_RATE = 0.18

    # Stamp duty — buy side only
    STAMP_DUTY_BUY_RATE = 0.00003  # 0.003% on buy side

    def calculate(self, legs, lot_size, lots):
        """
        Calculate all charges for a multi-leg options strategy.

        Args:
            legs: list of dicts, each with:
                - action: 'SELL' or 'BUY'
                - premium: float (per share)
                - strike: float
                - option_type: 'CE' or 'PE'
            lot_size: int (shares per lot)
            lots: int (number of lots)

        Returns dict with gross, net, charges breakdown.
        """
        qty = lot_size * lots
        gross_received = 0
        gross_paid = 0
        brokerage = 0
        stt = 0
        exchange = 0
        sebi = 0
        stamp = 0

        for leg in legs:
            premium = leg.get("premium", 0)
            turnover = abs(premium) * qty
            action = (leg.get("action") or "").upper()

            # Brokerage: ₹20 per order (per leg)
            brokerage += self.BROKERAGE_PER_ORDER

            # Exchange charges on turnover
            exchange += turnover * self.NSE_EXCHANGE_RATE

            # SEBI charges on turnover
            sebi += turnover * self.SEBI_RATE

            if action == "SELL":
                gross_received += premium * qty
                # STT only on sell side
                stt += turnover * self.STT_SELL_RATE
            else:
                gross_paid += premium * qty
                # Stamp duty only on buy side
                stamp += turnover * self.STAMP_DUTY_BUY_RATE

        # GST: 18% on (brokerage + exchange + SEBI)
        gst = (brokerage + exchange + sebi) * self.GST_RATE

        total_charges = brokerage + stt + exchange + sebi + gst + stamp
        net_premium = (gross_received - gross_paid) - total_charges

        return {
            "gross_premium_received": round(gross_received, 2),
            "gross_premium_paid": round(gross_paid, 2),
            "net_credit": round(gross_received - gross_paid, 2),
            "total_charges": round(total_charges, 2),
            "net_premium": round(net_premium, 2),
            "charges_breakdown": {
                "brokerage": round(brokerage, 2),
                "stt": round(stt, 2),
                "exchange_charges": round(exchange, 2),
                "sebi_charges": round(sebi, 2),
                "gst": round(gst, 2),
                "stamp_duty": round(stamp, 2),
            },
            "charges_per_share": round(total_charges / qty, 4) if qty else 0,
            "effective_breakeven_adjustment": round(total_charges / qty, 2) if qty else 0,
        }

    def estimate_exit_charges(self, legs, lot_size, lots):
        """Estimate charges for closing/exiting a position (buying back what was sold, selling what was bought)."""
        exit_legs = []
        for leg in legs:
            action = (leg.get("action") or "").upper()
            exit_legs.append({
                **leg,
                "action": "BUY" if action == "SELL" else "SELL",
            })
        return self.calculate(exit_legs, lot_size, lots)


# Module-level singleton
charges_engine = ChargesEngine()
