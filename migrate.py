"""Upgrade an existing inventory.db to the current version WITHOUT losing data.
Run once from the project folder:   python migrate.py
A backup is written first to inventory.db.bak. Running it again changes nothing new.

What it does:
  * gives every place its range number x (IP range 192.168.x.n), in the order the places were created;
  * keeps every valid IP address that is unique inside its place;
  * leaves a PC's IP as Not Assigned when it is blank or repeated inside its place (no automatic IPs).
"""
import ipaddress
import os
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path

from datetime import date
from queries import FAULT_CATEGORIES

BASE = Path(__file__).resolve().parent
DB = BASE / "inventory.db"


def valid_ipv4(s):
    try:
        ipaddress.IPv4Address(s or "")
        return True
    except ValueError:
        return False


def main():
    if not DB.exists():
        print("inventory.db not found - nothing to migrate (run python seed.py for demo data).")
        return
    old = sqlite3.connect(DB)
    old.row_factory = sqlite3.Row
    tables = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "locations" not in tables:
        old.close()
        raise SystemExit("This database is older than the 'locations' version; migrate it to version 5 first.")

    places = [(r["name"], r["prefix"]) for r in old.execute("SELECT name, prefix FROM locations ORDER BY rowid")]
    subnet = {name: i + 1 for i, (name, _) in enumerate(places)}
    assets = [dict(r) for r in old.execute("SELECT * FROM assets ORDER BY asset_id")]
    # Current fault fields (version 7): Operational = all NULL; a faulty PC keeps its category and details.
    # Old 'N/A' categories become 'Unknown'. Blank technician / action = not recorded. Date = the migration day
    # when it is not known. No history entries are invented for faults that already existed.
    for a in assets:
        if a["status"] == "Operational":
            for k in ["status_category", "status_detail", "status_action", "status_technician", "status_date"]:
                a[k] = None
        else:
            if a.get("status_category") not in FAULT_CATEGORIES:
                a["status_category"] = "Unknown"
            a["status_detail"] = a.get("status_detail") or ""
            a["status_action"] = a.get("status_action") or ""
            a["status_technician"] = a.get("status_technician") or ""
            a["status_date"] = a.get("status_date") or date.today().isoformat()
    events = [dict(r) for r in old.execute("SELECT * FROM maintenance ORDER BY id")]
    old.close()

    # 1. Decide each PC's IP: keep a valid IP the first time it appears in its place; re-assign the rest.
    used = defaultdict(set)
    final_ip = {}
    for a in assets:
        ip = (a["ip_address"] or "").strip()
        if valid_ipv4(ip) and ip not in used[a["location"]]:
            used[a["location"]].add(ip)
            final_ip[a["asset_id"]] = ip
    changed = []
    for a in assets:
        if a["asset_id"] not in final_ip:      # blank, or repeated inside its place: left as Not Assigned
            final_ip[a["asset_id"]] = None
            changed.append((a["asset_id"], a["location"], a["ip_address"] or "(blank)", "Not Assigned"))

    # 2. Rebuild the database from schema.sql and copy every row. The original is only replaced at the end,
    #    so a failure leaves inventory.db untouched.
    shutil.copy(DB, BASE / "inventory.db.bak")
    tmp = BASE / "inventory.new.db"
    if tmp.exists():
        tmp.unlink()
    new = sqlite3.connect(tmp)
    new.executescript((BASE / "schema.sql").read_text(encoding="utf-8"))
    new.executemany("INSERT INTO locations (name,prefix,ip_subnet) VALUES (?,?,?)",
                    [(n, p, subnet[n]) for n, p in places])

    def copy_rows(table, rows):
        cols = [r[1] for r in new.execute(f"PRAGMA table_info({table})")]
        new.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [[r.get(c) for c in cols] for r in rows])

    for a in assets:
        a["ip_address"] = final_ip[a["asset_id"]]
    copy_rows("assets", assets)
    copy_rows("maintenance", events)
    new.commit()
    new.close()
    os.replace(tmp, DB)

    print(f"Places (range number x): " + ", ".join(f"{n} = {subnet[n]}" for n, _ in places))
    print(f"Assets copied: {len(assets)}   Maintenance events copied: {len(events)}")
    if changed:
        print(f"IP addresses left as Not Assigned: {len(changed)}")
        for aid, loc, old_ip, ip in changed:
            print(f"  {aid:<12} {loc:<15} {old_ip:<16} -> {ip}")
    else:
        print("No IP addresses needed changing.")
    print("Backup saved as inventory.db.bak")


if __name__ == "__main__":
    main()
