"""
User and permission management routes.
"""

import logging
import sqlite3

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.security import current_user, hash_password
from app.db.database import get_db
from app.db.schema import user_has_perm, get_user_perms, grant_default_permissions
from app.core.config import ALL_PERMISSIONS
from app.routes import get_form_or_json

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/users")
async def api_get_users(request: Request):
    user = current_user(request)
    if not user or not user_has_perm(user, "see_all_users"):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    with get_db() as con:
        rows = con.execute("SELECT id, username, role, created_at FROM users ORDER BY username").fetchall()
        return [dict(r) for r in rows]


@router.post("/users/add")
async def add_user(request: Request):
    user = current_user(request)
    if not user or not user_has_perm(user, "manage_users"):
        return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)

    data = await get_form_or_json(request)
    username = data.get("username", "").strip()
    password = data.get("password", "")
    role = data.get("role", "user")

    if not username or not password:
        return JSONResponse({"success": False, "error": "Username and password are required"}, status_code=400)
    if role not in ("admin", "user"):
        return JSONResponse({"success": False, "error": "Invalid role"}, status_code=400)

    hashed = hash_password(password)
    try:
        with get_db() as con:
            cur = con.cursor()
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (username, hashed, role)
            )
            new_id = cur.lastrowid
            if role == "user":
                grant_default_permissions(new_id, con)
            con.commit()
        return JSONResponse({"success": True, "message": "User created successfully"})
    except sqlite3.IntegrityError:
        return JSONResponse({"success": False, "error": "Username already exists"}, status_code=409)


@router.post("/users/edit/{user_id}")
async def edit_user(request: Request, user_id: int):
    user = current_user(request)
    if not user or not user_has_perm(user, "manage_users"):
        return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)

    data = await get_form_or_json(request)
    username = data.get("username", "").strip()
    password = data.get("password", "")
    role = data.get("role", "user")

    if not username:
        return JSONResponse({"success": False, "error": "Username is required"}, status_code=400)
    if role not in ("admin", "user"):
        return JSONResponse({"success": False, "error": "Invalid role"}, status_code=400)

    with get_db() as con:
        try:
            existing = con.execute(
                "SELECT role FROM users WHERE id=?",
                (user_id,),
            ).fetchone()
            if not existing:
                return JSONResponse({"success": False, "error": "User not found"}, status_code=404)
            if role == "user" and existing["role"] == "admin":
                admin_count = con.execute(
                    "SELECT COUNT(*) FROM users WHERE role='admin'"
                ).fetchone()[0]
                if admin_count <= 1:
                    return JSONResponse(
                        {"success": False, "error": "Cannot demote the last admin"},
                        status_code=400,
                    )
            if password:
                hashed = hash_password(password)
                con.execute(
                    "UPDATE users SET username=?, password_hash=?, role=? WHERE id=?",
                    (username, hashed, role, user_id)
                )
            else:
                con.execute(
                    "UPDATE users SET username=?, role=? WHERE id=?",
                    (username, role, user_id)
                )
            if role == "user":
                grant_default_permissions(user_id, con)
            else:
                con.execute("DELETE FROM permissions WHERE user_id=?", (user_id,))
            con.commit()
        except sqlite3.IntegrityError:
            return JSONResponse({"success": False, "error": "Username already exists"}, status_code=409)

    return JSONResponse({"success": True, "message": "User updated successfully"})


@router.post("/users/delete/{user_id}")
async def delete_user(request: Request, user_id: int):
    user = current_user(request)
    if not user or not user_has_perm(user, "manage_users"):
        return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)

    if user_id == user["id"]:
        return JSONResponse({"success": False, "error": "You cannot delete yourself"}, status_code=400)

    with get_db() as con:
        r = con.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
        if not r:
            return JSONResponse({"success": False, "error": "User not found"}, status_code=404)
        if r["role"] == "admin":
            admin_count = con.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
            if admin_count <= 1:
                return JSONResponse({"success": False, "error": "Cannot delete the last admin"}, status_code=400)

        con.execute("UPDATE routes SET owner_id=NULL WHERE owner_id=?", (user_id,))
        con.execute("DELETE FROM users WHERE id=?", (user_id,))
        con.commit()

    return JSONResponse({"success": True, "message": "User deleted successfully"})


@router.get("/api/permissions/{user_id}")
async def api_get_permissions(request: Request, user_id: int):
    user = current_user(request)
    if not user or not user_has_perm(user, "manage_users"):
        return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)
    return {"success": True, "permissions": list(get_user_perms(user_id))}


@router.post("/api/permissions/{user_id}")
async def api_set_permissions(request: Request, user_id: int):
    user = current_user(request)
    if not user or not user_has_perm(user, "manage_users"):
        return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)

    data = await request.json()
    new_perms = data.get("permissions", [])

    with get_db() as con:
        con.execute("DELETE FROM permissions WHERE user_id=?", (user_id,))
        for p in new_perms:
            if p in ALL_PERMISSIONS:
                con.execute("INSERT INTO permissions (user_id, permission) VALUES (?, ?)", (user_id, p))
        con.commit()

    return {"success": True}