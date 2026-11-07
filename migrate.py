"""Upgrade an existing inventory.db to the current version WITHOUT losing data.
Run once from the project folder:   python migrate.py
A backup is written first to inventory.db.bak. The original is only replaced when everything succeeded.

Changes made by this version:
  * Updated Cost: each open fault gets assets.status_cost, its current total. Earlier per-step costs are added up
    into it. Steps no longer carry a cost (fault_steps.cost_usd is removed).
  * History entries that are not complete (a blank or Unknown field) are removed. Valid entries logged while a
    fault was still open become steps of that fault (older versions only).
  * IP addresses are kept when valid and unique inside their place; otherwise they are Not Assigned.
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


def columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


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
    old_steps = [dict(r) for r in old.execute("SELECT * FROM fault_steps ORDER BY id")] if "fault_steps" in tables else []
    steps_have_cost = "fault_steps" in tables and "cost_usd" in columns(old, "fault_steps")
    legacy = "fault_steps" not in tables           # before version 9: work on an open fault was logged as history
    old.close()

    # 1. Current fault fields: Operational = all NULL; an open fault keeps its details.
    for a in assets:
        if a["status"] == "Operational":
            for k in ["status_category", "status_detail", "status_action", "status_technician", "status_date", "status_cost"]:
                a[k] = None
        else:
            a["status_category"] = a.get("status_category") or "Unknown"
            a["status_detail"] = a.get("status_detail") or ""
            a["status_action"] = a.get("status_action") or ""
            a["status_technician"] = a.get("status_technician") or ""
            a["status_date"] = a.get("status_date") or date.today().isoformat()
    open_assets = {a["asset_id"]: a for a in assets if a["status"] in CURRENT_ERROR_STATUSES}

    # 2. History: remove incomplete entries; before version 9, valid work on an open fault becomes a step.
    cost_of = defaultdict(float)                  # the cost already spent on each open fault (sum of its steps)
    if steps_have_cost:
        for s in old_steps:
            cost_of[s["asset_id"]] += s.get("cost_usd") or 0
    removed, converted, kept, steps = [], [], [], []
    for e in events:
        if is_incomplete(e):
            removed.append(e)
            continue
        a = open_assets.get(e["asset_id"])
        if legacy and a and e["event_date"] >= a["status_date"]:
            converted.append(e)
            cost_of[e["asset_id"]] += e["cost_usd"] or 0
            steps.append({"asset_id": e["asset_id"], "step_date": e["event_date"], "action": e["action_taken"],
                          "technician": e["technician"]})
            continue
        kept.append(e)
    for s in old_steps:
        steps.append({"asset_id": s["asset_id"], "step_date": s["step_date"], "action": s["action"],
                      "technician": s["technician"]})
    for aid, a in open_assets.items():           # the current action of an open fault is its latest step
        act = (a["status_action"] or "").strip()
        if act and not any(s["asset_id"] == aid and s["action"] == act for s in steps):
            steps.append({"asset_id": aid, "step_date": a["status_date"], "action": act,
                          "technician": a["status_technician"]})

    # 3. Updated Cost of each open fault: kept if the database already has it, otherwise the sum of its steps.
    for aid, a in open_assets.items():
        if a.get("status_cost") is None:
            a["status_cost"] = round(cost_of[aid], 2)
    cost_report = [(aid, a["status_cost"]) for aid, a in open_assets.items() if a["status_cost"]]

    # 4. IP addresses: keep a valid IP the first time it appears in its place; everything else is Not Assigned.
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
    no_technician = [aid for aid, a in open_assets.items()
                     if a["status"] == "Under Repair" and not (a["status_technician"] or "").strip()]

    # 5. Rebuild the database from schema.sql and copy the rows. The original is replaced only at the end.
    shutil.copy(DB, BASE / "inventory.db.bak")
    tmp = BASE / "inventory.new.db"
    if tmp.exists():
        tmp.unlink()
    new = sqlite3.connect(tmp)
    new.executescript((BASE / "schema.sql").read_text(encoding="utf-8"))
    new.executemany("INSERT INTO locations (name,prefix,ip_subnet) VALUES (?,?,?)",
                    [(n, p, subnet[n]) for n, p in places])

    def copy_rows(table, rows, cols=None):
        cols = cols or [r[1] for r in new.execute(f"PRAGMA table_info({table})")]
        new.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [[r.get(c) for c in cols] for r in rows])

    copy_rows("assets", assets)
    copy_rows("maintenance", kept)
    copy_rows("fault_steps", steps, ["asset_id", "step_date", "action", "technician"])
    new.commit()
    new.close()
    os.replace(tmp, DB)

    print(f"Assets copied: {len(assets)}   History entries kept: {len(kept)}   Steps: {len(steps)}")
    print("Places (range number): " + ", ".join(f"{n} = {subnet[n]}" for n, _ in places))
    if removed:
        print(f"\nIncomplete history entries removed: {len(removed)}")
        for e in removed:
            print(f"  {e['asset_id']:<12} {e['event_date']}  {e['fault_category']:<15} "
                  f"{(e['fault_description'] or '(blank)')[:34]:<35} action: {e['action_taken'] or '(blank)'}")
    if converted:
        print(f"\nValid entries of still-open faults moved into their steps: {len(converted)}")
    if cost_report:
        print("\nUpdated Cost of open faults (sum of their earlier step costs):")
        for aid, cost in cost_report:
            print(f"  {aid:<12} ${cost:,.2f}")
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
