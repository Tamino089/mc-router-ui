"""
SQLite connection management with a clean context-manager API.
"""

import sqlite3
from contextlib import contextmanager

from app.core.config import DB_PATH


@contextmanager
def get_db():
    """Yield a sqlite3.Connection with Row factory. Auto-closes on exit.

    WAL mode, foreign_keys, and synchronous are set once in schema.init_db()
    and persist at the database-file level.
    """
    con = sqlite3.connect(str(DB_PATH), timeout=5)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()
