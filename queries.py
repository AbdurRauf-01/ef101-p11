"""DATABASE LAYER: every SQL statement in the project lives in this file.
app.py (backend) calls these functions and never writes SQL itself.
All values are passed as ? parameters (never pasted into the SQL text), so input cannot inject SQL.
"""
import re

from db import get_db


def _all(sql, params=()):
    """Run a read query on its own connection, return every row, and close the connection."""
    db = get_db()
    try:
        return db.execute(sql, params).fetchall()
    finally:
        db.close()


def _one(sql, params=()):
    """Run a read query on its own connection, return the first row (or None), and close the connection."""
    db = get_db()
    try:
        return db.execute(sql, params).fetchone()
    finally:
        db.close()

# ---- Drop-down lists. app.py validates against these.
BASE_FAULTS = ["Disk failure", "Overheating", "Network issue", "RAM fault",
               "OS corruption", "Display issue", "PSU failure"]
FAULT_CATEGORIES = BASE_FAULTS + ["Other", "Unknown"]                    # maintenance event category
RAM_TYPES = ["DDR1", "DDR2", "DDR3", "DDR4", "DDR5"]

# ---- Current error: required for these statuses. Operational = N/A (stored as NULL).
CURRENT_ERROR_STATUSES = ("Faulty", "Under Repair")

# ---- Warranty label window (used in app.py)
WARRANTY_SOON_DAYS = 30

# Columns of the assets table that a user can enter (active is handled separately)
FIELDS = ["asset_id", "manufacturer", "model", "cpu", "cpu_family", "ram_gb", "ram_type",
          "storage_type", "storage_gb", "gpu", "gpu_vram_gb", "os", "ip_address", "location",
          "purchase_date", "warranty_expiry", "status", "status_category", "status_detail",
          "status_action", "status_technician", "status_date", "status_cost"]
CURRENT_FAULT_FIELDS = ["status_category", "status_detail", "status_action", "status_technician", "status_date", "status_cost"]

# Asset list sorting: column key from the URL -> SQL expression. Anything else is ignored.
SORT_COLUMNS = {
    "asset_id": "a.asset_id", "model": "a.model", "cpu": "a.cpu", "ram": "a.ram_gb",
    "storage": "a.storage_gb", "gpu": "a.gpu", "location": "a.location", "status": "a.status",
    "warranty": "a.warranty_expiry",
    "faults": "(SELECT COUNT(*) FROM maintenance m WHERE m.asset_id=a.asset_id)",
}

# Fault history sorting: column key -> SQL expression.
FAULT_SORT_COLUMNS = {
    "date": "m.event_date",
    "asset_id": "m.asset_id",
    "model": "a.model",
    "location": "a.location",
    "status": "a.status",
    "category": "m.fault_category",
    "description": "m.fault_description",
    "action": "m.action_taken",
    "technician": "m.technician",
    "cost": "m.cost_usd",
}

EVENT_FIELDS = ["id", "asset_id", "event_date", "fault_category", "fault_description",
                "action_taken", "technician", "cost_usd"]


def attention_sql():
    """SQL fragment (for table alias a) implementing the 'needs attention' rule: current errors only."""
    return "(a.active=1 AND a.status IN ('Faulty','Under Repair'))"


# ---- Locations ----------------------------------------------------------------
def location_names():
    return [r[0] for r in _all("SELECT name FROM locations ORDER BY name")]


def find_location(name):
    """The existing location with this name (upper/lower case ignored), or None."""
    r = _one("SELECT name FROM locations WHERE LOWER(name)=LOWER(?)", (name,))
    return r[0] if r else None


def location_prefix(name):
    r = _one("SELECT prefix FROM locations WHERE name=?", (name,))
    return r[0] if r else None


def all_prefixes():
    return {r[0] for r in _all("SELECT prefix FROM locations")}


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
    for (aid,) in _all("SELECT asset_id FROM assets WHERE asset_id LIKE ? ESCAPE '\\'", (_like_escape(stem) + "%",)):
        tail = aid[len(stem):]
        if tail.isascii() and tail.isdigit():
            nums.append(int(tail))
    return f"{stem}{(max(nums) if nums else 0) + 1:03d}"


# ---- Assets: read -----------------------------------------------------------
def asset_exists(asset_id):
    return _one("SELECT 1 FROM assets WHERE asset_id=? COLLATE NOCASE", (asset_id,)) is not None


def get_asset(asset_id):
    return _one("SELECT * FROM assets WHERE asset_id=?", (asset_id,))


