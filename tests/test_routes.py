"""
Route CRUD: validation, the single-default invariant, and rollback behaviour.
"""

import pytest

from app.db.database import get_db

from .conftest import ORIGIN, add_route_direct, create_user


def rows():
    with get_db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM routes ORDER BY id").fetchall()]


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"hostname": "a.example.com", "backend": ""}, "empty backend"),
        ({"hostname": "a.example.com", "backend": "not a backend!!"}, "invalid characters"),
        ({"hostname": "a.example.com", "backend": "1.2.3.4:99999"}, "port out of range"),
        ({"hostname": "a.example.com", "backend": "1.2.3.4:0"}, "port zero"),
        ({"hostname": "", "backend": "1.2.3.4:25565"}, "empty hostname"),
        ({"hostname": "no-dots-here", "backend": "1.2.3.4:25565"}, "unresolvable hostname"),
        ({"hostname": "bad_host!.example.com", "backend": "1.2.3.4:25565"}, "invalid fqdn"),
    ],
)
def test_add_route_rejects_invalid_input(admin_client, router_stub, payload, reason):
    response = admin_client.post("/routes/add", data=payload, headers=ORIGIN)
    assert response.status_code == 400, f"{reason} was accepted: {response.text}"
    assert not router_stub.mutations(), (
        f"invalid route reached mc-router ({reason}): {router_stub.mutations()}"
    )


def test_add_route_succeeds_and_normalizes_the_backend(admin_client, router_stub):
    response = admin_client.post(
        "/routes/add",
        data={"hostname": "play.example.com", "backend": "10.0.0.5"},
        headers=ORIGIN,
    )
    assert response.status_code == 200
    stored = rows()[0]
    assert stored["hostname"] == "play.example.com"
    assert stored["backend"] == "10.0.0.5:25565", "bare host did not get the default port"

    pushed = [c for c in router_stub.mutations() if c["path"] == "/routes"]
    assert pushed, "route was not pushed to mc-router"
    assert pushed[0]["kwargs"]["json"] == {
        "serverAddress": "play.example.com",
        "backend": "10.0.0.5:25565",
    }


