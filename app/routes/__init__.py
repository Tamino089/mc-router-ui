"""
Shared helpers for route handlers.
"""

from fastapi import Request


async def get_form_or_json(request: Request) -> dict:
    """Return the request body as a dict from JSON or form-encoded input.

    A JSON body that is not an object (list, string, number) yields an empty
    dict so callers can treat malformed input as a validation failure instead of
    raising AttributeError on .get().
    """
    content_type = request.headers.get("content-type", "")
    if "json" in content_type:
        try:
            body = await request.json()
        except Exception:
            return {}
        return body if isinstance(body, dict) else {}
    try:
        form = await request.form()
        return dict(form)
    except Exception:
        return {}


def set_flash(request: Request, type_: str, message: str) -> None:
    """Store a one-shot flash message in the session, surviving one redirect."""
    request.session["flash"] = {"type": type_, "message": message}


def get_flash(request: Request) -> dict | None:
    """Pop and return the pending flash message, if any."""
    return request.session.pop("flash", None)
