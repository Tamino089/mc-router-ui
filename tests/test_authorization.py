"""
Authorisation: endpoint permission requirements and the default-route guard.
"""

import pytest

from app.core.config import DEFAULT_USER_PERMISSIONS
from app.db.database import get_db

from .conftest import ORIGIN, add_route_direct, create_user, login

USER_PASSWORD = "averylongpassword123"


@pytest.fixture
def default_user(client):
    """A signed-in account holding exactly the default permission set."""
    create_user("default-user", password=USER_PASSWORD, perms=DEFAULT_USER_PERMISSIONS)
    assert login(client, "default-user", USER_PASSWORD).status_code == 200
    return client


@pytest.mark.parametrize(
    "path",
    [
        "/api/users",
        "/api/permissions/1",
        "/api/crafty/servers",
        "/api/cf/records",
        "/api/cf/zones",
    ],
)
def test_endpoints_require_their_permission(default_user, path):
    assert default_user.get(path).status_code == 403


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/users/add", {"username": "x", "password": "averylongpassword123"}),
        ("/users/delete/1", {}),
        ("/api/permissions/1", {"permissions": []}),
    ],
)
def test_mutations_require_manage_permissions(default_user, path, payload):
    response = default_user.post(path, data=payload, headers=ORIGIN)
    assert response.status_code == 403


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/settings/cloudflare", {"cf_token": "x"}),
        ("/settings/crafty", {"crafty_url": "https://crafty:8443", "crafty_token": "y"}),
        ("/settings/wizard", {"cf_token": "x"}),
    ],
)
def test_form_endpoints_redirect_without_permission(default_user, path, payload):
    """These are HTML form handlers, so they flash an error and redirect."""
    from app.db.database import get_db

    response = default_user.post(path, data=payload, headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303
    with get_db() as con:
        row = con.execute(
            "SELECT value FROM settings WHERE key='cf_api_token'"
        ).fetchone()
    assert row is None, "a forbidden settings change was persisted"


def test_default_route_creation_is_forbidden_without_the_permission(default_user, router_stub):
    response = default_user.post(
        "/routes/add",
        data={"hostname": "", "backend": "127.0.0.1:25590", "is_default": "on"},
        headers=ORIGIN,
    )
    assert response.status_code == 403
    assert not router_stub.called("/defaultRoute"), "fallback route was pushed anyway"


def test_default_route_cannot_be_claimed_by_editing_an_owned_route(default_user, router_stub):
    with get_db() as con:
        user_id = con.execute(
            "SELECT id FROM users WHERE username='default-user'"
        ).fetchone()[0]
    route_id = add_route_direct("mine.example.com", owner_id=user_id)

    response = default_user.post(
        f"/routes/edit/{route_id}",
        data={"hostname": "mine.example.com", "backend": "10.0.0.1:25565", "is_default": "on"},
        headers=ORIGIN,
    )
    assert response.status_code == 403
    assert not router_stub.called("/defaultRoute")


def test_default_route_deletion_is_forbidden_without_the_permission(default_user):
    default_route_id = add_route_direct(
        "__default__", backend="10.0.0.1:25565", is_default=1
    )
    response = default_user.post(
        f"/routes/delete/{default_route_id}", headers=ORIGIN
    )
    assert response.status_code == 403


def test_granting_manage_default_route_allows_it(client, router_stub):
    create_user(
        "trusted",
        password=USER_PASSWORD,
        perms=[*DEFAULT_USER_PERMISSIONS, "manage_default_route"],
    )
    assert login(client, "trusted", USER_PASSWORD).status_code == 200

    response = client.post(
        "/routes/add",
        data={"hostname": "", "backend": "10.0.0.1:25595", "is_default": "on"},
        headers=ORIGIN,
    )
    assert response.status_code == 200
    assert router_stub.called("/defaultRoute")


def test_user_cannot_edit_another_users_route(client, router_stub):
    other_id = create_user("owner-x", password=USER_PASSWORD, perms=DEFAULT_USER_PERMISSIONS)
    create_user("intruder", password=USER_PASSWORD, perms=DEFAULT_USER_PERMISSIONS)
    route_id = add_route_direct("owned.example.com", owner_id=other_id)

    assert login(client, "intruder", USER_PASSWORD).status_code == 200
    response = client.post(
        f"/routes/edit/{route_id}",
        data={"hostname": "owned.example.com", "backend": "10.9.9.9:25565"},
        headers=ORIGIN,
    )
    assert response.status_code == 403


def test_user_cannot_delete_another_users_route(client):
    other_id = create_user("owner-y", password=USER_PASSWORD, perms=DEFAULT_USER_PERMISSIONS)
    create_user("intruder2", password=USER_PASSWORD, perms=DEFAULT_USER_PERMISSIONS)
    route_id = add_route_direct("owned2.example.com", owner_id=other_id)

    assert login(client, "intruder2", USER_PASSWORD).status_code == 200
    assert client.post(f"/routes/delete/{route_id}", headers=ORIGIN).status_code == 403


def test_route_visibility_is_scoped_to_ownership(client):
    mine = create_user("mine", password=USER_PASSWORD, perms=["see_own_routes"])
    theirs = create_user("theirs", password=USER_PASSWORD, perms=["see_own_routes"])
    add_route_direct("mine.example.com", owner_id=mine)
    add_route_direct("theirs.example.com", owner_id=theirs)

    assert login(client, "mine", USER_PASSWORD).status_code == 200
    hostnames = {r["hostname"] for r in client.get("/api/routes").json()["routes"]}
    assert hostnames == {"mine.example.com"}


def test_health_endpoints_respect_route_visibility(client):
    mine = create_user("health-mine", password=USER_PASSWORD, perms=["see_own_routes"])
    theirs = create_user("health-theirs", password=USER_PASSWORD, perms=["see_own_routes"])
    mine_id = add_route_direct("h1.example.com", owner_id=mine)
    theirs_id = add_route_direct("h2.example.com", owner_id=theirs)

    assert login(client, "health-mine", USER_PASSWORD).status_code == 200
    assert client.get(f"/api/health/{mine_id}").status_code == 200
    assert client.get(f"/api/health/{theirs_id}").status_code == 403
    assert client.get(f"/api/health/{theirs_id}/history").status_code == 403


def test_connection_map_is_filtered_for_restricted_users(client, router_stub):
    mine = create_user("conn-mine", password=USER_PASSWORD, perms=["see_own_routes"])
    add_route_direct("visible.example.com", owner_id=mine)
    add_route_direct("hidden.example.com")
    router_stub.connections = {"visible.example.com": 2, "hidden.example.com": 7}

    assert login(client, "conn-mine", USER_PASSWORD).status_code == 200
    body = client.get("/api/connections").json()
    assert body == {"visible.example.com": 2}


def test_fallback_toggle_is_hidden_without_the_permission(default_user):
    """The control must not be offered to a user the server would refuse."""
    html = default_user.get("/").text
    assert 'id="f-is-default"' not in html


def test_fallback_toggle_is_shown_with_the_permission(admin_client):
    html = admin_client.get("/").text
    assert 'id="f-is-default"' in html


def test_admin_sees_every_route(admin_client):
    add_route_direct("a.example.com")
    add_route_direct("b.example.com")
    hostnames = {r["hostname"] for r in admin_client.get("/api/routes").json()["routes"]}
    assert hostnames == {"a.example.com", "b.example.com"}


def test_admin_connection_map_is_unfiltered(admin_client, router_stub):
    router_stub.connections = {"a.example.com": 1, "b.example.com": 2}
    assert admin_client.get("/api/connections").json() == router_stub.connections


def test_last_admin_cannot_be_demoted_or_deleted(admin_client):
    users = {u["username"]: u for u in admin_client.get("/api/users").json()}
    admin_id = users["admin"]["id"]

    response = admin_client.post(
        f"/users/edit/{admin_id}",
        data={"username": "admin", "role": "user"},
        headers=ORIGIN,
    )
    assert response.status_code == 400
    assert admin_client.post(f"/users/delete/{admin_id}", headers=ORIGIN).status_code == 400
