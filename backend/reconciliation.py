"""
Reconciliation Service — Yield Engine v3, Section 4L.

Post-order verification layer. After every order is placed via Kite,
the reconciliation service fetches the order book and verifies that
the executed order matches expected parameters.

On mismatch: locks execution to READONLY mode and creates an URGENT
notification so the operator can investigate.

Statuses:
  VERIFIED        — Order matches expected params.
  MISMATCH        — Order found but params don't match.
  ORDER_NOT_FOUND — Order ID not found in Kite order book.
  KITE_REJECTED   — Order was rejected by Kite/exchange.
"""

import logging
from datetime import datetime

from models import get_db, generate_id
from notification_service import create_notification

logger = logging.getLogger(__name__)

# Reconciliation result statuses
STATUS_VERIFIED = "VERIFIED"
STATUS_MISMATCH = "MISMATCH"
STATUS_ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
STATUS_KITE_REJECTED = "KITE_REJECTED"

# Fields to reconcile
_RECONCILE_FIELDS = [
    "tradingsymbol",
    "exchange",
    "transaction_type",
    "product",
    "quantity",
]


def _lock_execution_mode():
    """Set execution mode to READONLY to prevent further orders."""
    db = get_db()
    try:
        # Use settings table to store execution lock
        db.execute(
            """
            INSERT INTO settings (key, value) VALUES ('execution_mode', 'READONLY')
            ON CONFLICT(key) DO UPDATE SET value = 'READONLY'
            """,
        )
        db.commit()
        logger.critical("EXECUTION LOCKED TO READONLY — reconciliation mismatch detected")
    except Exception as e:
        logger.error("Failed to lock execution mode: %s", e)
        db.rollback()
    finally:
        db.close()


def _log_reconciliation(trade_id, kite_order_id, status, details):
    """Write a reconciliation record to the DB for audit."""
    db = get_db()
    try:
        db.execute(
            """
            INSERT INTO notifications (id, type, title, message, severity, read, action_url, created_at)
            VALUES (?, 'RECONCILIATION', ?, ?, ?, 0, ?, ?)
            """,
            (
                generate_id(),
                f"Reconciliation: {status}",
                details,
                "URGENT" if status != STATUS_VERIFIED else "INFO",
                f"/trades/{trade_id}" if trade_id else "/trades",
                datetime.utcnow().isoformat(),
            ),
        )
        db.commit()
    except Exception as e:
        logger.error("Failed to log reconciliation: %s", e)
    finally:
        db.close()


