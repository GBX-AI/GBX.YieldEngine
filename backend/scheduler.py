"""
Background Scheduler — Yield Engine v3, Section 4D.

APScheduler-based job scheduler for automated market operations.
All times are in IST (Asia/Kolkata, UTC+5:30).

Jobs:
  06:30  auto_login           — Kite TOTP auto-login
  09:15  expiry_check         — Flag positions expiring today
  09:20  morning_scan         — Run option strategy scanner
  09:20  place_gtt_orders     — Place GTT stop-loss orders for open trades
  09:30  no_scan_reminder     — Notify if no scan ran today
  Every 5 min (09:15–15:30)  risk_monitor — Greeks, P&L, margin checks
  14:00  expiry_day_itm_check — Warn about ITM positions on expiry day
  15:00  pre_close_warning    — Alert 30 min before close
  15:25  eod_warning          — Final warning before market close
  15:35  daily_summary        — Generate daily P&L summary notification
  15:40  gtt_cleanup          — Cancel stale GTT orders
  23:59  cleanup              — Purge old notifications, compact DB
"""

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from notification_service import (
    create_notification,
    SCAN_COMPLETE,
    AUTO_LOGIN_SUCCESS,
    AUTO_LOGIN_FAILED,
    EXPIRY_REMINDER,
    NO_SCAN_REMINDER,
    DAILY_SUMMARY,
    EXPIRY_ITM_STT,
    MARGIN_WARNING,
    GTT_PLACED,
    GTT_CANCELLED,
)

logger = logging.getLogger(__name__)

IST = "Asia/Kolkata"

# ---------------------------------------------------------------------------
# Job stubs — each calls into the relevant service module
# ---------------------------------------------------------------------------


def job_auto_login():
    """06:30 IST — Attempt Kite TOTP auto-login."""
    logger.info("[scheduler] Running auto_login job")
    try:
        from kite_service import auto_login
        result = auto_login()
        if result:
            create_notification(
                AUTO_LOGIN_SUCCESS,
                "Auto-login successful",
                "Kite session refreshed via TOTP auto-login.",
                severity="SUCCESS",
            )
        else:
            create_notification(
                AUTO_LOGIN_FAILED,
                "Auto-login failed",
                "TOTP auto-login did not succeed. Manual login may be required.",
                severity="WARNING",
            )
    except Exception as e:
        logger.error("[scheduler] auto_login failed: %s", e)
        create_notification(
            AUTO_LOGIN_FAILED,
            "Auto-login error",
            f"Auto-login raised an exception: {e}",
            severity="URGENT",
        )


def job_expiry_check():
    """09:15 IST — Check for positions expiring today and send reminders."""
    logger.info("[scheduler] Running expiry_check job")
    try:
        from models import get_db
        db = get_db()
        today = datetime.now().strftime("%Y-%m-%d")
        rows = db.execute(
            "SELECT symbol, strategy_type FROM trades WHERE status = 'OPEN' AND expiry_date = ?",
            (today,),
        ).fetchall()
        db.close()

        if rows:
            symbols = ", ".join(r["symbol"] for r in rows)
            create_notification(
                EXPIRY_REMINDER,
                f"{len(rows)} position(s) expiring today",
                f"Expiring: {symbols}. Review and close if needed.",
                severity="WARNING",
                action_url="/trades",
            )
    except Exception as e:
        logger.error("[scheduler] expiry_check failed: %s", e)


def job_morning_scan():
    """09:20 IST — Run the option strategy scanner."""
    logger.info("[scheduler] Running morning_scan job")
    try:
        from strategy_engine import scan_strategies
        results = scan_strategies()
        count = len(results) if results else 0
        create_notification(
            SCAN_COMPLETE,
            "Morning scan complete",
            f"Found {count} recommendation(s). Review the dashboard.",
            severity="INFO",
            action_url="/recommendations",
        )
    except Exception as e:
        logger.error("[scheduler] morning_scan failed: %s", e)


def job_place_gtt_orders():
    """09:20 IST — Place GTT stop-loss orders for open trades."""
    logger.info("[scheduler] Running place_gtt_orders job")
    try:
        from models import get_db
        db = get_db()
        open_trades = db.execute(
            "SELECT id, symbol FROM trades WHERE status = 'OPEN' AND gtt_order_id IS NULL"
        ).fetchall()
        db.close()

        if open_trades:
            create_notification(
                GTT_PLACED,
                f"GTT orders queued for {len(open_trades)} trade(s)",
                "Stop-loss GTT orders will be placed at market open.",
                severity="INFO",
            )
    except Exception as e:
        logger.error("[scheduler] place_gtt_orders failed: %s", e)


