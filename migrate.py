"""Upgrade an existing inventory.db to the current version WITHOUT losing valid data.
Run once from the project folder:   python migrate.py
A backup is written first to inventory.db.bak. The original is only replaced when everything succeeded.

What it does:
  * places keep their range number; PC IP addresses are kept when valid and unique inside their place,
    otherwise they are left as Not Assigned (no automatic IPs);
  * history entries that are not complete (a field is blank or Unknown) are removed: an open fault
    is only recorded in the history once the PC is Operational again, with all fields filled in;
  * a valid history entry logged while a PC is still Faulty / Under Repair becomes a step of that open fault;
  * the current action of an open fault becomes its first step, if it is not already one.
"""
import ipaddress
import os
import shutil
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / "inventory.db"
CURRENT_ERROR_STATUSES = ("Faulty", "Under Repair")
PLACEHOLDERS = ("", "Unknown", "Not recorded")


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


def main():
    if not DB.exists():
        print("inventory.db not found - nothing to migrate (run python seed.py for demo data).")
        return
    old = sqlite3.connect(DB)
    old.row_factory = sqlite3.Row
    tables = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "locations" not in tables:
        old.close()
        raise SystemExit("This database is older than version 5; migrate it to version 5 first.")

    places = [(r["name"], r["prefix"]) for r in old.execute("SELECT name, prefix FROM locations ORDER BY rowid")]
    subnet = {name: i + 1 for i, (name, _) in enumerate(places)}
    assets = [dict(r) for r in old.execute("SELECT * FROM assets ORDER BY asset_id")]
    events = [dict(r) for r in old.execute("SELECT * FROM maintenance ORDER BY id")]
    old.close()

    # 1. Current fault fields: Operational = all NULL; an open fault keeps its details.
    for a in assets:
        if a["status"] == "Operational":
            for k in ["status_category", "status_detail", "status_action", "status_technician", "status_date"]:
                a[k] = None
        else:
            a["status_category"] = a.get("status_category") or "Unknown"
            a["status_detail"] = a.get("status_detail") or ""
            a["status_action"] = a.get("status_action") or ""
            a["status_technician"] = a.get("status_technician") or ""
            a["status_date"] = a.get("status_date") or date.today().isoformat()

    # 2. IP addresses: keep a valid IP the first time it appears in its place; everything else is Not Assigned.
    used = defaultdict(set)
    final_ip = {}
    for a in assets:
        ip = (a["ip_address"] or "").strip()
        if valid_ipv4(ip) and ip not in used[a["location"]]:
            used[a["location"]].add(ip)
            final_ip[a["asset_id"]] = ip
    ip_changed = []
    for a in assets:
        if a["asset_id"] not in final_ip:
            final_ip[a["asset_id"]] = None
            if a["ip_address"]:
                ip_changed.append((a["asset_id"], a["ip_address"]))
        a["ip_address"] = final_ip[a["asset_id"]]

    # 3. History: remove incomplete entries; turn work done on an open fault into steps of that fault.
    open_assets = {a["asset_id"]: a for a in assets if a["status"] in CURRENT_ERROR_STATUSES}
    removed, converted, kept, steps = [], [], [], []
    for e in events:
        if is_incomplete(e):
            removed.append(e)
            continue
        a = open_assets.get(e["asset_id"])
        if a and e["event_date"] >= a["status_date"]:
            converted.append(e)
            steps.append({"asset_id": e["asset_id"], "step_date": e["event_date"], "action": e["action_taken"],
                          "technician": e["technician"], "cost_usd": e["cost_usd"]})
            continue
        kept.append(e)
    for aid, a in open_assets.items():
        act = (a["status_action"] or "").strip()
        if act and not any(s["asset_id"] == aid and s["action"] == act for s in steps):
            steps.append({"asset_id": aid, "step_date": a["status_date"], "action": act,
                          "technician": a["status_technician"], "cost_usd": 0})
    no_technician = [aid for aid, a in open_assets.items()
                     if a["status"] == "Under Repair" and not (a["status_technician"] or "").strip()]

    # 4. Rebuild the database from schema.sql and copy the rows. The original is replaced only at the end.
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

    copy_rows("assets", assets)
    copy_rows("maintenance", kept)
    copy_rows("fault_steps", steps)
    new.commit()
    new.close()
    os.replace(tmp, DB)

    print(f"Assets copied: {len(assets)}   History entries kept: {len(kept)}   Steps of open faults: {len(steps)}")
    print("Places (range number): " + ", ".join(f"{n} = {subnet[n]}" for n, _ in places))
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
    print("\nBackup saved as inventory.db.bak")


if __name__ == "__main__":
    main()
