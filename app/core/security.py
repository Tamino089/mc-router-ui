"""
Password hashing, verification, and session helpers.
"""

import os
import base64
import hashlib
import secrets

from fastapi import Request


# ── Password hashing (pbkdf2_sha256) ─────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    pwdhash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, 100_000
    )
    return (
        f"pbkdf2_sha256$100000$"
        f"{base64.b64encode(salt).decode('utf-8')}$"
        f"{base64.b64encode(pwdhash).decode('utf-8')}"
    )


def verify_password(password: str, hashed: str) -> bool:
    try:
        parts = hashed.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False
        iterations = int(parts[1])
        salt = base64.b64decode(parts[2].encode("utf-8"))
        pwdhash = base64.b64decode(parts[3].encode("utf-8"))
        new_hash = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )
        return secrets.compare_digest(new_hash, pwdhash)
    except Exception:
        return False


# ── Session helpers ───────────────────────────────────────────────────────────

def current_user(request: Request) -> dict | None:
    session_user = request.session.get("user")
    if not isinstance(session_user, dict) or not session_user.get("id"):
        return None

    # Session cookies prove continuity, but the database remains authoritative
    # for whether the account still exists and which role it currently has.
    from app.db.database import get_db

    with get_db() as con:
        row = con.execute(
            "SELECT id, username, role FROM users WHERE id=?",
            (session_user["id"],),
        ).fetchone()

    if not row:
        request.session.clear()
        return None

    user = dict(row)
    if user != session_user:
        request.session["user"] = user
    return user
