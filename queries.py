"""DATABASE LAYER: every SQL statement in the project lives in this file.
app.py (backend) calls these functions and never writes SQL itself.
All values are passed as ? parameters (never pasted into the SQL text), so input cannot inject SQL.
"""
import re

from db import get_db

# ---- Drop-down lists. app.py validates against these.
BASE_FAULTS = ["Disk failure", "Overheating", "Network issue", "RAM fault",
               "OS corruption", "Display issue", "PSU failure"]
FAULT_CATEGORIES = BASE_FAULTS + ["Other", "Unknown"]                    # maintenance event category
CURRENT_ERROR_CATEGORIES = BASE_FAULTS + ["N/A", "Unknown", "Other"]     # Faulty / Under Repair category
RAM_TYPES = ["DDR1", "DDR2", "DDR3", "DDR4", "DDR5"]

# ---- Current error: required for these statuses. Operational = N/A (stored as NULL).
CURRENT_ERROR_STATUSES = ("Faulty", "Under Repair")

# ---- Warranty label window (used in app.py)
WARRANTY_SOON_DAYS = 30

# Columns of the assets table that a user can enter (active is handled separately)
FIELDS = ["asset_id", "manufacturer", "model", "cpu", "cpu_family", "ram_gb", "ram_type",
          "storage_type", "storage_gb", "gpu", "gpu_vram_gb", "os", "ip_address", "location",
          "purchase_date", "warranty_expiry", "status", "status_category", "status_detail",
          "status_action", "status_technician", "status_date"]
CURRENT_FAULT_FIELDS = ["status_category", "status_detail", "status_action", "status_technician", "status_date"]

# Asset list sorting: column key from the URL -> SQL expression. Anything else is ignored.
SORT_COLUMNS = {
    "asset_id": "a.asset_id", "model": "a.model", "cpu": "a.cpu", "ram": "a.ram_gb",
    "storage": "a.storage_gb", "gpu": "a.gpu", "location": "a.location", "status": "a.status",
    "warranty": "a.warranty_expiry",
    "faults": "(SELECT COUNT(*) FROM maintenance m WHERE m.asset_id=a.asset_id)",
}

EVENT_FIELDS = ["id", "asset_id", "event_date", "fault_category", "fault_description",
                "action_taken", "technician", "cost_usd"]


def attention_sql():
    """SQL fragment (for table alias a) implementing the 'needs attention' rule: current errors only."""
    return "(a.active=1 AND a.status IN ('Faulty','Under Repair'))"


# ---- Locations ----------------------------------------------------------------
def location_names():
    return [r[0] for r in get_db().execute("SELECT name FROM locations ORDER BY name")]


def find_location(name):
    """The existing location with this name (upper/lower case ignored), or None."""
    r = get_db().execute("SELECT name FROM locations WHERE LOWER(name)=LOWER(?)", (name,)).fetchone()
    return r[0] if r else None


def location_prefix(name):
    r = get_db().execute("SELECT prefix FROM locations WHERE name=?", (name,)).fetchone()
    return r[0] if r else None


def all_prefixes():
    return {r[0] for r in get_db().execute("SELECT prefix FROM locations")}


def suggest_prefix(name, taken):
    """Short code for asset IDs made from a place name: 'Lab 4' -> 'L4', 'Server Room' -> 'SR'.
    A number is added if the code is already used."""
    tokens = re.findall(r"[A-Za-z0-9]+", name)
    base = "".join(t[0] for t in tokens).upper() if tokens else ""
    if len(base) < 2 and tokens:
        base = tokens[0][:3].upper()
    base = base or "LOC"
    prefix, n = base, 2
    while prefix in taken:
        prefix, n = f"{base}{n}", n + 1
    return prefix


