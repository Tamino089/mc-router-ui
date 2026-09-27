import sqlite3
from contextlib import contextmanager

from app.core.config import DB_PATH


@contextmanager
def get_db():
    con = sqlite3.connect(str(DB_PATH), timeout=5)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=5000")
        yield con
    finally:
        con.close()