def test_subdomain_only_hostname_is_accepted_when_cloudflare_is_configured(
    admin_client, router_stub, monkeypatch
):
    """Regression: resolving a bare subdomain awaited a synchronous helper, so
    every such request failed with a TypeError instead of resolving the name."""
    from app.services import cloudflare

    monkeypatch.setattr(
        cloudflare, "get_cf_config", lambda: ("token", "zone-id", "example.com")
    )

    async def fake_resolve(hostname):
        return f"{hostname}.example.com"

    monkeypatch.setattr(cloudflare, "resolve_hostname", fake_resolve)

    response = admin_client.post(
        "/routes/add",
        data={"hostname": "play", "backend": "10.0.0.1:25565"},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text
    assert rows()[0]["hostname"] == "play.example.com"


def test_subdomain_only_hostname_without_cloudflare_is_a_400(admin_client, router_stub):
    response = admin_client.post(
        "/routes/add",
        data={"hostname": "play", "backend": "10.0.0.1:25565"},
        headers=ORIGIN,
    )
    assert response.status_code == 400
    assert "Cloudflare" in response.json()["error"]


def test_duplicate_hostname_is_rejected(admin_client, router_stub):
    add_route_direct("dup.example.com")
    response = admin_client.post(
        "/routes/add",
        data={"hostname": "dup.example.com", "backend": "10.0.0.1:25565"},
        headers=ORIGIN,
    )
    assert response.status_code == 409
    assert not router_stub.mutations()


def test_only_one_default_route_can_exist(admin_client, router_stub):
    """The fallback is a single row: a second one replaces it, never duplicates it."""
    first = admin_client.post(
        "/routes/add",
        data={"hostname": "", "backend": "10.0.0.1:25565", "is_default": "on"},
        headers=ORIGIN,
    )
    assert first.status_code == 200
    assert len([r for r in rows() if r["is_default"]]) == 1

    second = admin_client.post(
        "/routes/add",
        data={"hostname": "", "backend": "10.0.0.2:25565", "is_default": "on"},
        headers=ORIGIN,
    )
    # Both use the "__default__" sentinel, so the unique hostname constraint
    # rejects the duplicate rather than creating a second fallback.
    assert second.status_code == 409

    defaults = [r for r in rows() if r["is_default"]]
    assert len(defaults) == 1, f"expected exactly one default, found {len(defaults)}"
    assert defaults[0]["backend"] == "10.0.0.1:25565"


def test_editing_a_route_to_become_the_default_demotes_the_previous_one(
    admin_client, router_stub
):
    admin_client.post(
        "/routes/add",
        data={"hostname": "", "backend": "10.0.0.1:25565", "is_default": "on"},
        headers=ORIGIN,
    )
    route_id = admin_client.post(
        "/routes/add",
        data={"hostname": "second.example.com", "backend": "10.0.0.2:25565"},
        headers=ORIGIN,
    )
    assert route_id.status_code == 200
    new_id = next(r["id"] for r in rows() if r["hostname"] == "second.example.com")

    response = admin_client.post(
        f"/routes/edit/{new_id}",
        data={"hostname": "second.example.com", "backend": "10.0.0.2:25565", "is_default": "on"},
        headers=ORIGIN,
    )
    assert response.status_code == 200

    defaults = [r for r in rows() if r["is_default"]]
    assert len(defaults) == 1, f"expected exactly one default, found {len(defaults)}"
    assert defaults[0]["id"] == new_id


def test_default_route_backend_is_validated(admin_client, router_stub):
    response = admin_client.post(
        "/routes/add",
        data={"hostname": "", "backend": "not valid!!", "is_default": "on"},
        headers=ORIGIN,
    )
    assert response.status_code == 400
    assert not router_stub.called("/defaultRoute")


def test_edit_route_requires_a_hostname(admin_client, router_stub):
    route_id = add_route_direct("edit.example.com")

    response = admin_client.post(
        f"/routes/edit/{route_id}",
        data={"hostname": "", "backend": "10.0.0.1:25565"},
        headers=ORIGIN,
    )
    assert response.status_code == 400
    assert rows()[0]["hostname"] == "edit.example.com", "hostname was blanked"


def test_edit_route_updates_and_repushes(admin_client, router_stub):
    route_id = add_route_direct("old.example.com", backend="10.0.0.1:25565")

    response = admin_client.post(
        f"/routes/edit/{route_id}",
        data={"hostname": "new.example.com", "backend": "10.0.0.2:25566"},
        headers=ORIGIN,
    )
    assert response.status_code == 200
    stored = rows()[0]
    assert stored["hostname"] == "new.example.com"
    assert stored["backend"] == "10.0.0.2:25566"

    pushes = [c for c in router_stub.mutations() if c["path"] == "/routes"]
    assert pushes and pushes[-1]["kwargs"]["json"] == {
        "serverAddress": "new.example.com",
        "backend": "10.0.0.2:25566",
    }
    # The old hostname must stop routing.
    deletes = [c for c in router_stub.mutations() if c["method"].lower() == "delete"]
    assert any("old.example.com" in c["path"] for c in deletes)


def test_edit_route_rejects_a_hostname_collision(admin_client):
    add_route_direct("taken.example.com")
    route_id = add_route_direct("free.example.com")

    response = admin_client.post(
        f"/routes/edit/{route_id}",
        data={"hostname": "taken.example.com", "backend": "10.0.0.1:25565"},
        headers=ORIGIN,
    )
    assert response.status_code == 409


def test_delete_route_cleans_up_router_and_database(admin_client, router_stub):
    route_id = add_route_direct("gone.example.com")

    response = admin_client.post(f"/routes/delete/{route_id}", headers=ORIGIN)
    assert response.status_code == 200
    assert rows() == []
    assert router_stub.mutated("/routes/gone.example.com", "delete")


def test_deleting_the_default_route_clears_the_router_fallback(admin_client, router_stub):
    route_id = add_route_direct("__default__", backend="10.0.0.1:25565", is_default=1)

    response = admin_client.post(f"/routes/delete/{route_id}", headers=ORIGIN)
    assert response.status_code == 200
    assert router_stub.called("/defaultRoute"), "fallback was left active on mc-router"
    default_calls = [c for c in router_stub.mutations() if c["path"] == "/defaultRoute"]
    assert default_calls[-1]["kwargs"]["json"]["backend"] == ""


def test_router_failure_prevents_persisting_the_route(admin_client, router_stub):
    router_stub.online = False
    response = admin_client.post(
        "/routes/add",
        data={"hostname": "fail.example.com", "backend": "10.0.0.1:25565"},
        headers=ORIGIN,
    )
    assert response.status_code == 500
    assert rows() == [], "route was stored despite the router rejecting it"


def test_docker_managed_hostnames_cannot_be_edited(admin_client, monkeypatch, no_docker):
    route_id = add_route_direct("docker.example.com")

    async def fake_discovery(force=False):
        return [
            {
                "hostname": "docker.example.com",
                "backend": "172.17.0.2:25565",
                "source": "docker",
                "running": True,
                "container_name": "mc",
            }
        ]

    monkeypatch.setattr(no_docker, "discover_docker_routes", fake_discovery)

    response = admin_client.post(
        f"/routes/edit/{route_id}",
        data={"hostname": "elsewhere.example.com", "backend": "10.0.0.1:25565"},
        headers=ORIGIN,
    )
    assert response.status_code == 409


def test_docker_sourced_routes_cannot_be_deleted(admin_client):
    with get_db() as con:
        route_id = con.execute(
            "INSERT INTO routes (hostname, backend, source) VALUES (?,?, 'docker')",
            ("docker-label.example.com", "172.17.0.2:25565"),
        ).lastrowid
        con.commit()

    response = admin_client.post(f"/routes/delete/{route_id}", headers=ORIGIN)
    assert response.status_code == 403


def test_malformed_json_body_returns_400(admin_client):
    response = admin_client.post(
        "/routes/add",
        json=["not", "an", "object"],
        headers={**ORIGIN, "Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_route_creation_broadcasts_a_change_event(admin_client, monkeypatch):
    from app.services import sse

    events = []

    async def capture(event, data):
        events.append((event, data))

    monkeypatch.setattr(sse, "broadcast", capture)
    import app.routes.routes as routes_module

    monkeypatch.setattr(routes_module, "broadcast", capture)

    admin_client.post(
        "/routes/add",
        data={"hostname": "event.example.com", "backend": "10.0.0.1:25565"},
        headers=ORIGIN,
    )
    assert any(e[0] == "route-change" and e[1]["action"] == "add" for e in events)


def test_ownership_is_recorded_on_creation(admin_client):
    user_id = create_user("route-maker", perms=["create_route", "see_own_routes"])
    admin_client.post("/logout", headers=ORIGIN, follow_redirects=False)

    from .conftest import login

    login(admin_client, "route-maker", "averylongpassword123")
    response = admin_client.post(
        "/routes/add",
        data={"hostname": "mine.example.com", "backend": "10.0.0.1:25565"},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text
    stored = rows()
    assert len(stored) == 1
    assert stored[0]["owner_id"] == user_id
