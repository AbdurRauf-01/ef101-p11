"""DATABASE LAYER: every SQL statement in the project lives in this file.
app.py (backend) calls these functions and never writes SQL itself.
All values are passed as ? parameters (never pasted into the SQL text), so input cannot inject SQL.
"""
from db import get_db

# ---- Configurable "needs attention" rule (documented in the report) ---------
# Needs attention = active asset AND ANY of:
#   1. status is Faulty or Under Repair
#   2. it has >= ATTENTION_FAULT_COUNT maintenance events
#   3. warranty expires within WARRANTY_SOON_DAYS days (not yet expired)
ATTENTION_FAULT_COUNT = 3
WARRANTY_SOON_DAYS = 30

# Columns of the assets table that a user can enter (active is handled separately)
FIELDS = ["asset_id", "manufacturer", "model", "cpu", "cpu_family", "ram_gb",
          "storage_type", "storage_gb", "gpu", "os", "ip_address", "location",
          "purchase_date", "warranty_expiry", "status"]

EVENT_FIELDS = ["id", "asset_id", "event_date", "fault_category", "fault_description",
                "action_taken", "technician", "cost"]


def attention_sql():
    """SQL fragment (for table alias a) implementing the 'needs attention' rule."""
    return (f"(a.active=1 AND (a.status IN ('Faulty','Under Repair') "
            f"OR (SELECT COUNT(*) FROM maintenance m WHERE m.asset_id=a.asset_id) >= {int(ATTENTION_FAULT_COUNT)} "
            f"OR (a.warranty_expiry >= date('now') AND a.warranty_expiry <= date('now','+{int(WARRANTY_SOON_DAYS)} days'))))")


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
    for key in ["cpu_family", "ram_gb", "storage_type", "location", "status"]:
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


def add_event(asset_id, f, cost, set_status=None):
    """Save one maintenance event; optionally change the asset's status in the same commit."""
    db = get_db()
    db.execute("INSERT INTO maintenance (asset_id,event_date,fault_category,fault_description,"
               "action_taken,technician,cost) VALUES (?,?,?,?,?,?,?)",
               (asset_id, f["event_date"], f["fault_category"], f["fault_description"],
                f["action_taken"], f["technician"], cost))
    if set_status:
        db.execute("UPDATE assets SET status=? WHERE asset_id=?", (set_status, asset_id))
    db.commit()


def all_events():
    return get_db().execute("SELECT * FROM maintenance ORDER BY event_date DESC, id DESC").fetchall()


# ---- Dashboard --------------------------------------------------------------
def dashboard_data():
    """Everything the dashboard shows, computed with SQL (COUNT, GROUP BY, JOIN)."""
    db = get_db()
    one = lambda sql: db.execute(sql).fetchone()[0]
    stats = {
        "total": one("SELECT COUNT(*) FROM assets WHERE active=1"),
        "operational": one("SELECT COUNT(*) FROM assets WHERE active=1 AND status='Operational'"),
        "faulty": one("SELECT COUNT(*) FROM assets WHERE active=1 AND status='Faulty'"),
        "repair": one("SELECT COUNT(*) FROM assets WHERE active=1 AND status='Under Repair'"),
        "out_of_warranty": one("SELECT COUNT(*) FROM assets WHERE active=1 AND warranty_expiry < date('now')"),
        "attention": one(f"SELECT COUNT(*) FROM assets a WHERE {attention_sql()}"),
        "retired": one("SELECT COUNT(*) FROM assets WHERE active=0"),
        "total_cost": one("SELECT COALESCE(SUM(cost),0) FROM maintenance"),
    }

    def dist(col):
        rows = db.execute(f"SELECT {col} AS label, COUNT(*) AS n FROM assets WHERE active=1 "
                          f"GROUP BY {col} ORDER BY n DESC").fetchall()
        mx = max([r["n"] for r in rows] or [1])
        return [(str(r["label"]), r["n"], round(r["n"] * 100 / mx)) for r in rows]

    charts = {
        "RAM (GB)": [(f"{l} GB", n, w) for l, n, w in dist("ram_gb")],
        "Storage type": dist("storage_type"),
        "CPU family": dist("cpu_family"),
        "Assets per location": dist("location"),
    }
    cat_rows = db.execute("SELECT fault_category AS label, COUNT(*) AS n FROM maintenance "
                          "GROUP BY fault_category ORDER BY n DESC").fetchall()
    mx = max([r["n"] for r in cat_rows] or [1])
    fault_cats = [(r["label"], r["n"], round(r["n"] * 100 / mx)) for r in cat_rows]
    top_assets = db.execute(
        "SELECT m.asset_id, a.model, COUNT(*) AS n, SUM(m.cost) AS cost FROM maintenance m "
        "JOIN assets a ON a.asset_id=m.asset_id GROUP BY m.asset_id ORDER BY n DESC, cost DESC LIMIT 5"
    ).fetchall()
    attention = db.execute(
        f"SELECT a.asset_id, a.model, a.location, a.status, a.warranty_expiry, "
        f"(SELECT COUNT(*) FROM maintenance m WHERE m.asset_id=a.asset_id) AS fault_count "
        f"FROM assets a WHERE {attention_sql()} ORDER BY fault_count DESC").fetchall()
    return {"stats": stats, "charts": charts, "fault_cats": fault_cats,
            "top_assets": top_assets, "attention": attention}
