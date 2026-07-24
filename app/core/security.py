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
    return request.session.get("user")
