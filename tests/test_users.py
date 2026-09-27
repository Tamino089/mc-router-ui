"""
User management: role handling, password policy and permission grants.
"""

from app.core.config import MIN_PASSWORD_LENGTH
from app.db.database import get_db

from .conftest import ORIGIN, create_user, login, post_no_redirect

USER_PASSWORD = "averylongpassword123"


def users_by_name(client):
    return {u["username"]: u for u in client.get("/api/users").json()}


def test_short_password_is_rejected(admin_client):
    response = admin_client.post(
        "/users/add",
        data={"username": "shorty", "password": "x" * (MIN_PASSWORD_LENGTH - 1)},
        headers=ORIGIN,
    )
    assert response.status_code == 400
    assert "at least" in response.json()["error"]


def test_password_at_the_minimum_is_accepted(admin_client):
    response = admin_client.post(
        "/users/add",
        data={"username": "okuser", "password": "x" * MIN_PASSWORD_LENGTH, "role": "user"},
        headers=ORIGIN,
    )
    assert response.status_code == 200


def test_duplicate_username_is_rejected(admin_client):
    create_user("taken")
    response = admin_client.post(
        "/users/add",
        data={"username": "taken", "password": USER_PASSWORD},
        headers=ORIGIN,
    )
    assert response.status_code == 409


def test_invalid_role_is_rejected(admin_client):
    response = admin_client.post(
        "/users/add",
        data={"username": "weird", "password": USER_PASSWORD, "role": "superuser"},
        headers=ORIGIN,
    )
    assert response.status_code == 400


def test_username_with_whitespace_is_rejected(admin_client):
    response = admin_client.post(
        "/users/add",
        data={"username": "two words", "password": USER_PASSWORD},
        headers=ORIGIN,
    )
    assert response.status_code == 400


def test_editing_without_a_role_preserves_the_current_role(admin_client):
    user_id = create_user("promoted", role="admin")

    response = admin_client.post(
        f"/users/edit/{user_id}",
        data={"username": "renamed-admin"},
        headers=ORIGIN,
    )
    assert response.status_code == 200
    assert users_by_name(admin_client)["renamed-admin"]["role"] == "admin"


def test_editing_does_not_regrant_revoked_permissions(admin_client):
    create_user("revoked", perms=["see_own_routes | create_route"])
    user_id = create_user("revoked2", role="user")
    admin_client.post(
        f"/api/permissions/{user_id}",
        json={"permissions": ["see_own_routes"]},
        headers=ORIGIN,
    )
    assert admin_client.get(f"/api/permissions/{user_id}").json()["permissions"] == [
        "see_own_routes"
    ]

    admin_client.post(
        f"/users/edit/{user_id}", data={"username": "revoked2"}, headers=ORIGIN
    )
    assert admin_client.get(f"/api/permissions/{user_id}").json()["permissions"] == [
        "see_own_routes"
    ], "editing the user restored permissions an administrator had revoked"


def test_promoting_to_admin_clears_stored_permissions(admin_client):
    user_id = create_user("rising", perms=["see_own_routes"])
    admin_client.post(
        f"/users/edit/{user_id}",
        data={"username": "rising", "role": "admin"},
        headers=ORIGIN,
    )
    assert admin_client.get(f"/api/permissions/{user_id}").json()["permissions"] == []


def test_delete_user_releases_their_routes(admin_client):
    user_id = create_user("leaver", perms=["create_route"])
    with get_db() as con:
        route_id = con.execute(
            "INSERT INTO routes (hostname, backend, owner_id) VALUES (?,?,?)",
            ("leaver.example.com", "10.0.0.1:25565", user_id),
        ).lastrowid
        con.commit()

    assert admin_client.post(f"/users/delete/{user_id}", headers=ORIGIN).status_code == 200
    with get_db() as con:
        owner = con.execute(
            "SELECT owner_id FROM routes WHERE id=?", (route_id,)
        ).fetchone()[0]
    assert owner is None


def test_delete_user_removes_their_permissions(admin_client):
    user_id = create_user("perm-leaver", perms=["see_own_routes", "create_route"])
    admin_client.post(f"/users/delete/{user_id}", headers=ORIGIN)
    with get_db() as con:
        remaining = con.execute(
            "SELECT COUNT(*) FROM permissions WHERE user_id=?", (user_id,)
        ).fetchone()[0]
    assert remaining == 0


