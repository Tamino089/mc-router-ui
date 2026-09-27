"""
Shared test fixtures.

Environment variables are set before the application is imported because
app.core.config reads them at import time.
"""

import os
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = tempfile.mkdtemp(prefix="mc-router-ui-tests-")

os.environ["DB_PATH"] = str(Path(_TMP_DIR) / "test.db")
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test-admin-password"
os.environ["LOG_LEVEL"] = "CRITICAL"
os.environ["MC_ROUTER_API"] = "http://mc-router.invalid:8080"
# No Cloudflare or Crafty configuration: both integrations are optional and the
# unconfigured paths are what most tests exercise.
os.environ.pop("CLOUDFLARE_API_TOKEN", None)
os.environ.pop("CRAFTY_URL", None)
os.environ.pop("DOCKER_SOCKET", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import docker_watcher, mc_router  # noqa: E402

ADMIN_PASSWORD = "test-admin-password"
# Shared non-secret value for test accounts.
DEFAULT_TEST_PASSWORD = "averylongpassword123"
ORIGIN = {"Origin": "http://testserver"}

_TABLES = (
    "health_history",
    "health_checks",
    "permissions",
    "routes",
    "users",
    "settings",
)


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("error", request=None, response=self)


class RouterStub:
    """Records mc-router API calls and can be switched offline per test."""

    def __init__(self):
        self.calls = []
        self.online = True
        self.connections = {}

    async def request(self, method, path, **kwargs):
        self.calls.append({"method": method, "path": path, "kwargs": kwargs})
        if not self.online:
            return None, "mc-router unreachable (http://mc-router.invalid:8080)"
        if path == "/connections":
            return FakeResponse(self.connections), None
        return FakeResponse({}), None

    def paths(self):
        return [c["path"] for c in self.calls]

    def called(self, path):
        return any(c["path"] == path for c in self.calls)

    def mutations(self):
        """Only the writes; the SSE and health loops issue background reads."""
        return [c for c in self.calls if c["method"].lower() != "get"]

    def mutated(self, path, method=None):
        return any(
            c["path"] == path and (method is None or c["method"].lower() == method)
            for c in self.mutations()
        )


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    """The throttles are in-memory and keyed by client address, so they would
    otherwise carry attempts across tests that share the test client."""
    from app.routes.auth import login_limiter
    from app.routes.cloudflare_api import validate_limiter
    from app.routes.settings import password_change_limiter

    for limiter in (login_limiter, validate_limiter, password_change_limiter):
        limiter._attempts.clear()
    yield


@pytest.fixture(autouse=True)
def clean_db():
    """Start every test from an empty database containing only the admin."""
    with get_db() as con:
        for table in _TABLES:
            con.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed table names
        con.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
            ("admin", hash_password(ADMIN_PASSWORD)),
        )
        con.commit()
    yield


@pytest.fixture(autouse=True)
def router_stub(monkeypatch):
    """Replace the mc-router client so tests never touch the network."""
    stub = RouterStub()
    monkeypatch.setattr(mc_router, "router_request", stub.request)
    return stub


@pytest.fixture(autouse=True)
def no_docker(monkeypatch):
    """Docker discovery is unavailable unless a test opts in."""
    monkeypatch.setattr(docker_watcher, "docker_enabled", lambda: False)
    docker_watcher._cache = []
    docker_watcher._cache_valid = False
    docker_watcher._cache_ts = 0.0
    return docker_watcher


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin_client(client):
    response = client.post(
        "/login",
        data={"username": "admin", "password": ADMIN_PASSWORD},
        headers=ORIGIN,
    )
    assert response.status_code == 200, "admin login failed"
    return client


def create_user(
    username,
    password=DEFAULT_TEST_PASSWORD,
    role="user",
    perms=None,
):
    """Insert a user directly and return its id."""
    with get_db() as con:
        cursor = con.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, hash_password(password), role),
        )
        user_id = cursor.lastrowid
        for permission in perms or []:
            con.execute(
                "INSERT INTO permissions (user_id, permission) VALUES (?, ?)",
                (user_id, permission),
            )
        con.commit()
    return user_id


def get_no_redirect(client, path):
    """TestClient follows redirects by default; most assertions here do not want that."""
    return client.get(path, follow_redirects=False)


def post_no_redirect(client, path, **kwargs):
    return client.post(path, follow_redirects=False, **kwargs)


def login(client, username, password):
    return client.post(
        "/login", data={"username": username, "password": password}, headers=ORIGIN
    )


def route_count():
    with get_db() as con:
        return con.execute("SELECT COUNT(*) FROM routes").fetchone()[0]


def add_route_direct(hostname, backend="10.0.0.1:25565", owner_id=None, is_default=0):
    with get_db() as con:
        cursor = con.execute(
            """INSERT INTO routes (hostname, backend, is_default, source, owner_id)
               VALUES (?, ?, ?, 'static', ?)""",
            (hostname, backend, is_default, owner_id),
        )
        con.commit()
        return cursor.lastrowid
