"""
Database integrity: schema initialisation and foreign-key enforcement.
"""

from app.core.security import hash_password
from app.db import schema
from app.db.database import get_db

from .conftest import create_user


def test_foreign_keys_enabled_on_request_connections():
    # SQLite pragmas are per connection, so this must be set by get_db() itself
    # rather than relying on init_db().
    with get_db() as con:
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_journal_mode_is_wal():
    with get_db() as con:
        assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_permissions_cascade_when_user_deleted():
    user_id = create_user("cascade", perms=["see_own_routes", "create_route"])
    with get_db() as con:
        assert con.execute(
            "SELECT COUNT(*) FROM permissions WHERE user_id=?", (user_id,)
        ).fetchone()[0] == 2
        con.execute("DELETE FROM users WHERE id=?", (user_id,))
        con.commit()
        assert con.execute(
            "SELECT COUNT(*) FROM permissions WHERE user_id=?", (user_id,)
        ).fetchone()[0] == 0


def test_health_rows_cascade_when_route_deleted():
    user_id = create_user("owner")
    with get_db() as con:
        route_id = con.execute(
            "INSERT INTO routes (hostname, backend, owner_id) VALUES (?,?,?)",
            ("cascade.example.com", "10.0.0.1:25565", user_id),
        ).lastrowid
        con.execute(
            "INSERT INTO health_checks (route_id, healthy) VALUES (?, 1)", (route_id,)
        )
        con.execute(
            "INSERT INTO health_history (route_id, healthy) VALUES (?, 1)", (route_id,)
        )
        con.commit()

        con.execute("DELETE FROM routes WHERE id=?", (route_id,))
        con.commit()

        for table in ("health_checks", "health_history"):
            remaining = con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE route_id=?",  # noqa: S608
                (route_id,),
            ).fetchone()[0]
            assert remaining == 0, f"{table} rows were not cascaded"


def test_deleting_user_releases_owned_routes():
    user_id = create_user("route-owner")
    with get_db() as con:
        route_id = con.execute(
            "INSERT INTO routes (hostname, backend, owner_id) VALUES (?,?,?)",
            ("owned.example.com", "10.0.0.1:25565", user_id),
        ).lastrowid
        con.commit()

        # The handler nulls the owner first; the schema also uses SET NULL.
        con.execute("UPDATE routes SET owner_id=NULL WHERE owner_id=?", (user_id,))
        con.execute("DELETE FROM users WHERE id=?", (user_id,))
        con.commit()

        owner = con.execute(
            "SELECT owner_id FROM routes WHERE id=?", (route_id,)
        ).fetchone()[0]
        assert owner is None


def test_permissions_are_unique_per_user():
    import sqlite3

    user_id = create_user("dup")
    with get_db() as con:
        con.execute(
            "INSERT INTO permissions (user_id, permission) VALUES (?, ?)",
            (user_id, "see_own_routes"),
        )
        con.commit()
        try:
            con.execute(
                "INSERT INTO permissions (user_id, permission) VALUES (?, ?)",
                (user_id, "see_own_routes"),
            )
            con.commit()
        except sqlite3.IntegrityError:
            return
        raise AssertionError("duplicate permission was accepted")


def test_default_admin_password_is_flagged(monkeypatch):
    with get_db() as con:
        assert schema.is_default_admin_password() is False
        con.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')",
            (schema.DEFAULT_PASSWORD_SETTING,),
        )
        con.commit()
    assert schema.is_default_admin_password() is True


def test_migrations_add_owner_id_to_legacy_table(tmp_path, monkeypatch):
    """A database whose routes table predates owner_id is upgraded, not dropped."""
    import sqlite3

    from app.core import config

    legacy = tmp_path / "legacy.db"
    con = sqlite3.connect(str(legacy))
    con.executescript(
        """
        CREATE TABLE routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hostname TEXT UNIQUE NOT NULL,
            backend TEXT NOT NULL,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO routes (hostname, backend) VALUES ('keep.example.com', '10.0.0.9:25565');
        """
    )
    con.commit()
    con.close()

    monkeypatch.setattr(config, "DB_PATH", legacy)
    monkeypatch.setattr(config, "SECRET_KEY", "")
    monkeypatch.setattr(config, "SECRET_KEY_ENV", "")

    schema.init_db()

    con = sqlite3.connect(str(legacy))
    con.row_factory = sqlite3.Row
    columns = {row["name"] for row in con.execute("PRAGMA table_info(routes)")}
    assert "owner_id" in columns, "owner_id was not added"
    rows = con.execute("SELECT hostname FROM routes").fetchall()
    con.close()
    assert [r["hostname"] for r in rows] == ["keep.example.com"], "existing data was lost"


def test_user_has_perm_admin_bypass():
    user_id = create_user("plain-user")
    admin = {"id": 1, "username": "admin", "role": "admin"}
    assert schema.user_has_perm(admin, "manage_users") is True
    assert schema.user_has_perm({"id": user_id, "role": "user"}, "manage_users") is False
    assert schema.user_has_perm({}, "manage_users") is False


def test_get_user_perms_returns_granted_set():
    user_id = create_user("perm-user", perms=["see_own_routes", "create_route"])
    assert schema.get_user_perms(user_id) == {"see_own_routes", "create_route"}


def test_hash_and_verify_password_roundtrip():
    from app.core.security import verify_password

    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong password", hashed) is False
    assert verify_password("x", "not-a-hash") is False
    assert verify_password("x", "pbkdf2_sha256$bad$parts") is False
