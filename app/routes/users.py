"""
User and permission management routes.
"""

import logging

from fastapi import APIRouter, Form, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse

from app.core.security import current_user, hash_password
from app.db.database import get_db
from app.db.schema import user_has_perm, get_user_perms, grant_default_permissions
from app.core.config import ALL_PERMISSIONS

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/users")
async def api_get_users(request: Request):
    user = current_user(request)
    if not user or not user_has_perm(user, "see_all_users"):
        raise HTTPException(status_code=403, detail="Forbidden")

    with get_db() as con:
        rows = con.execute("SELECT id, username, role, created_at FROM users ORDER BY username").fetchall()
        return [dict(r) for r in rows]


@router.post("/users/add")
async def add_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
):
    user = current_user(request)
    if not user or not user_has_perm(user, "manage_users"):
        return RedirectResponse(url="/?error=Permission denied", status_code=303)

    if role not in ("admin", "user"):
        return RedirectResponse(url="/?error=Invalid role", status_code=303)

    hashed = hash_password(password)
    try:
        with get_db() as con:
            cur = con.cursor()
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (username.strip(), hashed, role)
            )
            new_id = cur.lastrowid
            if role == "user":
                grant_default_permissions(new_id, con)
            con.commit()
        return RedirectResponse(url="/?success=User created successfully", status_code=303)
    except Exception:
        return RedirectResponse(url="/?error=Username already exists", status_code=303)


@router.post("/users/edit/{user_id}")
async def edit_user(
    request: Request,
    user_id: int,
    username: str = Form(...),
    password: str = Form(""),
    role: str = Form(...),
):
    user = current_user(request)
    if not user or not user_has_perm(user, "manage_users"):
        return RedirectResponse(url="/?error=Permission denied", status_code=303)

    if role not in ("admin", "user"):
        return RedirectResponse(url="/?error=Invalid role", status_code=303)

    # Protect against removing the last admin
    if role == "user":
        with get_db() as con:
            admin_count = con.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
            curr_role = con.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()["role"]
            if curr_role == "admin" and admin_count <= 1:
                return RedirectResponse(url="/?error=Cannot demote the last admin", status_code=303)

    with get_db() as con:
        try:
            if password:
                hashed = hash_password(password)
                con.execute(
                    "UPDATE users SET username=?, password_hash=?, role=? WHERE id=?",
                    (username.strip(), hashed, role, user_id)
                )
            else:
                con.execute(
                    "UPDATE users SET username=?, role=? WHERE id=?",
                    (username.strip(), role, user_id)
                )
            con.commit()
        except Exception:
            return RedirectResponse(url="/?error=Username already exists", status_code=303)

    return RedirectResponse(url="/?success=User updated successfully", status_code=303)


@router.post("/users/delete/{user_id}")
async def delete_user(request: Request, user_id: int):
    user = current_user(request)
    if not user or not user_has_perm(user, "manage_users"):
        return RedirectResponse(url="/?error=Permission denied", status_code=303)

    if user_id == user["id"]:
        return RedirectResponse(url="/?error=You cannot delete yourself", status_code=303)

    with get_db() as con:
        r = con.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
        if not r:
            return RedirectResponse(url="/?error=User not found", status_code=303)
        if r["role"] == "admin":
            admin_count = con.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
            if admin_count <= 1:
                return RedirectResponse(url="/?error=Cannot delete the last admin", status_code=303)

        con.execute("UPDATE routes SET owner_id=NULL WHERE owner_id=?", (user_id,))
        con.execute("DELETE FROM users WHERE id=?", (user_id,))
        con.commit()

    return RedirectResponse(url="/?success=User deleted", status_code=303)


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
