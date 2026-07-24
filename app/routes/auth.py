"""
Authentication routes (Login/Logout).
"""

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.security import verify_password
from app.db.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if request.session.get("user"):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    with get_db() as con:
        user_row = con.execute(
            "SELECT * FROM users WHERE LOWER(username)=LOWER(?)", (username,)
        ).fetchone()

    if user_row and verify_password(password, user_row["password_hash"]):
        request.session["user"] = {
            "id": user_row["id"],
            "username": user_row["username"],
            "role": user_row["role"],
        }
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Invalid username or password."},
        status_code=401,
    )


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
