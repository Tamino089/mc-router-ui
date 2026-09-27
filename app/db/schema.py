"""
Database schema creation, migrations, and initial bootstrap.
"""

import logging
import secrets
import sqlite3

from app.core import config
from app.core.security import hash_password

logger = logging.getLogger(__name__)

# Marker set while the bootstrap admin still uses ADMIN_PASSWORD unchanged, so
# the UI can nag until a real password is chosen.
DEFAULT_PASSWORD_SETTING = "admin_password_is_default"


def init_db() -> str:
    """Initialize the database and return the SECRET_KEY to use for sessions."""
    if config.SECRET_KEY:
        return config.SECRET_KEY

    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(config.DB_PATH))
    con.row_factory = sqlite3.Row

    # journal_mode and synchronous persist in the database file; foreign_keys is
    # per-connection and is therefore also set by get_db().
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA synchronous=NORMAL")

    is_fresh = not con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' LIMIT 1"
    ).fetchone()

    # A routes table without a hostname column predates the current schema and
    # cannot be migrated in place. Only reached for genuinely old databases;
    # anything else is upgraded in place below.
    try:
        con.execute("SELECT hostname FROM routes LIMIT 1")
    except sqlite3.OperationalError:
        if is_fresh:
            logger.info("Initializing new database at %s", config.DB_PATH)
        else:
            logger.warning(
                "Database at %s has an outdated schema; recreating tables",
                config.DB_PATH,
            )
        # Children first, with enforcement off so the drops cannot trip
        # foreign-key checks on partially dropped schemas.
        con.execute("PRAGMA foreign_keys=OFF")
        con.executescript(
            "DROP TABLE IF EXISTS health_history; "
            "DROP TABLE IF EXISTS health_checks; "
            "DROP TABLE IF EXISTS permissions; "
            "DROP TABLE IF EXISTS routes; "
            "DROP TABLE IF EXISTS users; "
            "DROP TABLE IF EXISTS settings;"
        )
        con.execute("PRAGMA foreign_keys=ON")

    con.executescript("""
        CREATE TABLE IF NOT EXISTS routes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            hostname    TEXT UNIQUE NOT NULL,
            backend     TEXT NOT NULL,
            is_default  INTEGER NOT NULL DEFAULT 0,
            source      TEXT NOT NULL DEFAULT 'static',
            owner_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL CHECK(role IN ('admin', 'user')),
            created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS permissions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            permission  TEXT NOT NULL,
            UNIQUE(user_id, permission)
        );
        CREATE TABLE IF NOT EXISTS health_checks (
            route_id    INTEGER PRIMARY KEY REFERENCES routes(id) ON DELETE CASCADE,
            healthy     INTEGER NOT NULL DEFAULT 0,
            latency_ms  REAL,
            checked_at  TEXT NOT NULL DEFAULT (datetime('now')),
            error       TEXT
        );
        CREATE TABLE IF NOT EXISTS health_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            route_id    INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
            healthy     INTEGER NOT NULL DEFAULT 0,
            latency_ms  REAL,
            checked_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_health_history_route_time
            ON health_history(route_id, checked_at DESC);
    """)

    # Databases created before owner_id was added to the base schema.
    try:
        con.execute("SELECT owner_id FROM routes LIMIT 1")
    except sqlite3.OperationalError:
        logger.info("Migrating: adding owner_id column to routes")
        con.execute("ALTER TABLE routes ADD COLUMN owner_id INTEGER REFERENCES users(id)")
        con.commit()

    # Persistent session signing key.
    if config.SECRET_KEY_ENV:
        secret_key = config.SECRET_KEY_ENV
        con.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('secret_key', ?)",
            (secret_key,),
        )
        logger.info("Using SECRET_KEY from environment variable")
    else:
        stored = con.execute(
            "SELECT value FROM settings WHERE key='secret_key'"
        ).fetchone()
        if stored:
            secret_key = stored[0]
            logger.info("Loaded SECRET_KEY from database")
        else:
            secret_key = secrets.token_hex(32)
            con.execute(
                "INSERT INTO settings (key, value) VALUES ('secret_key', ?)",
                (secret_key,),
            )
            logger.info("Generated new SECRET_KEY and persisted it to the database")
    config.SECRET_KEY = secret_key

    admin_exists = con.execute(
        "SELECT id FROM users WHERE LOWER(username)=LOWER(?)",
        (config.ADMIN_USER,),
    ).fetchone()

    if not admin_exists:
        old_pw_row = con.execute(
            "SELECT value FROM settings WHERE key='admin_password'"
        ).fetchone()
        if old_pw_row:
            password = old_pw_row[0]
            logger.info("Migrating legacy plain-text admin password")
        else:
            password = config.ADMIN_PASS
            logger.info("Creating admin user '%s'", config.ADMIN_USER.lower())
        con.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
            (config.ADMIN_USER.lower(), hash_password(password)),
        )
        con.execute("DELETE FROM settings WHERE key='admin_password'")
        if password == "changeme":
            con.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')",
                (DEFAULT_PASSWORD_SETTING,),
            )
            logger.critical(
                "Admin user '%s' was created with the default password. Set "
                "ADMIN_PASSWORD and change it in Settings before exposing this "
                "instance.",
                config.ADMIN_USER.lower(),
            )
    else:
        # ADMIN_PASSWORD only bootstraps a missing admin; reapplying it would
        # overwrite a password changed through the UI on every restart.
        logger.info("Existing admin account retained")

    # Routes created before ownership existed belong to the bootstrap admin.
    admin_row = con.execute(
        "SELECT id FROM users WHERE LOWER(username)=LOWER(?)",
        (config.ADMIN_USER,),
    ).fetchone()
    if admin_row:
        con.execute(
            "UPDATE routes SET owner_id=? WHERE owner_id IS NULL",
            (admin_row["id"],),
        )

    # Environment configuration is applied over stored settings on every start.
    for key, value, label in (
        ("crafty_url", config.CRAFTY_URL_ENV, "CRAFTY_URL"),
        ("crafty_token", config.CRAFTY_API_KEY_ENV, "CRAFTY_API_KEY"),
        (
            "crafty_container_host",
            config.CRAFTY_CONTAINER_HOST_ENV,
            "CRAFTY_CONTAINER_HOST",
        ),
    ):
        if value:
            con.execute(
                "INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                (key, value),
            )
            logger.info("%s applied from environment variable", label)

    con.commit()
    con.close()
    return secret_key


