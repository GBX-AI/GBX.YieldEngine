"""
Email service for Yield Engine — sends password reset emails via Gmail SMTP.
"""

import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
FROM_NAME = os.getenv("SMTP_FROM_NAME", "Yield Engine")


def send_reset_email(to_email: str, reset_url: str, user_name: str = "") -> bool:
    """Send a password reset email. Returns True on success."""
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.error("SMTP credentials not configured")
        return False

    subject = "Reset your Yield Engine password"
    greeting = f"Hi {user_name}," if user_name else "Hi,"

    html_body = f"""
    <div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 480px; margin: 0 auto; padding: 32px; background: #0a0f1a; color: #e2e8f0; border-radius: 12px;">
      <div style="text-align: center; margin-bottom: 24px;">
        <span style="color: #6ee7b7; font-size: 24px;">&#9670;</span>
        <span style="font-family: monospace; font-weight: 600; font-size: 18px; color: #e2e8f0; margin-left: 8px;">Yield Engine</span>
      </div>
      <p style="font-size: 14px; line-height: 1.6;">{greeting}</p>
      <p style="font-size: 14px; line-height: 1.6;">
        We received a request to reset your password. Click the button below to set a new password.
        This link expires in <strong>1 hour</strong>.
      </p>
      <div style="text-align: center; margin: 28px 0;">
        <a href="{reset_url}" style="display: inline-block; padding: 12px 32px; background: #6ee7b7; color: #0a0f1a; font-weight: 600; font-size: 14px; text-decoration: none; border-radius: 8px;">
          Reset Password
        </a>
      </div>
      <p style="font-size: 12px; color: #94a3b8; line-height: 1.6;">
        If you didn't request this, you can safely ignore this email. Your password won't change.
      </p>
      <hr style="border: none; border-top: 1px solid rgba(148,163,184,0.15); margin: 24px 0;" />
      <p style="font-size: 11px; color: #64748b; text-align: center;">
        Yield Engine &mdash; Options Analytics Platform
      </p>
    </div>
    """

    text_body = f"""{greeting}

We received a request to reset your Yield Engine password.

Reset your password: {reset_url}

This link expires in 1 hour. If you didn't request this, ignore this email.

— Yield Engine"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{FROM_NAME} <{SMTP_USER}>"
        msg["To"] = to_email

        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)

        logger.info("Reset email sent to %s", to_email)
        return True

    except Exception as exc:
        logger.error("Failed to send reset email to %s: %s", to_email, exc)
        return False
