"""
Database schema creation, migrations, and initial bootstrap.
"""

import logging
import secrets
import sqlite3

from app.core import config
from app.core.security import hash_password

logger = logging.getLogger(__name__)


def init_db() -> str:
    """Initialize the database and return the SECRET_KEY to use for sessions."""
    if config.SECRET_KEY:
        return config.SECRET_KEY

    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(config.DB_PATH))
    con.row_factory = sqlite3.Row

    # ── Persistent WAL pragmas (set once, persist at DB-file level) ──────────
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA synchronous=NORMAL")

    # ── Migration: if routes table exists but has wrong schema, recreate ──────
    try:
        con.execute("SELECT hostname FROM routes LIMIT 1")
    except sqlite3.OperationalError:
        logger.warning("DB schema outdated or missing, recreating tables...")
        con.executescript(
            "DROP TABLE IF EXISTS routes; "
            "DROP TABLE IF EXISTS settings; "
            "DROP TABLE IF EXISTS users; "
            "DROP TABLE IF EXISTS permissions; "
            "DROP TABLE IF EXISTS health_checks; "
            "DROP TABLE IF EXISTS health_history;"
        )

    # ── Core tables ───────────────────────────────────────────────────────────
    con.executescript("""
        CREATE TABLE IF NOT EXISTS routes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            hostname    TEXT UNIQUE NOT NULL,
            backend     TEXT NOT NULL,
            is_default  INTEGER NOT NULL DEFAULT 0,
            source      TEXT NOT NULL DEFAULT 'static',
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

    # ── Non-destructive migration: add source column to routes ─────────────────
    try:
        con.execute("SELECT source FROM routes LIMIT 1")
    except sqlite3.OperationalError:
        logger.info("Migrating: adding source column to routes table...")
        con.execute(
            "ALTER TABLE routes ADD COLUMN source TEXT NOT NULL DEFAULT 'static'"
        )
        con.commit()

    # ── Non-destructive migration: add owner_id to routes ─────────────────────
    try:
        con.execute("SELECT owner_id FROM routes LIMIT 1")
    except sqlite3.OperationalError:
        logger.info("Migrating: adding owner_id column to routes table...")
        con.execute(
            "ALTER TABLE routes ADD COLUMN owner_id INTEGER REFERENCES users(id)"
        )
        con.commit()

    # ── Persistent SECRET_KEY ─────────────────────────────────────────────────
    if config.SECRET_KEY_ENV:
        secret_key = config.SECRET_KEY_ENV
        con.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('secret_key', ?)",
            (secret_key,),
        )
        logger.info("Using SECRET_KEY from environment variable.")
    else:
        stored = con.execute(
            "SELECT value FROM settings WHERE key='secret_key'"
        ).fetchone()
        if stored:
            secret_key = stored[0]
            logger.info("Loaded SECRET_KEY from persistent database.")
        else:
            secret_key = secrets.token_hex(32)
            con.execute(
                "INSERT INTO settings (key, value) VALUES ('secret_key', ?)",
                (secret_key,),
            )
            logger.info("Generated new SECRET_KEY and persisted to database.")
    config.SECRET_KEY = secret_key

    # ── Admin user ────────────────────────────────────────────────────────────
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
            logger.info("Migrating plain-text admin password to hashed users table...")
        else:
            password = config.ADMIN_PASS
            logger.info(
                "No admin user found. Creating admin user '%s'...",
                config.ADMIN_USER.lower(),
            )
        hashed = hash_password(password)
        con.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
            (config.ADMIN_USER.lower(), hashed),
        )
        con.execute("DELETE FROM settings WHERE key='admin_password'")
        if password == "changeme":
            logger.critical(
                "Admin account '%s' created with the DEFAULT password. Set "
                "ADMIN_PASSWORD when starting the container and change the "
                "password in Settings as soon as possible.",
                config.ADMIN_USER.lower(),
            )
    else:
        # ADMIN_PASSWORD bootstraps a missing admin only. Reapplying it here
        # would overwrite passwords changed through the UI on every restart.
        logger.info("Existing admin account retained; ADMIN_PASSWORD is bootstrap-only.")

    # ── Assign ownerless routes to admin ──────────────────────────────────────
    admin_row = con.execute(
        "SELECT id FROM users WHERE LOWER(username)=LOWER(?)",
        (config.ADMIN_USER,),
    ).fetchone()
    if admin_row:
        con.execute(
            "UPDATE routes SET owner_id=? WHERE owner_id IS NULL",
            (admin_row["id"],),
        )

    # ── Crafty env-vars -> DB ─────────────────────────────────────────────────
    if config.CRAFTY_URL_ENV:
        con.execute(
            "INSERT OR REPLACE INTO settings (key,value) VALUES ('crafty_url',?)",
            (config.CRAFTY_URL_ENV,),
        )
        logger.info("CRAFTY_URL set from environment variable.")
    if config.CRAFTY_API_KEY_ENV:
        con.execute(
            "INSERT OR REPLACE INTO settings (key,value) VALUES ('crafty_token',?)",
            (config.CRAFTY_API_KEY_ENV,),
        )
        logger.info("CRAFTY_API_KEY set from environment variable.")
    if config.CRAFTY_CONTAINER_HOST_ENV:
        con.execute(
            "INSERT OR REPLACE INTO settings (key,value) VALUES ('crafty_container_host',?)",
            (config.CRAFTY_CONTAINER_HOST_ENV,),
        )
        logger.info("CRAFTY_CONTAINER_HOST set from environment variable.")

    con.commit()
    con.close()
    return secret_key


# ── Permission helpers ────────────────────────────────────────────────────────

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
    """Return the set of permission strings for a user."""
    from app.db.database import get_db
    with get_db() as con:
        rows = con.execute(
            "SELECT permission FROM permissions WHERE user_id=?",
            (user_id,),
        ).fetchall()
        return {r[0] for r in rows}


def grant_default_permissions(user_id: int, con):
    """Grant the default user permissions. Called when creating a new user."""
    for perm in config.DEFAULT_USER_PERMISSIONS:
        con.execute(
            "INSERT OR IGNORE INTO permissions (user_id, permission) VALUES (?, ?)",
            (user_id, perm),
        )
