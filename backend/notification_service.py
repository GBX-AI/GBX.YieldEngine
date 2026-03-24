"""
Notification Service — Yield Engine v3, Section 4C.

In-app notifications and reminders system.
Handles creation, retrieval, pagination, read-status management,
and deletion of notifications stored in SQLite.
"""

import logging
from datetime import datetime

from models import get_db, generate_id

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Notification type constants
# ---------------------------------------------------------------------------

SCAN_COMPLETE = "SCAN_COMPLETE"
TRADE_EXECUTED = "TRADE_EXECUTED"
TRADE_CLOSED = "TRADE_CLOSED"
EXPIRY_REMINDER = "EXPIRY_REMINDER"
TOKEN_EXPIRED = "TOKEN_EXPIRED"
AUTO_LOGIN_SUCCESS = "AUTO_LOGIN_SUCCESS"
AUTO_LOGIN_FAILED = "AUTO_LOGIN_FAILED"
MARGIN_WARNING = "MARGIN_WARNING"
POSITION_ALERT = "POSITION_ALERT"
DAILY_SUMMARY = "DAILY_SUMMARY"
PNL_MILESTONE = "PNL_MILESTONE"
NO_SCAN_REMINDER = "NO_SCAN_REMINDER"
STOP_LOSS_HIT = "STOP_LOSS_HIT"
DELTA_BREACH = "DELTA_BREACH"
MARKET_DROP = "MARKET_DROP"
EXPIRY_ITM_STT = "EXPIRY_ITM_STT"
DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
ADJUSTMENT_SUGGESTED = "ADJUSTMENT_SUGGESTED"
GTT_PLACED = "GTT_PLACED"
GTT_TRIGGERED = "GTT_TRIGGERED"
GTT_CANCELLED = "GTT_CANCELLED"

ALL_NOTIFICATION_TYPES = [
    SCAN_COMPLETE, TRADE_EXECUTED, TRADE_CLOSED, EXPIRY_REMINDER,
    TOKEN_EXPIRED, AUTO_LOGIN_SUCCESS, AUTO_LOGIN_FAILED, MARGIN_WARNING,
    POSITION_ALERT, DAILY_SUMMARY, PNL_MILESTONE, NO_SCAN_REMINDER,
    STOP_LOSS_HIT, DELTA_BREACH, MARKET_DROP, EXPIRY_ITM_STT,
    DAILY_LOSS_LIMIT, CIRCUIT_BREAKER, ADJUSTMENT_SUGGESTED,
    GTT_PLACED, GTT_TRIGGERED, GTT_CANCELLED,
]

# Severity levels
SEVERITY_INFO = "INFO"
SEVERITY_WARNING = "WARNING"
SEVERITY_URGENT = "URGENT"
SEVERITY_SUCCESS = "SUCCESS"


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def create_notification(ntype, title, message, severity=SEVERITY_INFO, action_url=None):
    """
    Insert a notification into the notifications table.

    Args:
        ntype:      One of the notification type constants above.
        title:      Short human-readable title.
        message:    Detailed notification body.
        severity:   INFO | WARNING | URGENT | SUCCESS.
        action_url: Optional deep-link URL for the frontend to navigate to.

    Returns:
        The generated notification ID.
    """
    nid = generate_id()
    db = get_db()
    try:
        db.execute(
            """
            INSERT INTO notifications (id, type, title, message, severity, read, action_url, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (nid, ntype, title, message, severity, action_url, datetime.utcnow().isoformat()),
        )
        db.commit()
        logger.info("Notification created: [%s] %s — %s", ntype, title, nid)
        return nid
    except Exception as e:
        logger.error("Failed to create notification: %s", e)
        db.rollback()
        raise
    finally:
        db.close()


def get_notifications(page=1, per_page=20):
    """
    Retrieve paginated notifications, newest first.

    Returns:
        dict with "notifications" list, "total", "page", "per_page", "pages".
    """
    db = get_db()
    try:
        total = db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
        offset = (page - 1) * per_page
        rows = db.execute(
            """
            SELECT id, type, title, message, severity, read, action_url, created_at
            FROM notifications
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        ).fetchall()

        notifications = [
            {
                "id": r["id"],
                "type": r["type"],
                "title": r["title"],
                "message": r["message"],
                "severity": r["severity"],
                "read": bool(r["read"]),
                "action_url": r["action_url"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

        pages = max(1, -(-total // per_page))  # ceil division
        return {
            "notifications": notifications,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages,
        }
    finally:
        db.close()


def get_unread_count():
    """Return the number of unread notifications (for badge display)."""
    db = get_db()
    try:
        row = db.execute("SELECT COUNT(*) FROM notifications WHERE read = 0").fetchone()
        return row[0]
    finally:
        db.close()


def mark_read(nid):
    """Mark a single notification as read."""
    db = get_db()
    try:
        db.execute("UPDATE notifications SET read = 1 WHERE id = ?", (nid,))
        db.commit()
        logger.debug("Notification marked read: %s", nid)
    finally:
        db.close()


def mark_all_read():
    """Mark every unread notification as read."""
    db = get_db()
    try:
        affected = db.execute("UPDATE notifications SET read = 1 WHERE read = 0").rowcount
        db.commit()
        logger.info("Marked %d notifications as read", affected)
        return affected
    finally:
        db.close()


def delete_notification(nid):
    """Delete a notification by ID."""
    db = get_db()
    try:
        db.execute("DELETE FROM notifications WHERE id = ?", (nid,))
        db.commit()
        logger.debug("Notification deleted: %s", nid)
    finally:
        db.close()
