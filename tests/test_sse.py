"""
The SSE engine: the broadcast regression and per-subscriber filtering.
"""

import asyncio
import json

from .conftest import create_user


def drain(queue):
    frames = []
    while not queue.empty():
        frames.append(queue.get_nowait())
    return frames


def payload_of(frame):
    return json.loads(frame.split("data: ", 1)[1].strip())


def reset_sse():
    from app.services import sse

    sse._subscribers.clear()
    sse._last_connections.clear()
    sse._last_router_status.clear()
    return sse


def test_broadcast_does_not_raise_for_any_event_type():
    """Regression: the caches were assigned without a global declaration, so
    every connections and router-status broadcast raised UnboundLocalError and
    the emitter loop silently published nothing."""

    sse = reset_sse()

    async def run():
        sse.subscribe(None)
        for event, data in (
            ("connections", {"a.example.com": 1}),
            ("router-status", {"online": True}),
            ("route-change", {"action": "add"}),
        ):
            await sse.broadcast(event, data)

    asyncio.run(run())


def test_broadcast_populates_the_snapshot_cache():
    sse = reset_sse()

    async def run():
        queue = sse.subscribe(None)
        await sse.broadcast("connections", {"a.example.com": 3})
        await sse.broadcast("router-status", {"online": True})
        frames = drain(queue)
        events = {f.split("\n", 1)[0].removeprefix("event: ") for f in frames}
        assert events == {"connections", "router-status"}
        assert payload_of(frames[0]) == {"a.example.com": 3}

    asyncio.run(run())


def test_unchanged_payloads_are_not_resent():
    sse = reset_sse()

    async def run():
        queue = sse.subscribe(None)
        await sse.broadcast("connections", {"a.example.com": 1})
        await sse.broadcast("connections", {"a.example.com": 1})
        assert len(drain(queue)) == 1

    asyncio.run(run())


def test_route_change_is_always_delivered():
    sse = reset_sse()

    async def run():
        queue = sse.subscribe(None)
        for _ in range(3):
            await sse.broadcast("route-change", {"action": "add"})
        assert len(drain(queue)) == 3

    asyncio.run(run())


def test_restricted_subscriber_receives_only_permitted_hostnames():
    sse = reset_sse()

    async def run():
        unrestricted = sse.subscribe(None)
        restricted = sse.subscribe(frozenset({"mine.example.com"}))
        await sse.broadcast(
            "connections", {"mine.example.com": 2, "secret.example.com": 9}
        )

        assert payload_of(drain(unrestricted)[0]) == {
            "mine.example.com": 2,
            "secret.example.com": 9,
        }
        assert payload_of(drain(restricted)[0]) == {"mine.example.com": 2}

    asyncio.run(run())


def test_snapshot_is_filtered_per_subscriber():
    sse = reset_sse()

    async def run():
        await sse.broadcast(
            "connections", {"mine.example.com": 2, "secret.example.com": 9}
        )
        restricted = sse.snapshot(frozenset({"mine.example.com"}))
        assert len(restricted) == 1
        assert payload_of(restricted[0]) == {"mine.example.com": 2}

    asyncio.run(run())


def test_a_subscriber_with_no_visible_hostnames_gets_no_connection_frame():
    sse = reset_sse()

    async def run():
        await sse.broadcast("connections", {"secret.example.com": 9})
        assert sse.snapshot(frozenset()) == []

    asyncio.run(run())


def test_route_change_is_filtered_by_visibility():
    sse = reset_sse()

    async def run():
        unrestricted = sse.subscribe(None)
        restricted = sse.subscribe(frozenset({"mine.example.com"}))

        await sse.broadcast(
            "route-change", {"action": "add", "route_id": 1, "hostname": "secret.example.com"}
        )
        assert len(drain(restricted)) == 0, "a hidden hostname leaked in route-change"
        assert len(drain(unrestricted)) == 1

        await sse.broadcast(
            "route-change", {"action": "add", "route_id": 2, "hostname": "mine.example.com"}
        )
        assert len(drain(restricted)) == 1

    asyncio.run(run())


def test_route_change_without_a_hostname_reaches_everyone():
    """The Crafty port-change event is a refresh trigger, not route data."""
    sse = reset_sse()

    async def run():
        restricted = sse.subscribe(frozenset({"mine.example.com"}))
        await sse.broadcast("route-change", {"action": "edit", "reason": "crafty_port_change"})
        assert len(drain(restricted)) == 1

    asyncio.run(run())


def test_unsubscribe_is_idempotent():
    sse = reset_sse()
    queue = sse.subscribe(None)
    sse.unsubscribe(queue)
    sse.unsubscribe(queue)
    assert sse._subscribers == {}


def test_full_queue_drops_the_slow_subscriber():
    sse = reset_sse()

    async def run():
        queue = sse.subscribe(None)
        for i in range(200):
            await sse.broadcast("route-change", {"n": i})
        assert queue not in sse._subscribers, "slow subscriber was never dropped"

    asyncio.run(run())


# Note: the streaming endpoint itself is verified against a live uvicorn server
# rather than through TestClient, which does not surface chunks from an
# open-ended streaming response. These tests cover the subscription, filtering
# and snapshot logic that the endpoint delegates to.

def test_allowed_hostnames_for_an_admin_is_unrestricted(admin_client):
    from app.core.security import current_user
    from app.routes.events import _allowed_hostnames

    request = type("R", (), {"session": {"user": {"id": 1, "username": "admin", "role": "admin"}}})(
)
    user = current_user(request) or {"id": 1, "username": "admin", "role": "admin"}
    assert _allowed_hostnames(user) is None


def test_allowed_hostnames_is_scoped_for_own_routes_only(client):
    from app.db.database import get_db
    from app.routes.events import _allowed_hostnames

    user_id = create_user(
        "sse-scoped", password="averylongpassword123", perms=["see_own_routes"]
    )
    with get_db() as con:
        con.execute(
            "INSERT INTO routes (hostname, backend, owner_id) VALUES (?,?,?)",
            ("mine.example.com", "10.0.0.1:25565", user_id),
        )
        con.execute(
            "INSERT INTO routes (hostname, backend) VALUES (?,?)",
            ("other.example.com", "10.0.0.2:25565"),
        )
        con.commit()

    user = {"id": user_id, "username": "sse-scoped", "role": "user"}
    assert _allowed_hostnames(user) == frozenset({"mine.example.com"})


def test_allowed_hostnames_is_empty_without_route_permissions():
    from app.routes.events import _allowed_hostnames

    user_id = create_user("sse-nothing", password="averylongpassword123")
    assert _allowed_hostnames({"id": user_id, "role": "user"}) == frozenset()
