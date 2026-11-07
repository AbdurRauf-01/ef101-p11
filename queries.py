"""DATABASE LAYER: every SQL statement in the project lives in this file.
app.py (backend) calls these functions and never writes SQL itself.
All values are passed as ? parameters (never pasted into the SQL text), so input cannot inject SQL.
"""
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
          "storage_type", "storage_gb", "gpu", "os", "ip_address", "location",
          "purchase_date", "warranty_expiry", "status", "status_category", "status_detail"]

EVENT_FIELDS = ["id", "asset_id", "event_date", "fault_category", "fault_description",
                "action_taken", "technician", "cost_usd"]


def attention_sql():
    """SQL fragment (for table alias a) implementing the 'needs attention' rule: current errors only."""
    return "(a.active=1 AND a.status IN ('Faulty','Under Repair'))"


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
    q += " ORDER BY a.asset_id"
    return get_db().execute(q, p).fetchall()


def filter_options():
    """Distinct values used to fill the filter drop-downs."""
    db = get_db()
    return {
        "cpu_family": [r[0] for r in db.execute("SELECT DISTINCT cpu_family FROM assets ORDER BY 1")],
        "ram_gb": [r[0] for r in db.execute("SELECT DISTINCT ram_gb FROM assets ORDER BY 1")],
        "location": [r[0] for r in db.execute("SELECT DISTINCT location FROM assets ORDER BY 1")],
    }


# ---- Assets: write ----------------------------------------------------------
def insert_asset(c):
    """c = validated dict with every key in FIELDS. May raise sqlite3.IntegrityError."""
    db = get_db()
    db.execute(f"INSERT INTO assets ({','.join(FIELDS)}, active) VALUES ({','.join('?' * len(FIELDS))}, 1)",
               [c[k] for k in FIELDS])
    db.commit()


def update_asset(asset_id, c):
    """The primary key (asset_id) is never changed."""
    db = get_db()
    sets = ",".join(f"{k}=?" for k in FIELDS if k != "asset_id")
    db.execute(f"UPDATE assets SET {sets} WHERE asset_id=?",
               [c[k] for k in FIELDS if k != "asset_id"] + [asset_id])
    db.commit()


def set_active(asset_id, active):
    """Retire (0) or reactivate (1). Rows are never deleted, so history is kept."""
    db = get_db()
    db.execute("UPDATE assets SET active=? WHERE asset_id=?", (active, asset_id))
    db.commit()


# ---- Maintenance ------------------------------------------------------------
def get_events(asset_id):
    return get_db().execute("SELECT * FROM maintenance WHERE asset_id=? ORDER BY event_date DESC, id DESC",
                            (asset_id,)).fetchall()


def add_event(asset_id, f, cost, set_status=None, status_category=None, status_detail=None):
    """Save one maintenance event; optionally change the asset's status and current error in the same commit."""
    db = get_db()
    db.execute("INSERT INTO maintenance (asset_id,event_date,fault_category,fault_description,"
               "action_taken,technician,cost_usd) VALUES (?,?,?,?,?,?,?)",
               (asset_id, f["event_date"], f["fault_category"], f["fault_description"],
                f["action_taken"], f["technician"], cost))
    if set_status:
        db.execute("UPDATE assets SET status=?, status_category=?, status_detail=? WHERE asset_id=?",
                   (set_status, status_category, status_detail, asset_id))
    db.commit()


def all_events():
    return get_db().execute("SELECT * FROM maintenance ORDER BY event_date DESC, id DESC").fetchall()


def fault_history(limit=None):
    """Maintenance events across ALL assets, newest first, whatever their current status."""
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
        "Assets per location": _bars(_pairs(
            "SELECT location, COUNT(*) AS n FROM assets WHERE active=1 GROUP BY location ORDER BY n DESC")),
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
