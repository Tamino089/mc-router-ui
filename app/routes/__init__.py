from fastapi import Request


async def get_form_or_json(request: Request) -> dict:
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
    request.session["flash"] = {"type": type_, "message": message}

def get_flash(request: Request) -> dict | None:
    return request.session.pop("flash", None)
