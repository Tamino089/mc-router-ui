"""
Validation helpers, health probes and the Crafty/Cloudflare unconfigured paths.
"""

import asyncio

import pytest

from app.core.validation import (
    is_valid_backend,
    normalize_backend,
    normalize_base_url,
    parse_backend,
    valid_ip_port,
)
from app.services.health import tcp_check


@pytest.mark.parametrize(
    "value,expected",
    [
        ("play.example.com:25565", True),
        ("1.2.3.4", True),
        ("10.0.0.5:25565", True),
        # A bare token is a valid hostname; the port is defaulted later.
        ("25565", True),
        ("host:", False),
        ("", False),
        ("not a host!!", False),
        ("host:port", False),
    ],
)
def test_backend_validation(value, expected):
    assert is_valid_backend(value) is expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1.2.3.4:25565", True),
        ("255.255.255.255:1", True),
        ("1.2.3.4:0", False),
        ("1.2.3.4:65536", False),
        ("999.1.1.1:25565", False),
        ("1.2.3:25565", False),
    ],
)
def test_valid_ip_port(value, expected):
    assert valid_ip_port(value) is expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("play", "play:25565"),
        ("play.example.com", "play.example.com:25565"),
        ("1.2.3.4:25566", "1.2.3.4:25566"),
        ("", ""),
    ],
)
def test_normalize_backend(value, expected):
    assert normalize_backend(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("play.example.com:25566", ("play.example.com", 25566)),
        ("play.example.com", ("play.example.com", 25565)),
        ("1.2.3.4", ("1.2.3.4", 25565)),
    ],
)
def test_parse_backend(value, expected):
    assert parse_backend(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://crafty:8443", "https://crafty:8443"),
        ("http://192.168.1.5:8080/", "http://192.168.1.5:8080"),
        ("ftp://crafty", None),
        ("file:///etc/passwd", None),
        ("https://user:pass@crafty", None),
        ("crafty:8443", None),
        ("", None),
    ],
)
def test_normalize_base_url(value, expected):
    assert normalize_base_url(value) == expected


def test_healthz_is_always_ok(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_reports_degraded_when_the_router_is_unreachable(client, router_stub):
    router_stub.online = False
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "checks": {"database": "ok", "mc_router": "error"},
    }


def test_readyz_does_not_leak_internal_detail(client, router_stub):
    router_stub.online = False
    body = client.get("/readyz").text
    for leak in ("retries", "http://", "connection lost", "Traceback"):
        assert leak not in body, f"readiness probe leaked {leak!r}"


def test_readyz_is_ok_when_the_router_answers(client, router_stub):
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "mc_router": "ok"},
    }


def test_tcp_check_reports_a_refused_connection():
    healthy, latency, error = tcp_check("127.0.0.1", 1, timeout=0.5)
    assert healthy is False
    assert latency == -1
    assert error


def test_tcp_check_reports_dns_failure():
    healthy, _, error = tcp_check("host.invalid.example.", 25565, timeout=0.5)
    assert healthy is False
    assert "DNS" in error or "unreachable" in error


def test_router_status_endpoint_reflects_reachability(admin_client, router_stub):
    assert admin_client.get("/api/router-status").json()["online"] is True
    router_stub.online = False
    body = admin_client.get("/api/router-status").json()
    assert body["online"] is False
    assert body["error"]


def test_crafty_servers_reports_unconfigured_rather_than_failing(admin_client):
    body = admin_client.get("/api/crafty/servers").json()
    assert body["success"] is True
    assert body["configured"] is False
    assert body["servers"] == []