def add_location(name, prefix, subnet):
    """Writes are committed, or rolled back and closed on error, so a failed save never leaves
    a transaction open (an open one would lock the database for the next request)."""
    db = get_db()
    try:
        db.execute("INSERT INTO locations (name,prefix,ip_subnet) VALUES (?,?,?)", (name, prefix, subnet))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def next_asset_id(prefix):
    """Next free ID for a prefix, e.g. LIB-PC-038. Retired IDs are never reused."""
    stem = f"{prefix}-PC-"
    nums = []
    for (aid,) in get_db().execute("SELECT asset_id FROM assets WHERE asset_id LIKE ?", (stem + "%",)):
        tail = aid[len(stem):]
        if tail.isdigit():
            nums.append(int(tail))
    return f"{stem}{(max(nums) if nums else 0) + 1:03d}"


# ---- Assets: read -----------------------------------------------------------
def asset_exists(asset_id):
    return get_db().execute("SELECT 1 FROM assets WHERE asset_id=?", (asset_id,)).fetchone() is not None


def get_asset(asset_id):
    return get_db().execute("SELECT * FROM assets WHERE asset_id=?", (asset_id,)).fetchone()


def filtered_assets(args):
    """Asset list with search + filters. args = request.args (a dict-like of query-string values)."""
    q = ("SELECT a.*, (SELECT COUNT(*) FROM maintenance m WHERE m.asset_id=a.asset_id) AS fault_count "
         "FROM assets a WHERE 1=1")
    p = []
    if args.get("show") == "retired":
        q += " AND a.active=0"
    elif args.get("show") != "all":
        q += " AND a.active=1"
    if args.get("q"):
        q += " AND (a.asset_id LIKE ? OR a.model LIKE ? OR a.manufacturer LIKE ? OR a.cpu LIKE ? OR a.ip_address LIKE ?)"
        p += [f"%{args['q']}%"] * 5
    for key in ["cpu_family", "ram_gb", "ram_type", "storage_type", "location", "status"]:
        if args.get(key):
            q += f" AND a.{key}=?"
            p.append(args[key])
    w = args.get("warranty")
    if w == "Expired":
        q += " AND a.warranty_expiry < date('now')"
    elif w == "Active":
        q += " AND a.warranty_expiry >= date('now')"
    if args.get("sort") == "status":
        first = args.get("first") if args.get("first") in ("Operational", "Under Repair", "Faulty") else "Operational"
        q += " ORDER BY (CASE WHEN a.status=? THEN 0 ELSE 1 END), a.status, a.asset_id"
        p.append(first)
    else:
        col = SORT_COLUMNS.get(args.get("sort"), SORT_COLUMNS["asset_id"])
        direction = "DESC" if args.get("dir") == "desc" else "ASC"
        q += f" ORDER BY {col} {direction}, a.asset_id"   # column and direction come from fixed lists
    return get_db().execute(q, p).fetchall()


def filter_options():
    """Values used to fill the filter drop-downs."""
    db = get_db()
    return {
        "cpu_family": [r[0] for r in db.execute("SELECT DISTINCT cpu_family FROM assets ORDER BY 1")],
        "ram_gb": [r[0] for r in db.execute("SELECT DISTINCT ram_gb FROM assets ORDER BY 1")],
        "location": location_names(),
    }


# ---- Assets: write ----------------------------------------------------------
def _insert_event(db, asset_id, ev):
    """One maintenance event, written inside the caller's transaction."""
    db.execute("INSERT INTO maintenance (asset_id,event_date,fault_category,fault_description,"
               "action_taken,technician,cost_usd) VALUES (?,?,?,?,?,?,?)",
               (asset_id, ev["event_date"], ev["fault_category"], ev.get("fault_description") or "",
                ev.get("action_taken") or "", ev.get("technician") or "", ev.get("cost", 0)))


