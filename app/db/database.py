"""
SQLite connection management with a clean context-manager API.
"""

import sqlite3
from contextlib import contextmanager

from app.core.config import DB_PATH


@contextmanager
def get_db():
    """Yield a sqlite3.Connection with Row factory. Auto-closes on exit.

    PRAGMA foreign_keys=ON must be set per-connection (it is not a file-level
    pragma in SQLite) so foreign key cascades work at runtime.
    WAL mode and synchronous=NORMAL are set once in schema.init_db() — they
    persist at the database-file level.
    """
    con = sqlite3.connect(str(DB_PATH), timeout=5)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys=ON")
        yield con
    finally:
        con.close()