def test_saving_an_invalid_crafty_url_is_rejected(admin_client):
    response = admin_client.post(
        "/settings/crafty",
        data={"crafty_url": "file:///etc/passwd", "crafty_token": "abc"},
        headers={"Origin": "http://testserver", "Referer": "http://testserver/"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    from app.db.database import get_db

    with get_db() as con:
        row = con.execute(
            "SELECT value FROM settings WHERE key='crafty_url'"
        ).fetchone()
    assert row is None or row[0] == "", "an invalid Crafty URL was stored"


def test_valid_crafty_url_is_normalized(admin_client):
    admin_client.post(
        "/settings/crafty",
        data={"crafty_url": "https://crafty:8443/", "crafty_token": "abc"},
        headers={"Origin": "http://testserver", "Referer": "http://testserver/"},
    )
    from app.db.database import get_db

    with get_db() as con:
        row = con.execute(
            "SELECT value FROM settings WHERE key='crafty_url'"
        ).fetchone()
    assert row[0] == "https://crafty:8443"


def test_blank_token_leaves_the_stored_value_untouched(admin_client):
    from app.db.database import get_db

    headers = {"Origin": "http://testserver", "Referer": "http://testserver/"}
    admin_client.post(
        "/settings/crafty",
        data={"crafty_url": "https://crafty:8443", "crafty_token": "secret-token"},
        headers=headers,
    )
    admin_client.post(
        "/settings/crafty",
        data={"crafty_url": "https://crafty:8443", "crafty_token": ""},
        headers=headers,
    )
    with get_db() as con:
        token = con.execute(
            "SELECT value FROM settings WHERE key='crafty_token'"
        ).fetchone()[0]
    assert token == "secret-token", "a blank field erased the stored token"


def test_empty_stored_zone_falls_back_to_the_environment(admin_client, monkeypatch):
    """A blank field saved in Settings must not shadow an env-configured value."""
    from app.core import config
    from app.db.database import get_db
    from app.services import cloudflare

    monkeypatch.setattr(config, "CF_ZONE_NAME", "env-zone.example.com")
    with get_db() as con:
        con.execute(
            "INSERT OR REPLACE INTO settings (key,value) VALUES ('cf_zone_name','')"
        )
        con.commit()

    _, _, zone_name = cloudflare.get_cf_config()
    assert zone_name == "env-zone.example.com"


def test_zone_cache_invalidation(monkeypatch):
    from app.core import config
    from app.services import cloudflare

    monkeypatch.setattr(config, "CF_ZONE_NAME", "")
    cloudflare._cf_zone_name_cache = "stale.example.com"
    cloudflare.invalidate_zone_cache()
    assert cloudflare._cf_zone_name_cache is None


def test_docker_discovery_is_empty_without_a_socket(no_docker):
    assert asyncio.run(no_docker.discover_docker_routes(force=True)) == []


def test_docker_managed_check_is_false_without_a_socket(no_docker):
    assert asyncio.run(no_docker.is_docker_managed("play.example.com")) is False


def test_docker_routes_are_not_returned_to_restricted_users(client, monkeypatch, no_docker):
    from .conftest import create_user, login

    create_user("plain", password="averylongpassword123", perms=["see_own_routes"])

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

    assert login(client, "plain", "averylongpassword123").status_code == 200
    assert client.get("/api/routes").json()["routes"] == []


def test_docker_routes_are_visible_to_admins(admin_client, monkeypatch, no_docker):
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

    routes = admin_client.get("/api/routes").json()["routes"]
    assert [r["hostname"] for r in routes] == ["docker.example.com"]
    assert routes[0]["id"] is None


def test_validate_route_reports_backend_failure(admin_client):
    body = admin_client.get(
        "/api/validate-route?hostname=play.example.com&backend=127.0.0.1:1"
    ).json()
    assert body["success"] is True
    assert body["val-backend"]["status"] == "error"


def test_validate_route_marks_the_default_route_as_not_needing_a_hostname(admin_client):
    body = admin_client.get("/api/validate-route?is_default=true").json()
    assert body["val-format"]["status"] == "success"
    assert body["val-dns"]["status"] == "neutral"


def test_validate_route_is_rate_limited(admin_client):
    last = None
    for _ in range(40):
        last = admin_client.get("/api/validate-route?hostname=play.example.com")
        if last.status_code == 429:
            break
    assert last.status_code == 429, "validation endpoint is not throttled"
