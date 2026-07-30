"""
Shared utilities for route handlers.
"""

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
