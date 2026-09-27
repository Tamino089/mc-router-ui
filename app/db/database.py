"""
SQLite connection management with a context-manager API.
"""

import sqlite3
from contextlib import contextmanager

from app.core.config import DB_PATH


@contextmanager
def get_db():
    """Yield a sqlite3.Connection with a Row factory, closing it on exit.

    foreign_keys is a per-connection pragma, so it must be enabled here as well
    as in schema.init_db(); otherwise ON DELETE CASCADE silently never fires.
    """
    con = sqlite3.connect(str(DB_PATH), timeout=5)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=5000")
        yield con
    finally:
        con.close()