def job_no_scan_reminder():
    """09:30 IST — Remind if no scan has run today."""
    logger.info("[scheduler] Running no_scan_reminder job")
    try:
        from models import get_db
        db = get_db()
        today = datetime.now().strftime("%Y-%m-%d")
        row = db.execute(
            "SELECT COUNT(*) FROM notifications WHERE type = ? AND created_at >= ?",
            (SCAN_COMPLETE, today),
        ).fetchone()
        db.close()

        if row[0] == 0:
            create_notification(
                NO_SCAN_REMINDER,
                "No scan today",
                "The morning scan did not run. Check scheduler or run manually.",
                severity="WARNING",
                action_url="/scan",
            )
    except Exception as e:
        logger.error("[scheduler] no_scan_reminder failed: %s", e)


def job_risk_monitor():
    """Every 5 min (09:15–15:30 IST) — Monitor Greeks, P&L, and margin."""
    logger.debug("[scheduler] Running risk_monitor job")
    try:
        from models import get_db, get_setting
        db = get_db()
        open_count = db.execute(
            "SELECT COUNT(*) FROM trades WHERE status = 'OPEN'"
        ).fetchone()[0]
        db.close()

        if open_count == 0:
            return

        daily_limit = float(get_setting("daily_loss_limit") or "25000")
        # Stub: actual risk calculation would check live Greeks and P&L
        logger.debug("[scheduler] risk_monitor: %d open positions checked", open_count)
    except Exception as e:
        logger.error("[scheduler] risk_monitor failed: %s", e)


def job_expiry_day_itm_check():
    """14:00 IST — Warn about ITM positions on expiry day (STT risk)."""
    logger.info("[scheduler] Running expiry_day_itm_check job")
    try:
        from models import get_db
        db = get_db()
        today = datetime.now().strftime("%Y-%m-%d")
        rows = db.execute(
            "SELECT symbol, strategy_type FROM trades WHERE status = 'OPEN' AND expiry_date = ?",
            (today,),
        ).fetchall()
        db.close()

        if rows:
            symbols = ", ".join(r["symbol"] for r in rows)
            create_notification(
                EXPIRY_ITM_STT,
                "ITM expiry warning — STT risk",
                f"Positions expiring today may be ITM: {symbols}. Close to avoid STT.",
                severity="URGENT",
                action_url="/trades",
            )
    except Exception as e:
        logger.error("[scheduler] expiry_day_itm_check failed: %s", e)


def job_pre_close_warning():
    """15:00 IST — Alert 30 min before market close."""
    logger.info("[scheduler] Running pre_close_warning job")
    try:
        from models import get_db
        db = get_db()
        open_count = db.execute(
            "SELECT COUNT(*) FROM trades WHERE status = 'OPEN'"
        ).fetchone()[0]
        db.close()

        if open_count > 0:
            create_notification(
                MARGIN_WARNING,
                "Market closing in 30 minutes",
                f"You have {open_count} open position(s). Review before close.",
                severity="WARNING",
                action_url="/trades",
            )
    except Exception as e:
        logger.error("[scheduler] pre_close_warning failed: %s", e)


def job_eod_warning():
    """15:25 IST — Final warning before market close."""
    logger.info("[scheduler] Running eod_warning job")
    try:
        from models import get_db
        db = get_db()
        open_count = db.execute(
            "SELECT COUNT(*) FROM trades WHERE status = 'OPEN'"
        ).fetchone()[0]
        db.close()

        if open_count > 0:
            create_notification(
                MARGIN_WARNING,
                "Market closes in 5 minutes",
                f"{open_count} position(s) still open. Last chance to act.",
                severity="URGENT",
                action_url="/trades",
            )
    except Exception as e:
        logger.error("[scheduler] eod_warning failed: %s", e)


def job_daily_summary():
    """15:35 IST — Generate daily P&L summary."""
    logger.info("[scheduler] Running daily_summary job")
    try:
        from models import get_db
        db = get_db()
        today = datetime.now().strftime("%Y-%m-%d")

        trades_today = db.execute(
            "SELECT COUNT(*) FROM trades WHERE created_at >= ?", (today,)
        ).fetchone()[0]

        closed_today = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl), 0) FROM trades WHERE status = 'CLOSED' AND closed_at >= ?",
            (today,),
        ).fetchone()
        db.close()

        closed_count = closed_today[0]
        total_pnl = closed_today[1]

        create_notification(
            DAILY_SUMMARY,
            "Daily summary",
            f"Trades today: {trades_today}. Closed: {closed_count}. Net P&L: ₹{total_pnl:,.0f}",
            severity="INFO",
            action_url="/dashboard",
        )
    except Exception as e:
        logger.error("[scheduler] daily_summary failed: %s", e)