def is_default_admin_password() -> bool:
    """True while the bootstrap admin password has never been changed."""
    from app.db.database import get_db

    with get_db() as con:
        row = con.execute(
            "SELECT value FROM settings WHERE key=?", (DEFAULT_PASSWORD_SETTING,)
        ).fetchone()
    return bool(row and row[0] == "1")


def user_has_perm(user: dict, permission: str) -> bool:
    """Admins bypass all permission checks."""
    if not user:
        return False
    if user.get("role") == "admin":
        return True
    from app.db.database import get_db

    with get_db() as con:
        row = con.execute(
            "SELECT id FROM permissions WHERE user_id=? AND permission=?",
            (user["id"], permission),
        ).fetchone()
        return row is not None


def get_user_perms(user_id: int) -> set:
    """Return the set of permission strings granted to a user."""
    from app.db.database import get_db

    with get_db() as con:
        rows = con.execute(
            "SELECT permission FROM permissions WHERE user_id=?",
            (user_id,),
        ).fetchall()
        return {r[0] for r in rows}


def grant_default_permissions(user_id: int, con) -> None:
    """Grant the default permission set to a newly created user."""
    for perm in config.DEFAULT_USER_PERMISSIONS:
        con.execute(
            "INSERT OR IGNORE INTO permissions (user_id, permission) VALUES (?, ?)",
            (user_id, perm),
        )
