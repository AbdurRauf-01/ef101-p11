"""Database helpers (SQLite via the built-in sqlite3 module)."""
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "inventory.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")   # enforce maintenance.asset_id -> assets
    return conn


def init_db():
    """(Re)create the tables from schema.sql. Used by seed.py; wipes existing data."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript((BASE / "schema.sql").read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