def _like_escape(text):
    """Make % _ and \\ in user text match literally inside a LIKE pattern (used with ESCAPE '\\')."""
    return str(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
        q += (" AND (a.asset_id LIKE ? ESCAPE '\\' OR a.model LIKE ? ESCAPE '\\' OR a.manufacturer LIKE ? ESCAPE '\\'"
              " OR a.cpu LIKE ? ESCAPE '\\' OR a.ip_address LIKE ? ESCAPE '\\')")
        p += [f"%{_like_escape(args['q'])}%"] * 5
    for key in ["cpu_family", "ram_gb", "ram_type", "storage_type", "location", "status"]:
        if args.get(key):
            q += f" AND a.{key}=?"
            p.append(args[key])
    w = args.get("warranty")
    if w == "Expired":
        q += " AND a.warranty_expiry < date('now','localtime')"
    elif w == "Active":
        q += " AND a.warranty_expiry >= date('now','localtime')"
    if args.get("sort") == "status":
        first = args.get("first") if args.get("first") in ("Operational", "Under Repair", "Faulty") else "Operational"
        q += " ORDER BY (CASE WHEN a.status=? THEN 0 ELSE 1 END), a.status, a.asset_id"
        p.append(first)
    else:
        col = SORT_COLUMNS.get(args.get("sort"), SORT_COLUMNS["asset_id"])
        direction = "DESC" if args.get("dir") == "desc" else "ASC"
        q += f" ORDER BY {col} {direction}, a.asset_id"   # column and direction come from fixed lists
    return _all(q, p)


def filter_options():
    """Values used to fill the filter drop-downs."""
    return {
        "cpu_family": [r[0] for r in _all("SELECT DISTINCT cpu_family FROM assets ORDER BY 1")],
        "ram_gb": [r[0] for r in _all("SELECT DISTINCT ram_gb FROM assets ORDER BY 1")],
        "location": location_names(),
    }


# ---- Assets: write ----------------------------------------------------------
def _add_step(db, asset_id, step_date, action, technician):
    """One step of an open fault. Not history: it is combined into the history entry when the fault closes.
    A step has no cost; the cost of the open fault is the single Updated Cost on the asset."""
    db.execute("INSERT INTO fault_steps (asset_id,step_date,action,technician) VALUES (?,?,?,?)",
               (asset_id, step_date, action or "", technician or ""))


def _close_fault(db, asset_id, ev):
    """Closing a fault writes ONE history entry. Its Action taken lists every step (date: action) and the
    final action; its cost is the single Updated Cost (ev['cost'], 0 allowed; None = keep the cost of the open
    fault). The steps are then removed.
    ev = event_date, fault_category, fault_description, action, technician, cost (all validated by the app)."""
    steps = db.execute("SELECT step_date, action FROM fault_steps WHERE asset_id=? ORDER BY id",
                       (asset_id,)).fetchall()
    parts = [f"{s['step_date']}: {s['action']}" for s in steps if s["action"]]
    final = (ev.get("action") or "").strip()
    if final and not (steps and steps[-1]["action"] == final):
        parts.append(f"{ev['event_date']}: {final}")
    cost_val = ev.get("cost")
    if cost_val is not None and str(cost_val).strip() != "":
        cost = float(cost_val)           # a typed cost is used as it is, 0 included
    else:                                # a blank cost keeps the Updated Cost of the open fault
        cost_row = db.execute("SELECT status_cost FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
        cost = float(cost_row[0]) if cost_row and cost_row[0] is not None else 0.0
    db.execute("INSERT INTO maintenance (asset_id,event_date,fault_category,fault_description,"
               "action_taken,technician,cost_usd) VALUES (?,?,?,?,?,?,?)",
               (asset_id, ev["event_date"], ev["fault_category"], ev["fault_description"],
                "; ".join(parts) or final, ev["technician"], cost))
    db.execute("DELETE FROM fault_steps WHERE asset_id=?", (asset_id,))


def open_fault_cost(asset_id):
    """Cost of the open fault of this PC: its single Updated Cost."""
    row = _one("SELECT status_cost FROM assets WHERE asset_id=?", (asset_id,))
    return float(row["status_cost"]) if row and row["status_cost"] is not None else 0.0


def get_steps(asset_id):
    return _all("SELECT * FROM fault_steps WHERE asset_id=? ORDER BY id", (asset_id,))


def insert_asset(c, step=None):
    """c = validated dict with every key in FIELDS. step = optional first step of a new open fault.
    May raise sqlite3.IntegrityError (the transaction is rolled back and the connection closed first)."""
    db = get_db()
    try:
        db.execute(f"INSERT INTO assets ({','.join(FIELDS)}, active) VALUES ({','.join('?' * len(FIELDS))}, 1)",
                   [c[k] for k in FIELDS])
        if step:
            _add_step(db, c["asset_id"], **step)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def update_asset(asset_id, c, step=None, close=None):
    """The primary key (asset_id) is never changed.
    step  = a step of the open fault (saved as a step, not as history);
    close = the closing details: the fault is written to history as one entry."""
    db = get_db()
    try:
        if close and close.get("cost") is None:       # blank cost: the closing entry keeps the open fault's cost
            prev = db.execute("SELECT status_cost FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
            close = dict(close, cost=float(prev[0] or 0) if prev else 0.0)   # read BEFORE the UPDATE clears it
        sets = ",".join(f"{k}=?" for k in FIELDS if k != "asset_id")
        db.execute(f"UPDATE assets SET {sets} WHERE asset_id=?",
                   [c[k] for k in FIELDS if k != "asset_id"] + [asset_id])
        if close:
            _close_fault(db, asset_id, close)
        if step:
            _add_step(db, asset_id, **step)
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
    return _all("SELECT * FROM maintenance WHERE asset_id=? ORDER BY event_date DESC, id DESC", (asset_id,))


def log_event(asset_id, f, cost, new_status, current=None):
    """Log form. Operational = close the fault (one history entry, all steps combined).
    Faulty / Under Repair = keep the fault open and update current fault fields + Updated Cost.
    A step is recorded if there is a new action.
    cost = the typed Updated Cost (0 allowed), or None when the box was blank (keep the current cost).
    f = event_date, fault_category, fault_description, action_taken, technician (validated by the app)."""
    db = get_db()
    try:
        if new_status == "Operational":
            _close_fault(db, asset_id, dict(f, action=f["action_taken"], cost=cost))
            db.execute("UPDATE assets SET status='Operational', status_category=NULL, status_detail=NULL, "
                       "status_action=NULL, status_technician=NULL, status_date=NULL, status_cost=NULL WHERE asset_id=?", (asset_id,))
        else:
            prev = db.execute("SELECT status_action, status_cost FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
            if cost is not None:
                cost_val = float(cost)
            else:
                cost_val = float(prev["status_cost"] or 0) if prev else 0.0
            db.execute("UPDATE assets SET status=?, status_category=?, status_detail=?, status_action=?, "
                       "status_technician=?, status_date=?, status_cost=? WHERE asset_id=?",
                       (new_status, current["category"], current["detail"], current["action"],
                        current["technician"], current["date"], cost_val, asset_id))
            new_work = current["action"] and current["action"] != ((prev["status_action"] if prev else "") or "")
            if new_work:
                _add_step(db, asset_id, f["event_date"], current["action"], current["technician"])
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def all_events():
    return _all("SELECT * FROM maintenance ORDER BY event_date DESC, id DESC")


def get_maintenance_event(event_id):
    """Retrieve a single maintenance event by its ID."""
    return _one("SELECT * FROM maintenance WHERE id=?", (event_id,))


def update_maintenance_event(event_id, event_date, fault_category, fault_description, action_taken, technician, cost_usd):
    """Update a maintenance event in the history."""
    db = get_db()
    try:
        db.execute("UPDATE maintenance SET event_date=?, fault_category=?, fault_description=?, "
                   "action_taken=?, technician=?, cost_usd=? WHERE id=?",
                   (event_date, fault_category, fault_description or "", action_taken or "",
                    technician or "", float(cost_usd or 0), event_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def delete_maintenance_event(event_id):
    """Delete a maintenance event by its ID."""
    db = get_db()
    try:
        db.execute("DELETE FROM maintenance WHERE id=?", (event_id,))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_step(step_id):
    """Retrieve a single fault step by ID."""
    return _one("SELECT * FROM fault_steps WHERE id=?", (step_id,))


def update_step(step_id, step_date, action, technician):
    """Update an open fault step (date, action, technician). Steps carry no cost."""
    db = get_db()
    try:
        db.execute("UPDATE fault_steps SET step_date=?, action=?, technician=? WHERE id=?",
                   (step_date, action or "", technician or "", step_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def delete_step(step_id):
    """Delete a single fault step by ID. The Updated Cost of the open fault is not touched."""
    db = get_db()
    try:
        db.execute("DELETE FROM fault_steps WHERE id=?", (step_id,))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def update_current_fault(asset_id, status_date, status_category, status_detail, status_action, status_technician, status_cost):
    """Update the current fault information directly on an asset."""
    db = get_db()
    try:
        db.execute("UPDATE assets SET status_date=?, status_category=?, status_detail=?, "
                   "status_action=?, status_technician=?, status_cost=? WHERE asset_id=?",
                   (status_date, status_category, status_detail or "", status_action or "",
                    status_technician or "", float(status_cost or 0), asset_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def fault_history(limit=None, sort="date", direction="desc", first=None):
    """Maintenance events across ALL assets, dynamically sorted like Asset lists table."""
    is_desc = str(direction).lower() == "desc"
    dir_sql = "DESC" if is_desc else "ASC"

    if sort == "status":
        first_st = first if first in ("Operational", "Under Repair", "Faulty") else "Operational"
        order_sql = f"(CASE WHEN a.status=? THEN 0 ELSE 1 END), a.status, a.asset_id ASC, m.event_date DESC"
        params = [first_st]
    else:
        col = FAULT_SORT_COLUMNS.get(sort, FAULT_SORT_COLUMNS["date"])
        order_sql = f"{col} {dir_sql}, m.id {dir_sql}"
        params = []

    sql = (f"SELECT m.*, a.model, a.location, a.status FROM maintenance m "
           f"JOIN assets a ON a.asset_id=m.asset_id ORDER BY {order_sql}")
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return _all(sql, params)


# ---- Dashboard --------------------------------------------------------------
def _pairs(sql):
    return [(r[0], r[1]) for r in _all(sql)]


def _bars(pairs):
    """[(label, count)] -> [(label, count, percent_of_largest)] for the bar charts."""
    pairs = list(pairs)
    biggest = max([n for _, n in pairs] + [1])
    return [(str(label), n, round(n * 100 / biggest)) for label, n in pairs]


def dashboard_data():
    """Everything the dashboard shows, all computed from the same tables with SQL.
    Charts count ACTIVE assets, so they always add up to stats['total']."""
    one = lambda sql: _one(sql)[0]
    stats = {
        "total": one("SELECT COUNT(*) FROM assets WHERE active=1"),
        "operational": one("SELECT COUNT(*) FROM assets WHERE active=1 AND status='Operational'"),
        "attention": one(f"SELECT COUNT(*) FROM assets a WHERE {attention_sql()}"),
        "retired": one("SELECT COUNT(*) FROM assets WHERE active=0"),
        "faulty": one("SELECT COUNT(*) FROM assets WHERE active=1 AND status='Faulty'"),
        "repair": one("SELECT COUNT(*) FROM assets WHERE active=1 AND status='Under Repair'"),
        "events": one("SELECT COUNT(*) FROM maintenance"),
        "out_of_warranty": one("SELECT COUNT(*) FROM assets WHERE active=1 AND warranty_expiry < date('now','localtime')"),
        "total_cost": one("SELECT COALESCE(SUM(cost_usd),0) FROM maintenance"),
    }

    in_warranty, out_warranty = _one(
        "SELECT COALESCE(SUM(CASE WHEN warranty_expiry >= date('now','localtime') THEN 1 ELSE 0 END),0), "
        "COALESCE(SUM(CASE WHEN warranty_expiry < date('now','localtime') THEN 1 ELSE 0 END),0) "
        "FROM assets WHERE active=1")

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

    top_assets = _all(
        "SELECT m.asset_id, a.model, COUNT(*) AS n, SUM(m.cost_usd) AS cost FROM maintenance m "
        "JOIN assets a ON a.asset_id=m.asset_id GROUP BY m.asset_id ORDER BY n DESC, cost DESC LIMIT 5")
    attention = _all(
        f"SELECT a.asset_id, a.model, a.location, a.status, a.status_category, a.status_detail "
        f"FROM assets a WHERE {attention_sql()} ORDER BY a.asset_id")
    return {"stats": stats, "charts": charts, "fault_cats": fault_cats,
            "top_assets": top_assets, "attention": attention}


# ---- IP addresses -------------------------------------------------------------
def location_subnet(name):
    """x of this place's range 192.168.x.n, or None for an unknown place."""
    r = _one("SELECT ip_subnet FROM locations WHERE name=?", (name,))
    return r[0] if r else None


def next_subnet():
    """Range number for the next new place: one more than the highest in use."""
    return _one("SELECT COALESCE(MAX(ip_subnet),0)+1 FROM locations")[0]


def ip_owner(location, ip, exclude_id=""):
    """The PC that already has this IP in this place, or None."""
    r = _one("SELECT asset_id FROM assets WHERE location=? AND ip_address=? AND asset_id<>?",
             (location, ip, exclude_id or ""))
    return r[0] if r else None
