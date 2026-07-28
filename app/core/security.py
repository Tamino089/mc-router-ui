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
    """Return the current authenticated user dict, or None.

    The role field is re-fetched from the DB on every call so that
    demotions, permission changes, and user-deletion take effect
    immediately without requiring a re-login.
    """
    session_user = request.session.get("user")
    if not session_user:
        return None

    user_id = session_user.get("id")
    if not user_id:
        return None

    # Deferred import to avoid circular dependency at module level
    from app.db.database import get_db

    try:
        with get_db() as con:
            row = con.execute(
                "SELECT role FROM users WHERE id=?", (user_id,)
            ).fetchone()
    except Exception:
        # DB unreachable — use cached session data rather than fail-closed
        return session_user

    if not row:
        # User was deleted — clear session
        request.session.pop("user", None)
        return None

    # Keep id/username from session, refresh role from DB
    session_user["role"] = row["role"]
    return session_user
