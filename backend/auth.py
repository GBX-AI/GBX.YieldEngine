"""
JWT Authentication blueprint for Yield Engine.
Provides signup, login, token refresh, and @require_auth decorator.
"""

import functools
import os
from datetime import datetime, timedelta, timezone

import jwt
import bcrypt as _bcrypt
from flask import Blueprint, request, jsonify, g

from models import create_user, get_user_by_email, get_user_by_id, generate_id

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

    if not email or not name or not password:
        return jsonify({"error": "Email, name, and password are required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    existing = get_user_by_email(email)
    if existing:
        return jsonify({"error": "Email already registered"}), 409

    password_hash = _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")
    user = create_user(email, name, password_hash)

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

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    user = get_user_by_email(email)
    if not user or not _bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        return jsonify({"error": "Invalid email or password"}), 401

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
