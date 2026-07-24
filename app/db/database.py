"""
SQLite connection management with a clean context-manager API.
"""

import sqlite3
from contextlib import contextmanager

from app.core.config import DB_PATH


@contextmanager
def get_db():
    """Yield a sqlite3.Connection with Row factory. Auto-closes on exit.

    Pragmas applied on every connection:
      - journal_mode=WAL  : concurrent readers + single writer, far better
                             throughput than the default rollback journal.
      - foreign_keys=ON    : enforces ON DELETE CASCADE / REFERENCES declared
                             in the schema (off by default in sqlite3).
      - synchronous=NORMAL: safe under WAL, avoids an fsync per commit.
    """
    con = sqlite3.connect(str(DB_PATH), timeout=5)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA synchronous=NORMAL")
        yield con
    finally:
        con.close()