def insert_asset(c, event=None):
    """c = validated dict with every key in FIELDS. May raise sqlite3.IntegrityError
    (the transaction is rolled back and the connection closed first)."""
    db = get_db()
    try:
        db.execute(f"INSERT INTO assets ({','.join(FIELDS)}, active) VALUES ({','.join('?' * len(FIELDS))}, 1)",
                   [c[k] for k in FIELDS])
        if event:
            _insert_event(db, c["asset_id"], event)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def update_asset(asset_id, c, event=None):
    """The primary key (asset_id) is never changed. event = optional history entry (new fault or fix)."""
    db = get_db()
    try:
        sets = ",".join(f"{k}=?" for k in FIELDS if k != "asset_id")
        db.execute(f"UPDATE assets SET {sets} WHERE asset_id=?",
                   [c[k] for k in FIELDS if k != "asset_id"] + [asset_id])
        if event:
            _insert_event(db, asset_id, event)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def set_active(asset_id, active):
    """Retire (0) or reactivate (1). Rows are never deleted, so history is kept."""
    db = get_db()
    try:
        db.execute("UPDATE assets SET active=? WHERE asset_id=?", (active, asset_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---- Maintenance ------------------------------------------------------------
def get_events(asset_id):
    return get_db().execute("SELECT * FROM maintenance WHERE asset_id=? ORDER BY event_date DESC, id DESC",
                            (asset_id,)).fetchall()


def add_event(asset_id, f, cost, set_status=None, current=None):
    """Save one maintenance event. set_status (optional) changes the asset's status in the same commit:
    - 'Operational' clears the current fault (the fix has been logged);
    - 'Faulty' / 'Under Repair' stores `current` (the fault: category, detail, action, technician, date)."""
    db = get_db()
    try:
        db.execute("INSERT INTO maintenance (asset_id,event_date,fault_category,fault_description,"
                   "action_taken,technician,cost_usd) VALUES (?,?,?,?,?,?,?)",
                   (asset_id, f["event_date"], f["fault_category"], f["fault_description"],
                    f["action_taken"], f["technician"], cost))
        if set_status == "Operational":
            db.execute("UPDATE assets SET status='Operational', status_category=NULL, status_detail=NULL, "
                       "status_action=NULL, status_technician=NULL, status_date=NULL WHERE asset_id=?", (asset_id,))
        elif set_status in CURRENT_ERROR_STATUSES:
            db.execute("UPDATE assets SET status=?, status_category=?, status_detail=?, status_action=?, "
                       "status_technician=?, status_date=? WHERE asset_id=?",
                       (set_status, current["category"], current["detail"], current["action"],
                        current["technician"], current["date"], asset_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def all_events():
    return get_db().execute("SELECT * FROM maintenance ORDER BY event_date DESC, id DESC").fetchall()


def fault_history(limit=None):
    """Maintenance events across ALL assets, newest first, whatever their current status.
    a.status is included so the page can mark finished work as (Done)."""
    sql = ("SELECT m.*, a.model, a.location, a.status FROM maintenance m "
           "JOIN assets a ON a.asset_id=m.asset_id ORDER BY m.event_date DESC, m.id DESC")
    if limit is None:
        return get_db().execute(sql).fetchall()
    return get_db().execute(sql + " LIMIT ?", (int(limit),)).fetchall()


# ---- Dashboard --------------------------------------------------------------
def _pairs(sql):
    return [(r[0], r[1]) for r in get_db().execute(sql).fetchall()]


def _bars(pairs):
    """[(label, count)] -> [(label, count, percent_of_largest)] for the bar charts."""
    pairs = list(pairs)
    biggest = max([n for _, n in pairs] + [1])
    return [(str(label), n, round(n * 100 / biggest)) for label, n in pairs]


def dashboard_data():
    """Everything the dashboard shows, all computed from the same tables with SQL.
    Charts count ACTIVE assets, so they always add up to stats['total']."""
    db = get_db()
    one = lambda sql: db.execute(sql).fetchone()[0]
    stats = {
        "total": one("SELECT COUNT(*) FROM assets WHERE active=1"),
        "operational": one("SELECT COUNT(*) FROM assets WHERE active=1 AND status='Operational'"),
        "attention": one(f"SELECT COUNT(*) FROM assets a WHERE {attention_sql()}"),
        "retired": one("SELECT COUNT(*) FROM assets WHERE active=0"),
        "faulty": one("SELECT COUNT(*) FROM assets WHERE active=1 AND status='Faulty'"),
        "repair": one("SELECT COUNT(*) FROM assets WHERE active=1 AND status='Under Repair'"),
        "events": one("SELECT COUNT(*) FROM maintenance"),
        "out_of_warranty": one("SELECT COUNT(*) FROM assets WHERE active=1 AND warranty_expiry < date('now')"),
        "total_cost": one("SELECT COALESCE(SUM(cost_usd),0) FROM maintenance"),
    }

    in_warranty, out_warranty = db.execute(
        "SELECT COALESCE(SUM(CASE WHEN warranty_expiry >= date('now') THEN 1 ELSE 0 END),0), "
        "COALESCE(SUM(CASE WHEN warranty_expiry < date('now') THEN 1 ELSE 0 END),0) "
        "FROM assets WHERE active=1").fetchone()

    charts = {
        "CPU family": _bars(_pairs(
            "SELECT cpu_family, COUNT(*) AS n FROM assets WHERE active=1 GROUP BY cpu_family ORDER BY n DESC")),
        "GPU": _bars(_pairs(
            "SELECT CASE WHEN gpu IS NULL OR TRIM(gpu)='' OR LOWER(gpu) LIKE '%integrated%' "
            "THEN 'Integrated' ELSE TRIM(gpu) END AS g, COUNT(*) AS n FROM assets WHERE active=1 "
            "GROUP BY g ORDER BY n DESC")),
        "RAM (GB)": _bars((f"{gb} GB", n) for gb, n in _pairs(
            "SELECT ram_gb, COUNT(*) AS n FROM assets WHERE active=1 GROUP BY ram_gb ORDER BY ram_gb")),
        "Storage type": _bars(_pairs(
            "SELECT storage_type, COUNT(*) AS n FROM assets WHERE active=1 GROUP BY storage_type ORDER BY n DESC")),
        # every place is listed, including places with no assets yet (LEFT JOIN)
        "Assets per location": _bars(_pairs(
            "SELECT l.name, COUNT(a.asset_id) AS n FROM locations l "
            "LEFT JOIN assets a ON a.location=l.name AND a.active=1 "
            "GROUP BY l.name ORDER BY n DESC, l.name")),
        "Warranty": _bars([("In warranty", in_warranty), ("Out of warranty", out_warranty)]),
    }

    # Failure analysis: every drop-down category is always shown (0 if unused). Any stored value
    # that is not in the list is counted as "Other", so no event is ever left out of the chart.
    counts = {cat: 0 for cat in FAULT_CATEGORIES}
    for label, n in _pairs("SELECT fault_category, COUNT(*) FROM maintenance GROUP BY fault_category"):
        counts[label if label in counts else "Other"] += n
    fault_cats = _bars(sorted(counts.items(), key=lambda kv: -kv[1]))

    top_assets = db.execute(
        "SELECT m.asset_id, a.model, COUNT(*) AS n, SUM(m.cost_usd) AS cost FROM maintenance m "
        "JOIN assets a ON a.asset_id=m.asset_id GROUP BY m.asset_id ORDER BY n DESC, cost DESC LIMIT 5"
    ).fetchall()
    attention = db.execute(
        f"SELECT a.asset_id, a.model, a.location, a.status, a.status_category, a.status_detail "
        f"FROM assets a WHERE {attention_sql()} ORDER BY a.asset_id").fetchall()
    return {"stats": stats, "charts": charts, "fault_cats": fault_cats,
            "top_assets": top_assets, "attention": attention}


# ---- IP addresses -------------------------------------------------------------
def location_subnet(name):
    """x of this place's range 192.168.x.n, or None for an unknown place."""
    r = get_db().execute("SELECT ip_subnet FROM locations WHERE name=?", (name,)).fetchone()
    return r[0] if r else None


def next_subnet():
    """Range number for the next new place: one more than the highest in use."""
    return get_db().execute("SELECT COALESCE(MAX(ip_subnet),0)+1 FROM locations").fetchone()[0]


def ip_owner(location, ip, exclude_id=""):
    """The PC that already has this IP in this place, or None."""
    r = get_db().execute("SELECT asset_id FROM assets WHERE location=? AND ip_address=? AND asset_id<>?",
                         (location, ip, exclude_id or "")).fetchone()
    return r[0] if r else None
