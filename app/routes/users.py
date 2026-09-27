"""
User and permission management routes.
"""

import asyncio
import logging
import sqlite3

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.config import ALL_PERMISSIONS, MIN_PASSWORD_LENGTH
from app.core.security import current_user, hash_password
from app.db.database import get_db
from app.db.schema import get_user_perms, grant_default_permissions, user_has_perm
from app.routes import get_form_or_json

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_USERNAME_LENGTH = 64
MAX_PASSWORD_LENGTH = 1024
MAX_PERMISSIONS_PER_REQUEST = len(ALL_PERMISSIONS)


def _error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"success": False, "error": message}, status_code=status_code)


def _validate_username(username: str) -> str | None:
    if not username:
        return "Username is required"
    if len(username) > MAX_USERNAME_LENGTH:
        return f"Username must be at most {MAX_USERNAME_LENGTH} characters"
    if any(c.isspace() for c in username) or any(ord(c) < 32 for c in username):
        return "Username must not contain whitespace or control characters"
    return None


def _validate_password(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Password must be at most {MAX_PASSWORD_LENGTH} characters"
    return None


@router.get("/api/users")
async def api_get_users(request: Request):
    user = current_user(request)
    if not user:
        return _error("Not authenticated", 401)
    if not user_has_perm(user, "see_all_users"):
        return _error("Forbidden", 403)

    with get_db() as con:
        rows = con.execute(
            "SELECT id, username, role, created_at FROM users ORDER BY username"
        ).fetchall()
        return [dict(r) for r in rows]


@router.post("/users/add")
async def add_user(request: Request):
    user = current_user(request)
    if not user or not user_has_perm(user, "manage_users"):
        return _error("Permission denied", 403)

    data = await get_form_or_json(request)
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    role = str(data.get("role", "user"))

    if err := _validate_username(username):
        return _error(err)
    if err := _validate_password(password):
        return _error(err)
    if role not in ("admin", "user"):
        return _error("Invalid role")

    hashed = await asyncio.to_thread(hash_password, password)
    try:
        with get_db() as con:
            cur = con.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (username, hashed, role),
            )
            if role == "user":
                grant_default_permissions(cur.lastrowid, con)
            con.commit()
    except sqlite3.IntegrityError:
        return _error("Username already exists", 409)

    return JSONResponse({"success": True, "message": "User created successfully"})


@router.post("/users/edit/{user_id}")
async def edit_user(request: Request, user_id: int):
    user = current_user(request)
    if not user or not user_has_perm(user, "manage_users"):
        return _error("Permission denied", 403)

    data = await get_form_or_json(request)
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))

    if err := _validate_username(username):
        return _error(err)
    if password:
        if err := _validate_password(password):
            return _error(err)
        hashed = await asyncio.to_thread(hash_password, password)
    else:
        hashed = None

    with get_db() as con:
        try:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute(
                "SELECT role FROM users WHERE id=?", (user_id,)
            ).fetchone()
            if not existing:
                return _error("User not found", 404)

            # An omitted role keeps the current one; defaulting to "user" would
            # silently demote an admin on an unrelated rename.
            role = str(data.get("role", existing["role"]))
            if role not in ("admin", "user"):
                return _error("Invalid role")

            role_changed = role != existing["role"]
            if role == "user" and existing["role"] == "admin":
                admin_count = con.execute(
                    "SELECT COUNT(*) FROM users WHERE role='admin'"
                ).fetchone()[0]
                if admin_count <= 1:
                    return _error("Cannot demote the last admin")

            if hashed is not None:
                con.execute(
                    "UPDATE users SET username=?, password_hash=?, role=? WHERE id=?",
                    (username, hashed, role, user_id),
                )
            else:
                con.execute(
                    "UPDATE users SET username=?, role=? WHERE id=?",
                    (username, role, user_id),
                )

            if role == "admin":
                # Admins bypass permission checks, so stored grants are unused.
                con.execute("DELETE FROM permissions WHERE user_id=?", (user_id,))
            elif role_changed:
                # Only a fresh promotion to "user" gets the defaults; re-granting
                # on every edit would undo an administrator's revocations.
                grant_default_permissions(user_id, con)
            con.commit()
        except sqlite3.IntegrityError:
            return _error("Username already exists", 409)

    return JSONResponse({"success": True, "message": "User updated successfully"})


@router.post("/users/delete/{user_id}")
async def delete_user(request: Request, user_id: int):
    user = current_user(request)
    if not user or not user_has_perm(user, "manage_users"):
        return _error("Permission denied", 403)

    if user_id == user["id"]:
        return _error("You cannot delete yourself")

    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            return _error("User not found", 404)
        if row["role"] == "admin":
            admin_count = con.execute(
                "SELECT COUNT(*) FROM users WHERE role='admin'"
            ).fetchone()[0]
            if admin_count <= 1:
                return _error("Cannot delete the last admin")

        # permissions cascade from this delete; owned routes are released.
        con.execute("UPDATE routes SET owner_id=NULL WHERE owner_id=?", (user_id,))
        con.execute("DELETE FROM users WHERE id=?", (user_id,))
        con.commit()

    return JSONResponse({"success": True, "message": "User deleted successfully"})


@router.get("/api/permissions/{user_id}")
async def api_get_permissions(request: Request, user_id: int):
    user = current_user(request)
    if not user or not user_has_perm(user, "manage_users"):
        return _error("Permission denied", 403)
    return {"success": True, "permissions": sorted(get_user_perms(user_id))}


@router.post("/api/permissions/{user_id}")
async def api_set_permissions(request: Request, user_id: int):
    user = current_user(request)
    if not user or not user_has_perm(user, "manage_users"):
        return _error("Permission denied", 403)

    data = await get_form_or_json(request)
    # An absent key must not be read as "revoke everything", which is what
    # defaulting to an empty list would do for a malformed body.
    if "permissions" not in data:
        return _error("permissions is required")
    new_perms = data["permissions"]
    if not isinstance(new_perms, list):
        return _error("permissions must be a list")
    if len(new_perms) > MAX_PERMISSIONS_PER_REQUEST:
        return _error("Too many permissions supplied")

    with get_db() as con:
        if not con.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone():
            return _error("User not found", 404)
        con.execute("DELETE FROM permissions WHERE user_id=?", (user_id,))
        for permission in new_perms:
            if permission in ALL_PERMISSIONS:
                con.execute(
                    "INSERT INTO permissions (user_id, permission) VALUES (?, ?)",
                    (user_id, permission),
                )
        con.commit()

    return {"success": True}
