"""
Shared utilities for route handlers.
"""

from typing import Optional

from fastapi import Request


async def get_form_or_json(request: Request) -> dict:
    """Extract form data from either JSON body or form-encoded POST."""
    content_type = request.headers.get("content-type", "")
    if "json" in content_type:
        try:
            return await request.json()
        except Exception:
            return {}
    try:
        form = await request.form()
        return {k: v for k, v in form.items()}
    except Exception:
        return {}


def set_flash(request: Request, type_: str, message: str) -> None:
    """Store a one-shot flash message in the session (survives the redirect)."""
    request.session["flash"] = {"type": type_, "message": message}


def get_flash(request: Request) -> Optional[dict]:
    """Pop and return the pending flash message, if any."""
    return request.session.pop("flash", None)
