"""Upgrade an existing inventory.db to the current version WITHOUT losing data.
Run once from the project folder:   python migrate.py        (or: python migrate.py path/to/inventory.db)
A backup is written first to inventory.db.bak. The original is only replaced when everything succeeded
and the new database has been checked (row counts, integrity, foreign keys).
Running it again on an up-to-date database does nothing (and does not touch the backup).

Two kinds of old database are recognised:

  * Version 14-15.x (it already has the fault_steps table, with a cost on each step).
    Everything is kept exactly as it is: assets, places (with their range numbers), IP addresses, history
    entries, and the steps of open faults (same order, same text). The only change: steps no longer carry
    a cost. The cost of an open fault is its single Updated Cost, which stays on the asset. If an open
    fault has no Updated Cost yet, the cost of its last step becomes the Updated Cost (what the page showed).
    Steps that only held a cost (no action) are removed; their cost is already in the Updated Cost.

  * Version 8-13 (no fault_steps table). Here the old rules are applied:
      - PC IP addresses are kept when valid and unique inside their place, otherwise left as Not Assigned;
      - history entries that are not complete (a field is blank or Unknown) are removed;
      - a valid history entry logged while a PC is still Faulty / Under Repair becomes a step of that fault
        and its cost becomes the Updated Cost when the fault had none;
      - the current action of an open fault becomes its first step, if it is not already one.
"""
import ipaddress
import os
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEFAULT_DB = BASE / "inventory.db"
CURRENT_ERROR_STATUSES = ("Faulty", "Under Repair")
PLACEHOLDERS = ("", "Unknown", "Not recorded")
FAULT_KEYS = ["status_category", "status_detail", "status_action", "status_technician", "status_date", "status_cost"]


def valid_ipv4(s):
    try:
        ipaddress.IPv4Address(s or "")
        return True
    except ValueError:
        return False


def is_incomplete(e):
    """A history entry is incomplete when a required field is blank or a placeholder."""
    return (e["fault_category"] in ("", "Unknown") or e["fault_description"] in PLACEHOLDERS
            or e["action_taken"] in PLACEHOLDERS or e["technician"] in PLACEHOLDERS or e["technician"] is None)


def columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def main(db_path=None):
    DB = Path(db_path) if db_path else DEFAULT_DB
    if not DB.exists():
        print(f"{DB.name} not found - nothing to migrate (run python seed.py for demo data).")
        return
    old = sqlite3.connect(DB)
    old.row_factory = sqlite3.Row
    tables = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "locations" not in tables:
        old.close()
        raise SystemExit("This database is older than version 5; migrate it to version 5 first.")

    has_steps = "fault_steps" in tables
    if has_steps and "cost_usd" not in columns(old, "fault_steps"):
        old.close()
        print("This database is already up to date - nothing changed.")
        return

    loc_cols = columns(old, "locations")
    if "ip_subnet" in loc_cols:       # keep each place's range number as it is
        places = [(r["name"], r["prefix"], r["ip_subnet"]) for r in
                  old.execute("SELECT name, prefix, ip_subnet FROM locations ORDER BY rowid")]
    else:
        places = [(r["name"], r["prefix"], i + 1) for i, r in
                  enumerate(old.execute("SELECT name, prefix FROM locations ORDER BY rowid"))]
    assets = [dict(r) for r in old.execute("SELECT * FROM assets ORDER BY asset_id")]
    events = [dict(r) for r in old.execute("SELECT * FROM maintenance ORDER BY id")]
    old_steps = [dict(r) for r in old.execute("SELECT * FROM fault_steps ORDER BY id")] if has_steps else []
    old.close()

    removed, converted, ip_changed, dropped_cost_only = [], [], [], []

    # 1. Current fault fields: Operational = all NULL; an open fault keeps its details.
    for a in assets:
        if a["status"] == "Operational":
            for k in FAULT_KEYS:
                a[k] = None
        else:
            a["status_category"] = a.get("status_category") or "Unknown"
            a["status_detail"] = a.get("status_detail") or ""
            a["status_action"] = a.get("status_action") or ""
            a["status_technician"] = a.get("status_technician") or ""
            a["status_date"] = a.get("status_date") or date.today().isoformat()
            # status_cost stays None here when unknown; it is settled below (never lose a recorded cost)

    open_assets = {a["asset_id"]: a for a in assets if a["status"] in CURRENT_ERROR_STATUSES}
    kept, steps = events, []

    if has_steps:
        # ---- Version 14-15.x: keep everything; steps lose their cost, the Updated Cost stays on the asset.
        last_step_cost = {}
        for s in old_steps:                               # ordered by id: the last one wins
            last_step_cost[s["asset_id"]] = s["cost_usd"]
        for aid, a in open_assets.items():
            if a["status_cost"] is None:
                a["status_cost"] = float(last_step_cost.get(aid) or 0)
        for s in old_steps:
            if (s["action"] or "").strip() == "":
                dropped_cost_only.append(s)               # held only a cost, which the Updated Cost already has
                continue
            steps.append({"id": s["id"], "asset_id": s["asset_id"], "step_date": s["step_date"],
                          "action": s["action"], "technician": s["technician"] or ""})
    else:
        # ---- Version 8-13: IP clean-up, remove incomplete history, open-fault work becomes steps.
        used = defaultdict(set)
        final_ip = {}
        for a in assets:
            ip = (a["ip_address"] or "").strip()
            if valid_ipv4(ip) and ip not in used[a["location"]]:
                used[a["location"]].add(ip)
                final_ip[a["asset_id"]] = ip
        for a in assets:
            if a["asset_id"] not in final_ip:
                final_ip[a["asset_id"]] = None
                if a["ip_address"]:
                    ip_changed.append((a["asset_id"], a["ip_address"]))
            a["ip_address"] = final_ip[a["asset_id"]]

        kept = []
        last_cost = {}
        for e in events:
            if is_incomplete(e):
                removed.append(e)
                continue
            a = open_assets.get(e["asset_id"])
            if a and e["event_date"] >= a["status_date"]:
                converted.append(e)
                steps.append({"asset_id": e["asset_id"], "step_date": e["event_date"], "action": e["action_taken"],
                              "technician": e["technician"]})
                last_cost[e["asset_id"]] = e["cost_usd"]
                continue
            kept.append(e)
        for aid, a in open_assets.items():
            act = (a["status_action"] or "").strip()
            if act and not any(s["asset_id"] == aid and s["action"] == act for s in steps):
                steps.append({"asset_id": aid, "step_date": a["status_date"], "action": act,
                              "technician": a["status_technician"]})
            if not a.get("status_cost"):                  # None or 0: the last converted cost is the best total
                a["status_cost"] = float(last_cost.get(aid) or 0)
    for a in open_assets.values():
        if a["status_cost"] is None:
            a["status_cost"] = 0.0
    no_technician = [aid for aid, a in open_assets.items()
                     if a["status"] == "Under Repair" and not (a["status_technician"] or "").strip()]

    # 2. Rebuild the database from schema.sql and copy the rows. The original is replaced only at the end.
    bak = DB.with_name(DB.name + ".bak")
    shutil.copy(DB, bak)
    tmp = DB.with_name("inventory.new.db")
    if tmp.exists():
        tmp.unlink()
    new = sqlite3.connect(tmp)
    new.executescript((BASE / "schema.sql").read_text(encoding="utf-8"))
    new.executemany("INSERT INTO locations (name,prefix,ip_subnet) VALUES (?,?,?)", places)

    def copy_rows(table, rows):
        cols = [r[1] for r in new.execute(f"PRAGMA table_info({table})")]
        new.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [[r.get(c) for c in cols] for r in rows])

    try:
        copy_rows("assets", assets)
        copy_rows("maintenance", kept)
        copy_rows("fault_steps", steps)
        new.commit()
    except sqlite3.Error as e:      # e.g. an old row breaks a current rule: leave the original untouched
        new.close()
        tmp.unlink()
        raise SystemExit(f"Migration stopped, your database was NOT changed (backup: {bak.name}). "
                         f"A row does not fit the current rules: {e}")

    # 3. Check the new database before it replaces the old one.
    problems = []
    for table, expected in (("locations", len(places)), ("assets", len(assets)),
                            ("maintenance", len(kept)), ("fault_steps", len(steps))):
        got = new.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if got != expected:
            problems.append(f"{table}: expected {expected} rows, found {got}")
    if new.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        problems.append("integrity check failed")
    if new.execute("PRAGMA foreign_key_check").fetchall():
        problems.append("foreign key check failed")
    new.close()
    if problems:
        tmp.unlink()
        raise SystemExit("Migration stopped, your database was NOT changed: " + "; ".join(problems))
    os.replace(tmp, DB)

    print(f"Assets copied: {len(assets)}   History entries kept: {len(kept)}   Steps of open faults: {len(steps)}")
    print("Places (range number): " + ", ".join(f"{n} = {sn}" for n, _, sn in places))
    if dropped_cost_only:
        print(f"\nSteps that only held a cost were removed: {len(dropped_cost_only)} "
              "(the cost of an open fault is its single Updated Cost)")
    if removed:
        print(f"\nIncomplete history entries removed: {len(removed)}")
        for e in removed:
            print(f"  {e['asset_id']:<12} {e['event_date']}  {e['fault_category']:<15} "
                  f"{(e['fault_description'] or '(blank)')[:34]:<35} action: {e['action_taken'] or '(blank)'}")
    if converted:
        print(f"\nValid entries of still-open faults moved into their steps: {len(converted)}")
        for e in converted:
            print(f"  {e['asset_id']:<12} {e['event_date']}  {e['action_taken']}  (cost ${e['cost_usd']:.2f})")
    if ip_changed:
        print(f"\nIP addresses left as Not Assigned (blank or repeated in their place): {len(ip_changed)}")
        for aid, ip in ip_changed:
            print(f"  {aid:<12} was {ip}")
    if no_technician:
        print("\nUnder Repair without a technician (set one on the Edit form before closing):")
        for aid in no_technician:
            print(f"  {aid}")
    print(f"\nBackup saved as {bak.name}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
