"""
Authentication, CSRF, security headers and session behaviour.
"""

from .conftest import ADMIN_PASSWORD, ORIGIN, get_no_redirect, login, post_no_redirect


def test_dashboard_requires_authentication(client):
    response = get_no_redirect(client, "/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "Sign In" in response.text


def test_login_rejects_wrong_password(client):
    response = login(client, "admin", "wrong-password")
    assert response.status_code == 401
    assert "Invalid username or password" in response.text


def test_login_rejects_unknown_user(client):
    response = login(client, "nobody", ADMIN_PASSWORD)
    assert response.status_code == 401


def test_login_succeeds_and_preserves_username_on_failure(client):
    assert login(client, "admin", ADMIN_PASSWORD).status_code == 200

    client.post("/logout", headers=ORIGIN)
    response = login(client, "admin", "nope")
    assert 'value="admin"' in response.text, "username was not preserved"


def test_logout_clears_session(admin_client):
    assert get_no_redirect(admin_client, "/").status_code == 200
    post_no_redirect(admin_client, "/logout", headers=ORIGIN)
    assert get_no_redirect(admin_client, "/").status_code == 303


def test_unsafe_methods_require_same_origin(admin_client):
    response = admin_client.post("/routes/add", data={"hostname": "a.example.com"})
    assert response.status_code == 403
    assert response.json()["error"] == "csrf_failed"

    response = admin_client.post(
        "/routes/add",
        data={"hostname": "a.example.com"},
        headers={"Origin": "http://evil.example"},
    )
    assert response.status_code == 403


def test_referer_is_accepted_when_origin_missing(admin_client):
    response = admin_client.post(
        "/routes/add",
        data={"hostname": "a.example.com", "backend": "10.0.0.1:25565"},
        headers={"Referer": "http://testserver/"},
    )
    assert response.status_code != 403


def test_safe_methods_are_exempt_from_csrf(admin_client):
    assert admin_client.get("/api/routes").status_code == 200


def test_health_probes_bypass_csrf(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code in (200, 503)


def test_unauthenticated_api_calls_return_401(client):
    for path in (
        "/api/cf/records",
        "/api/cf/zones",
        "/api/users",
        "/api/crafty/servers",
        "/api/ports/used",
        "/api/routes",
        "/api/events",
        "/api/connections",
        "/api/router-status",
    ):
        response = client.get(path)
        assert response.status_code == 401, f"{path} returned {response.status_code}"


def test_security_headers_present(client):
    headers = client.get("/login").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert headers["cross-origin-opener-policy"] == "same-origin"
    assert "x-xss-protection" not in headers


def test_content_security_policy_is_strict(client):
    csp = client.get("/login").headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self' 'nonce-" in csp
    assert "style-src 'self' 'nonce-" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp


def test_csp_nonce_is_unique_per_response(client):
    import re

    def nonce(response):
        return re.search(r"'nonce-([^']+)'", response.headers["content-security-policy"]).group(1)

    first = nonce(client.get("/login"))
    second = nonce(client.get("/login"))
    assert first != second


def test_inline_scripts_carry_the_response_nonce(client):
    import re

    response = client.get("/login")
    nonce = re.search(
        r"'nonce-([^']+)'", response.headers["content-security-policy"]
    ).group(1)
    for attrs in re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>", response.text):
        assert f'nonce="{nonce}"' in attrs, f"inline script without nonce: {attrs}"


def test_hsts_only_when_https_only_enabled(client, monkeypatch):
    from app.core import config

    assert "strict-transport-security" not in client.get("/login").headers
    monkeypatch.setattr(config, "SESSION_HTTPS_ONLY", True)
    assert "strict-transport-security" in client.get("/login").headers


def test_login_is_rate_limited(client):
    for _ in range(5):
        login(client, "admin", "wrong-password")
    response = login(client, "admin", "wrong-password")
    assert response.status_code == 429
    assert "Too many attempts" in response.text


def decode_session_cookie(response):
    """Read the session payload without verifying the signature."""
    import base64
    import json

    raw = response.headers["set-cookie"].split("mc_router_ui_session=", 1)[1]
    raw = raw.split(";", 1)[0]
    payload = raw.rsplit(".", 1)[0]  # strip the itsdangerous signature
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def test_login_discards_pre_existing_session_state(client):
    """Session fixation defence: nothing set before login survives it."""
    response = login(client, "admin", ADMIN_PASSWORD)
    payload = decode_session_cookie(response)
    assert set(payload) == {"user"}, f"unexpected session keys: {set(payload)}"
    assert payload["user"]["username"] == "admin"


def test_tampered_session_cookie_is_rejected(client):
    """The cookie is signed, so editing its payload must not authenticate."""
    import base64
    import json

    login(client, "admin", ADMIN_PASSWORD)
    value = client.cookies["mc_router_ui_session"]
    signature = value.rsplit(".", 1)[1]
    forged_payload = base64.urlsafe_b64encode(
        json.dumps({"user": {"id": 1, "username": "admin", "role": "admin"}}).encode()
    ).decode().rstrip("=")

    client.cookies.set("mc_router_ui_session", f"{forged_payload}.{signature}")
    assert get_no_redirect(client, "/").status_code == 303, (
        "a forged session cookie was accepted"
    )


def test_session_cookie_is_httponly(client):
    response = login(client, "admin", ADMIN_PASSWORD)
    header = response.headers.get("set-cookie", "")
    assert "mc_router_ui_session=" in header
    assert "httponly" in header.lower()
    assert "samesite=lax" in header.lower()


def test_deleted_user_session_is_rejected(admin_client):
    """A session must stop working once the account behind it is gone."""
    from fastapi.testclient import TestClient

    from app.main import app

    from .conftest import create_user

    create_user("temporary", password="averylongpassword123")

    with TestClient(app) as other:
        assert login(other, "temporary", "averylongpassword123").status_code == 200
        assert get_no_redirect(other, "/").status_code == 200

        users = {u["username"]: u["id"] for u in admin_client.get("/api/users").json()}
        admin_client.post(f"/users/delete/{users['temporary']}", headers=ORIGIN)

        assert get_no_redirect(other, "/").status_code == 303, (
            "a session for a deleted account was still accepted"
        )
