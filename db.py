"""Database helpers (SQLite via the built-in sqlite3 module).

The data lives in inventory.db, in the same folder as this program. Copy the folder and the data goes with it;
launching the app never deletes data. Set the environment variable INVENTORY_DB to use another file.
"""
import os
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("INVENTORY_DB") or (BASE / "inventory.db"))


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")   # enforce maintenance.asset_id -> assets
    return conn


def has_tables():
    """True when the database file already has the inventory tables (data may be in it)."""
    if not DB_PATH.exists():
        return False
    conn = sqlite3.connect(DB_PATH)
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='assets'").fetchone() is not None
    finally:
        conn.close()


# The standard places a new database starts with (no assets): name -> short code for asset IDs.
DEFAULT_PLACES = {"Lab 1": "L1", "Lab 2": "L2", "Lab 3": "L3", "Server Room": "SRV",
                  "Faculty Office": "FAC", "Library": "LIB"}


def ensure_database():
    """Launching the app: create an EMPTY database (the places, but no assets) the first time.
    Existing data is never touched. Returns True when a new database was created."""
    if has_tables():
        return False
    conn = sqlite3.connect(DB_PATH)
    conn.executescript((BASE / "schema.sql").read_text(encoding="utf-8"))
    conn.executemany("INSERT INTO locations (name,prefix,ip_subnet) VALUES (?,?,?)",
                     [(name, prefix, i + 1) for i, (name, prefix) in enumerate(DEFAULT_PLACES.items())])
    conn.commit()
    conn.close()
    return True


def schema_problem():
    """None when inventory.db has the current structure, otherwise a short message telling what to do.
    Catches an old copy of the database (it would otherwise fail later with a 'no such table/column' page)."""
    if not has_tables():
        return None
    conn = sqlite3.connect(DB_PATH)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"locations", "assets", "maintenance", "fault_steps"} <= tables:
            return "inventory.db is from an older version (a table is missing)."
        step_cols = {r[1] for r in conn.execute("PRAGMA table_info(fault_steps)")}
        asset_cols = {r[1] for r in conn.execute("PRAGMA table_info(assets)")}
        if "cost_usd" in step_cols or "status_cost" not in asset_cols:
            return "inventory.db is from an older version (its structure is out of date)."
        return None
    except sqlite3.Error as e:
        return f"inventory.db cannot be read ({e})."
    finally:
        conn.close()


def init_db():
    """(Re)create the tables from schema.sql. Used by seed.py; WIPES existing data."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript((BASE / "schema.sql").read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