def test_cannot_delete_yourself(admin_client):
    admin_id = users_by_name(admin_client)["admin"]["id"]
    response = admin_client.post(f"/users/delete/{admin_id}", headers=ORIGIN)
    assert response.status_code == 400


def test_permission_list_rejects_unknown_entries(admin_client):
    user_id = create_user("clean-perms")
    response = admin_client.post(
        f"/api/permissions/{user_id}",
        json={"permissions": ["see_own_routes", "not_a_real_permission"]},
        headers=ORIGIN,
    )
    assert response.status_code == 200
    assert admin_client.get(f"/api/permissions/{user_id}").json()["permissions"] == [
        "see_own_routes"
    ]


def test_permission_update_requires_the_key(admin_client):
    """A body without the key must not be read as 'revoke everything'."""
    user_id = create_user("keeper", perms=["see_own_routes"])

    for body in ("just a string", {}, [], 42):
        response = admin_client.post(
            f"/api/permissions/{user_id}",
            json=body,
            headers={**ORIGIN, "Content-Type": "application/json"},
        )
        assert response.status_code == 400, f"body {body!r} returned {response.status_code}"

    assert admin_client.get(f"/api/permissions/{user_id}").json()["permissions"] == [
        "see_own_routes"
    ], "permissions were wiped by a malformed body"


def test_explicit_empty_list_revokes_all_permissions(admin_client):
    user_id = create_user("revoke-me", perms=["see_own_routes", "create_route"])
    response = admin_client.post(
        f"/api/permissions/{user_id}", json={"permissions": []}, headers=ORIGIN
    )
    assert response.status_code == 200
    assert admin_client.get(f"/api/permissions/{user_id}").json()["permissions"] == []


def test_permissions_for_missing_user_returns_404(admin_client):
    response = admin_client.post(
        "/api/permissions/99999", json={"permissions": []}, headers=ORIGIN
    )
    assert response.status_code == 404


def test_oversized_permission_list_is_rejected(admin_client):
    user_id = create_user("flood")
    response = admin_client.post(
        f"/api/permissions/{user_id}",
        json={"permissions": ["see_own_routes"] * 500},
        headers=ORIGIN,
    )
    assert response.status_code == 400


def test_user_can_change_their_own_password(admin_client):
    response = post_no_redirect(
        admin_client,
        "/settings/password",
        data={
            "current_password": "test-admin-password",
            "new_password": "a-brand-new-password",
            "confirm_password": "a-brand-new-password",
        },
        headers=ORIGIN,
    )
    assert response.status_code == 303

    admin_client.post("/logout", headers=ORIGIN)
    assert login(admin_client, "admin", "a-brand-new-password").status_code == 200


def test_password_change_requires_the_current_password(admin_client):
    response = post_no_redirect(
        admin_client,
        "/settings/password",
        data={
            "current_password": "wrong",
            "new_password": "a-brand-new-password",
            "confirm_password": "a-brand-new-password",
        },
        headers=ORIGIN,
    )
    assert response.status_code == 303
    admin_client.post("/logout", headers=ORIGIN)
    assert login(admin_client, "admin", "a-brand-new-password").status_code == 401


def test_password_change_enforces_the_minimum_length(admin_client):
    response = post_no_redirect(
        admin_client,
        "/settings/password",
        data={
            "current_password": "test-admin-password",
            "new_password": "short",
            "confirm_password": "short",
        },
        headers=ORIGIN,
    )
    assert response.status_code == 303
    admin_client.post("/logout", headers=ORIGIN)
    assert login(admin_client, "admin", "short").status_code == 401


def test_password_change_requires_matching_confirmation(admin_client):
    post_no_redirect(
        admin_client,
        "/settings/password",
        data={
            "current_password": "test-admin-password",
            "new_password": "a-brand-new-password",
            "confirm_password": "a-different-password",
        },
        headers=ORIGIN,
    )
    admin_client.post("/logout", headers=ORIGIN)
    assert login(admin_client, "admin", "a-brand-new-password").status_code == 401


def test_changing_the_password_clears_the_default_password_flag(admin_client):
    from app.db import schema

    with get_db() as con:
        con.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')",
            (schema.DEFAULT_PASSWORD_SETTING,),
        )
        con.commit()
    assert schema.is_default_admin_password() is True

    post_no_redirect(
        admin_client,
        "/settings/password",
        data={
            "current_password": "test-admin-password",
            "new_password": "a-brand-new-password",
            "confirm_password": "a-brand-new-password",
        },
        headers=ORIGIN,
    )
    assert schema.is_default_admin_password() is False