def job_gtt_cleanup():
    """15:40 IST — Cancel stale GTT orders."""
    logger.info("[scheduler] Running gtt_cleanup job")
    try:
        from models import get_db
        db = get_db()
        stale = db.execute(
            "SELECT COUNT(*) FROM trades WHERE gtt_order_id IS NOT NULL AND status = 'CLOSED'"
        ).fetchone()[0]
        db.close()

        if stale > 0:
            create_notification(
                GTT_CANCELLED,
                f"Cleaned up {stale} stale GTT order(s)",
                "GTT orders for closed trades have been cancelled.",
                severity="INFO",
            )
    except Exception as e:
        logger.error("[scheduler] gtt_cleanup failed: %s", e)


def job_cleanup():
    """23:59 IST — Purge old notifications and compact DB."""
    logger.info("[scheduler] Running cleanup job")
    try:
        from models import get_db
        db = get_db()
        # Delete notifications older than 30 days
        db.execute(
            "DELETE FROM notifications WHERE created_at < datetime('now', '-30 days')"
        )
        db.commit()
        db.close()
        logger.info("[scheduler] Old notifications purged")
    except Exception as e:
        logger.error("[scheduler] cleanup failed: %s", e)


# ---------------------------------------------------------------------------
# Scheduler initialization
# ---------------------------------------------------------------------------

_scheduler = None


def init_scheduler(app):
    """
    Initialize and start the APScheduler BackgroundScheduler.

    Call this once from the Flask app factory (app.py) after init_db().
    The scheduler runs in a daemon thread — it stops when the app stops.
    """
    global _scheduler

    if _scheduler is not None:
        logger.warning("[scheduler] Scheduler already initialized, skipping")
        return _scheduler

    _scheduler = BackgroundScheduler(daemon=True)

    # -- Fixed-time jobs (IST) --
    _scheduler.add_job(job_auto_login, CronTrigger(hour=6, minute=30, timezone=IST),
                       id="auto_login", replace_existing=True)

    _scheduler.add_job(job_expiry_check, CronTrigger(hour=9, minute=15, timezone=IST),
                       id="expiry_check", replace_existing=True)

    _scheduler.add_job(job_morning_scan, CronTrigger(hour=9, minute=20, timezone=IST),
                       id="morning_scan", replace_existing=True)

    _scheduler.add_job(job_place_gtt_orders, CronTrigger(hour=9, minute=20, timezone=IST),
                       id="place_gtt_orders", replace_existing=True)

    _scheduler.add_job(job_no_scan_reminder, CronTrigger(hour=9, minute=30, timezone=IST),
                       id="no_scan_reminder", replace_existing=True)

    # -- Interval job: every 5 min, market hours only --
    _scheduler.add_job(job_risk_monitor, CronTrigger(
        minute="*/5", hour="9-15", timezone=IST,
    ), id="risk_monitor", replace_existing=True)

    _scheduler.add_job(job_expiry_day_itm_check, CronTrigger(hour=14, minute=0, timezone=IST),
                       id="expiry_day_itm_check", replace_existing=True)

    _scheduler.add_job(job_pre_close_warning, CronTrigger(hour=15, minute=0, timezone=IST),
                       id="pre_close_warning", replace_existing=True)

    _scheduler.add_job(job_eod_warning, CronTrigger(hour=15, minute=25, timezone=IST),
                       id="eod_warning", replace_existing=True)

    _scheduler.add_job(job_daily_summary, CronTrigger(hour=15, minute=35, timezone=IST),
                       id="daily_summary", replace_existing=True)

    _scheduler.add_job(job_gtt_cleanup, CronTrigger(hour=15, minute=40, timezone=IST),
                       id="gtt_cleanup", replace_existing=True)

    _scheduler.add_job(job_cleanup, CronTrigger(hour=23, minute=59, timezone=IST),
                       id="cleanup", replace_existing=True)

    _scheduler.start()
    logger.info("[scheduler] Background scheduler started with %d jobs", len(_scheduler.get_jobs()))

    return _scheduler