def reconcile_order(expected_params, kite_order_id, kite_client):
    """
    Verify a placed order against the Kite order book.

    Args:
        expected_params: dict with expected order fields:
            - tradingsymbol (str)
            - exchange (str)
            - transaction_type (str): "BUY" or "SELL"
            - product (str): "NRML" or "CNC"
            - quantity (int)
            - price (float, optional)
            - trade_id (str, optional): internal trade ID for linking

        kite_order_id: str — the order ID returned by Kite on placement.

        kite_client: Kite API client instance with .orders() method.

    Returns:
        dict: {
            "status": "VERIFIED" | "MISMATCH" | "ORDER_NOT_FOUND" | "KITE_REJECTED",
            "alert": bool,
            "message": str,
        }
    """
    trade_id = expected_params.get("trade_id", "")

    # ------------------------------------------------------------------
    # Fetch order book from Kite
    # ------------------------------------------------------------------
    try:
        order_book = kite_client.orders()
    except Exception as e:
        msg = f"Failed to fetch Kite order book: {e}"
        logger.error(msg)
        create_notification(
            "POSITION_ALERT",
            "Reconciliation error",
            msg,
            severity="URGENT",
            action_url=f"/trades/{trade_id}" if trade_id else "/trades",
        )
        return {"status": STATUS_ORDER_NOT_FOUND, "alert": True, "message": msg}

    # ------------------------------------------------------------------
    # Find the order by ID
    # ------------------------------------------------------------------
    kite_order = None
    for order in order_book:
        if order.get("order_id") == kite_order_id:
            kite_order = order
            break

    if kite_order is None:
        msg = f"Order {kite_order_id} not found in Kite order book."
        logger.error(msg)

        _lock_execution_mode()
        create_notification(
            "POSITION_ALERT",
            "Order not found — READONLY mode",
            msg,
            severity="URGENT",
            action_url=f"/trades/{trade_id}" if trade_id else "/trades",
        )
        _log_reconciliation(trade_id, kite_order_id, STATUS_ORDER_NOT_FOUND, msg)

        return {"status": STATUS_ORDER_NOT_FOUND, "alert": True, "message": msg}

    # ------------------------------------------------------------------
    # Check if Kite rejected the order
    # ------------------------------------------------------------------
    kite_status = kite_order.get("status", "").upper()
    if kite_status == "REJECTED":
        reason = kite_order.get("status_message", "No reason provided")
        msg = f"Order {kite_order_id} rejected by Kite: {reason}"
        logger.error(msg)

        _lock_execution_mode()
        create_notification(
            "POSITION_ALERT",
            "Order rejected — READONLY mode",
            msg,
            severity="URGENT",
            action_url=f"/trades/{trade_id}" if trade_id else "/trades",
        )
        _log_reconciliation(trade_id, kite_order_id, STATUS_KITE_REJECTED, msg)

        return {"status": STATUS_KITE_REJECTED, "alert": True, "message": msg}

    # ------------------------------------------------------------------
    # Verify each expected field matches the Kite order
    # ------------------------------------------------------------------
    mismatches = []

    for field in _RECONCILE_FIELDS:
        expected_val = expected_params.get(field)
        actual_val = kite_order.get(field)

        if expected_val is None:
            continue

        # Normalize for comparison
        if isinstance(expected_val, str):
            expected_val = expected_val.upper().strip()
        if isinstance(actual_val, str):
            actual_val = actual_val.upper().strip()

        # Quantity comparison: Kite may report filled_quantity
        if field == "quantity":
            actual_val = kite_order.get("filled_quantity", actual_val)
            try:
                expected_val = int(expected_val)
                actual_val = int(actual_val)
            except (TypeError, ValueError):
                pass

        if expected_val != actual_val:
            mismatches.append(
                f"{field}: expected={expected_val}, actual={actual_val}"
            )

    # ------------------------------------------------------------------
    # Also check price deviation if expected price provided
    # ------------------------------------------------------------------
    expected_price = expected_params.get("price")
    if expected_price and expected_price > 0:
        actual_price = kite_order.get("average_price", 0)
        if actual_price and actual_price > 0:
            deviation = abs(actual_price - expected_price) / expected_price
            if deviation > 0.20:
                mismatches.append(
                    f"price: expected={expected_price:.2f}, "
                    f"actual={actual_price:.2f} (deviation={deviation:.1%})"
                )

    # ------------------------------------------------------------------
    # Return result
    # ------------------------------------------------------------------
    if mismatches:
        detail_str = "; ".join(mismatches)
        msg = f"Order {kite_order_id} MISMATCH: {detail_str}"
        logger.error(msg)

        _lock_execution_mode()
        create_notification(
            "POSITION_ALERT",
            "Order mismatch — READONLY mode",
            msg,
            severity="URGENT",
            action_url=f"/trades/{trade_id}" if trade_id else "/trades",
        )
        _log_reconciliation(trade_id, kite_order_id, STATUS_MISMATCH, msg)

        return {"status": STATUS_MISMATCH, "alert": True, "message": msg}

    # All checks passed
    msg = f"Order {kite_order_id} verified successfully."
    logger.info(msg)
    _log_reconciliation(trade_id, kite_order_id, STATUS_VERIFIED, msg)

    return {"status": STATUS_VERIFIED, "alert": False, "message": msg}
