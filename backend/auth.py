"""
JWT Authentication blueprint for Yield Engine.
Provides signup, login, token refresh, and @require_auth decorator.
"""

import functools
import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
import bcrypt as _bcrypt
from flask import Blueprint, request, jsonify, g

from models import (
    create_user, get_user_by_email, get_user_by_id, generate_id,
    create_reset_token, get_valid_reset_token, mark_reset_token_used, update_user_password,
)
from email_service import send_reset_email

logger = logging.getLogger(__name__)

# Configure logging to show in gunicorn output
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

JWT_SECRET = os.getenv("JWT_SECRET", "yield-engine-dev-secret-change-in-prod")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRY_MINUTES = 30
REFRESH_TOKEN_EXPIRY_DAYS = 7


# ─── Token helpers ───────────────────────────────────────────────────────────

def _create_access_token(user_id, email):
    payload = {
        "sub": user_id,
        "email": email,
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRY_MINUTES),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _create_refresh_token(user_id, email):
    payload = {
        "sub": user_id,
        "email": email,
        "type": "refresh",
        "exp": datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRY_DAYS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _create_tokens(user_id, email):
    return {
        "access_token": _create_access_token(user_id, email),
        "refresh_token": _create_refresh_token(user_id, email),
    }


# ─── Decorator ───────────────────────────────────────────────────────────────

def require_auth(f):
    """Decorator that validates JWT Bearer token and sets g.current_user."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing authorization header"}), 401

        token = auth_header[7:]
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            if payload.get("type") != "access":
                return jsonify({"error": "Invalid token type"}), 401
            g.current_user = {"id": payload["sub"], "email": payload["email"]}
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "token_expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401

        return f(*args, **kwargs)
    return decorated


# ─── Routes ──────────────────────────────────────────────────────────────────

@auth_bp.route("/signup", methods=["POST"])
def signup():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    name = (data.get("name") or "").strip()
    password = data.get("password", "")
    logger.info("Signup attempt: %s", email)

    if not email or not name or not password:
        return jsonify({"error": "Email, name, and password are required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    existing = get_user_by_email(email)
    if existing:
        logger.warning("Signup failed: email already registered: %s", email)
        return jsonify({"error": "Email already registered"}), 409

    password_hash = _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")
    user = create_user(email, name, password_hash)
    logger.info("User created: %s (id: %s)", email, user["id"])

    tokens = _create_tokens(user["id"], user["email"])
    return jsonify({
        "user": {"id": user["id"], "email": user["email"], "name": user["name"]},
        **tokens,
    }), 201


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password", "")
    logger.info("Login attempt: %s", email)

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    user = get_user_by_email(email)
    if not user:
        logger.warning("Login failed: email not found: %s", email)
        return jsonify({"error": "Invalid email or password"}), 401

    try:
        pw_ok = _bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8"))
    except Exception as exc:
        logger.error("Login bcrypt error for %s: %s (hash prefix: %s)", email, exc, user["password_hash"][:10])
        return jsonify({"error": "Invalid email or password"}), 401

    if not pw_ok:
        logger.warning("Login failed: wrong password for %s", email)
        return jsonify({"error": "Invalid email or password"}), 401

    logger.info("Login successful: %s", email)
    tokens = _create_tokens(user["id"], user["email"])
    return jsonify({
        "user": {"id": user["id"], "email": user["email"], "name": user["name"]},
        **tokens,
    })


@auth_bp.route("/refresh", methods=["POST"])
def refresh():
    data = request.json or {}
    refresh_token = data.get("refresh_token", "")

    if not refresh_token:
        return jsonify({"error": "Refresh token is required"}), 400

    try:
        payload = jwt.decode(refresh_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            return jsonify({"error": "Invalid token type"}), 401
    except jwt.ExpiredSignatureError:
        return jsonify({"error": "Refresh token expired"}), 401
    except jwt.InvalidTokenError:
        return jsonify({"error": "Invalid refresh token"}), 401

    # Issue new access token
    user = get_user_by_id(payload["sub"])
    if not user:
        return jsonify({"error": "User not found"}), 401

    access_token = _create_access_token(user["id"], user["email"])
    return jsonify({"access_token": access_token})


@auth_bp.route("/logout", methods=["POST"])
def logout():
    # Stateless JWT — frontend discards tokens. No server-side blacklist needed for now.
    return jsonify({"message": "Logged out"})


@auth_bp.route("/me", methods=["GET"])
@require_auth
def me():
    user = get_user_by_id(g.current_user["id"])
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "kite_connected": bool(user.get("kite_access_token")),
        "kite_user_id": user.get("kite_user_id"),
    })


# ─── Forgot / Reset Password ────────────────────────────────────────────────

FRONTEND_URL = os.getenv("FRONTEND_URL", "https://yield-engine-web.whiteocean-b818a22a.centralindia.azurecontainerapps.io")


@auth_bp.route("/forgot-password", methods=["POST"])
def forgot_password():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "Email is required"}), 400

    # Always return success (don't reveal if email exists)
    user = get_user_by_email(email)
    if user:
        logger.info("Forgot password: generating reset token for %s", email)
        raw_token = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = (datetime.utcnow() + timedelta(hours=1)).isoformat()

        create_reset_token(user["id"], token_hash, expires_at)

        reset_url = f"{FRONTEND_URL}/reset-password?token={raw_token}"
        logger.info("Forgot password: sending email to %s, reset URL generated", email)
        email_sent = send_reset_email(user["email"], reset_url, user.get("name", ""))
        if not email_sent:
            logger.error("Forgot password: FAILED to send email to %s", email)
        else:
            logger.info("Forgot password: email sent successfully to %s", email)
    else:
        logger.info("Forgot password: no account found for %s (not revealing to client)", email)

    return jsonify({"message": "If an account exists with that email, a reset link has been sent."})


@auth_bp.route("/reset-password", methods=["POST"])
def reset_password():
    data = request.json or {}
    token = data.get("token", "")
    new_password = data.get("password", "")

    if not token or not new_password:
        return jsonify({"error": "Token and new password are required"}), 400
    if len(new_password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    reset_record = get_valid_reset_token(token_hash)

    if not reset_record:
        return jsonify({"error": "Invalid or expired reset link. Please request a new one."}), 400

    # Update password
    password_hash = _bcrypt.hashpw(new_password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")
    update_user_password(reset_record["user_id"], password_hash)
    mark_reset_token_used(reset_record["id"])

    return jsonify({"message": "Password has been reset. You can now login with your new password."})
