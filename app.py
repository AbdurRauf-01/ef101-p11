"""EF101-P11 Computer Hardware Inventory and Maintenance System.
Run: python seed.py   (once, creates demo data)
     python app.py    (then open http://127.0.0.1:5000)
"""
import csv
import io
import ipaddress
import sqlite3
from datetime import date, datetime, timedelta

from flask import (Flask, Response, flash, redirect, render_template, request,
                   url_for)

from db import DB_PATH, get_db

app = Flask(__name__)
app.secret_key = "ef101-demo-key"  # demo only; no real secrets in this project

# ---- Configurable rules (documented in the report) -------------------------
# "Needs attention" = active asset AND ANY of:
#   1. status is Faulty or Under Repair
#   2. it has >= ATTENTION_FAULT_COUNT maintenance events
#   3. warranty expires within WARRANTY_SOON_DAYS days (not yet expired)
ATTENTION_FAULT_COUNT = 3
WARRANTY_SOON_DAYS = 30

STATUSES = ["Operational", "Faulty", "Under Repair"]
STORAGE_TYPES = ["SSD", "NVMe", "HDD", "Hybrid"]
FIELDS = ["asset_id", "manufacturer", "model", "cpu", "cpu_family", "ram_gb",
          "storage_type", "storage_gb", "gpu", "os", "ip_address", "location",
          "purchase_date", "warranty_expiry", "status"]


def today():
    return date.today().isoformat()


def warranty_status(expiry):
    t = date.today()
    exp = date.fromisoformat(expiry)
    if exp < t:
        return "Expired"
    if exp <= t + timedelta(days=WARRANTY_SOON_DAYS):
        return "Expiring soon"
    return "Active"


def parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def validate_asset(f, is_new, existing_id=None):
    """Server-side validation. Returns (clean_dict, errors_list)."""
    errors = []
    c = {k: (f.get(k) or "").strip() for k in FIELDS}
    for k in ["asset_id", "manufacturer", "model", "cpu", "cpu_family", "os", "location"]:
        if not c[k]:
            errors.append(f"{k.replace('_', ' ').title()} is required.")
    for k in ["ram_gb", "storage_gb"]:
        try:
            c[k] = int(c[k])
            if c[k] <= 0:
                raise ValueError
        except ValueError:
            errors.append(f"{k.replace('_', ' ').upper()} must be a positive whole number.")
    if c["storage_type"] not in STORAGE_TYPES:
        errors.append("Choose a valid storage type.")
    if c["status"] not in STATUSES:
        errors.append("Choose a valid status.")
    if c["ip_address"]:
        try:
            ipaddress.ip_address(c["ip_address"])
        except ValueError:
            errors.append("IP address is not valid (example: 192.168.1.20).")
    p, w = parse_date(c["purchase_date"]), parse_date(c["warranty_expiry"])
    if not p:
        errors.append("Purchase date must be a valid date (YYYY-MM-DD).")
    elif p > date.today():
        errors.append("Purchase date cannot be in the future.")
    if not w:
        errors.append("Warranty expiry must be a valid date (YYYY-MM-DD).")
    if p and w and w < p:
        errors.append("Warranty expiry cannot be before the purchase date.")
    if is_new and c["asset_id"]:
        row = get_db().execute("SELECT 1 FROM assets WHERE asset_id=?", (c["asset_id"],)).fetchone()
        if row:
            errors.append(f"Asset ID '{c['asset_id']}' already exists.")
    return c, errors


def attention_sql():
    """SQL fragment (for table alias a) implementing the 'needs attention' rule."""
    return (f"(a.active=1 AND (a.status IN ('Faulty','Under Repair') "
            f"OR (SELECT COUNT(*) FROM maintenance m WHERE m.asset_id=a.asset_id) >= {int(ATTENTION_FAULT_COUNT)} "
            f"OR (a.warranty_expiry >= date('now') AND a.warranty_expiry <= date('now','+{int(WARRANTY_SOON_DAYS)} days'))))")


# ---- Asset list with filters ------------------------------------------------
def filtered_assets(args):
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


@app.route("/")
def home():
    return redirect(url_for("dashboard"))


@app.route("/assets")
def assets():
    rows = filtered_assets(request.args)
    db = get_db()
    opts = {
        "cpu_family": [r[0] for r in db.execute("SELECT DISTINCT cpu_family FROM assets ORDER BY 1")],
        "ram_gb": [r[0] for r in db.execute("SELECT DISTINCT ram_gb FROM assets ORDER BY 1")],
        "location": [r[0] for r in db.execute("SELECT DISTINCT location FROM assets ORDER BY 1")],
    }
    enriched = [dict(r, warranty=warranty_status(r["warranty_expiry"])) for r in rows]
    return render_template("assets.html", assets=enriched, opts=opts, f=request.args,
                           storage_types=STORAGE_TYPES, statuses=STATUSES)


@app.route("/assets/new", methods=["GET", "POST"])
def asset_new():
    if request.method == "POST":
        c, errors = validate_asset(request.form, is_new=True)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("asset_form.html", a=request.form, new=True,
                                   storage_types=STORAGE_TYPES, statuses=STATUSES)
        try:
            db = get_db()
            db.execute(f"INSERT INTO assets ({','.join(FIELDS)}, active) VALUES ({','.join('?' * len(FIELDS))}, 1)",
                       [c[k] for k in FIELDS])
            db.commit()
        except sqlite3.IntegrityError as e:
            flash(f"Database rejected the record: {e}", "danger")
            return render_template("asset_form.html", a=request.form, new=True,
                                   storage_types=STORAGE_TYPES, statuses=STATUSES)
        flash(f"Asset {c['asset_id']} added.", "success")
        return redirect(url_for("asset_detail", asset_id=c["asset_id"]))
    return render_template("asset_form.html", a={}, new=True,
                           storage_types=STORAGE_TYPES, statuses=STATUSES)


@app.route("/assets/<asset_id>")
def asset_detail(asset_id):
    db = get_db()
    a = db.execute("SELECT * FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
    if not a:
        return render_template("message.html", title="Asset not found",
                               text=f"No asset with ID '{asset_id}'."), 404
    events = db.execute("SELECT * FROM maintenance WHERE asset_id=? ORDER BY event_date DESC, id DESC",
                        (asset_id,)).fetchall()
    total_cost = sum(e["cost"] for e in events)
    return render_template("asset_detail.html", a=a, events=events, total_cost=total_cost,
                           warranty=warranty_status(a["warranty_expiry"]), today=today())


@app.route("/assets/<asset_id>/edit", methods=["GET", "POST"])
def asset_edit(asset_id):
    db = get_db()
    a = db.execute("SELECT * FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
    if not a:
        return render_template("message.html", title="Asset not found", text=asset_id), 404
    if request.method == "POST":
        form = dict(request.form)
        form["asset_id"] = asset_id  # primary key is never changed
        c, errors = validate_asset(form, is_new=False)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("asset_form.html", a=form, new=False,
                                   storage_types=STORAGE_TYPES, statuses=STATUSES)
        sets = ",".join(f"{k}=?" for k in FIELDS if k != "asset_id")
        db.execute(f"UPDATE assets SET {sets} WHERE asset_id=?",
                   [c[k] for k in FIELDS if k != "asset_id"] + [asset_id])
        db.commit()
        flash("Asset updated.", "success")
        return redirect(url_for("asset_detail", asset_id=asset_id))
    return render_template("asset_form.html", a=a, new=False,
                           storage_types=STORAGE_TYPES, statuses=STATUSES)


@app.route("/assets/<asset_id>/retire", methods=["POST"])
def asset_retire(asset_id):
    db = get_db()
    db.execute("UPDATE assets SET active=0 WHERE asset_id=?", (asset_id,))
    db.commit()
    flash(f"{asset_id} retired. History is kept.", "success")
    return redirect(url_for("asset_detail", asset_id=asset_id))


@app.route("/assets/<asset_id>/reactivate", methods=["POST"])
def asset_reactivate(asset_id):
    db = get_db()
    db.execute("UPDATE assets SET active=1 WHERE asset_id=?", (asset_id,))
    db.commit()
    flash(f"{asset_id} reactivated.", "success")
    return redirect(url_for("asset_detail", asset_id=asset_id))


@app.route("/assets/<asset_id>/maintenance", methods=["POST"])
def maintenance_add(asset_id):
    db = get_db()
    if not db.execute("SELECT 1 FROM assets WHERE asset_id=?", (asset_id,)).fetchone():
        return render_template("message.html", title="Asset not found", text=asset_id), 404
    f = {k: (request.form.get(k) or "").strip() for k in
         ["event_date", "fault_category", "fault_description", "action_taken", "technician", "cost"]}
    errors = []
    d = parse_date(f["event_date"])
    if not d:
        errors.append("Event date must be a valid date (YYYY-MM-DD).")
    elif d > date.today():
        errors.append("Event date cannot be in the future.")
    for k in ["fault_category", "fault_description", "action_taken", "technician"]:
        if not f[k]:
            errors.append(f"{k.replace('_', ' ').title()} is required.")
    try:
        cost = float(f["cost"] or 0)
        if cost < 0:
            raise ValueError
    except ValueError:
        errors.append("Cost must be a number, zero or more.")
        cost = 0
    if errors:
        for e in errors:
            flash(e, "danger")
        return redirect(url_for("asset_detail", asset_id=asset_id))
    db.execute("INSERT INTO maintenance (asset_id,event_date,fault_category,fault_description,"
               "action_taken,technician,cost) VALUES (?,?,?,?,?,?,?)",
               (asset_id, f["event_date"], f["fault_category"], f["fault_description"],
                f["action_taken"], f["technician"], cost))
    if request.form.get("set_status") in STATUSES:
        db.execute("UPDATE assets SET status=? WHERE asset_id=?", (request.form["set_status"], asset_id))
    db.commit()
    flash("Maintenance event logged.", "success")
    return redirect(url_for("asset_detail", asset_id=asset_id))


# ---- Dashboard ----------------------------------------------------------------
@app.route("/dashboard")
def dashboard():
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
    return render_template("dashboard.html", stats=stats, charts=charts, fault_cats=fault_cats,
                           top_assets=top_assets, attention=attention,
                           rule_count=ATTENTION_FAULT_COUNT, rule_days=WARRANTY_SOON_DAYS)


# ---- Exports -------------------------------------------------------------------
def csv_response(header, rows, filename):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(list(r))
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.route("/export/assets.csv")
def export_assets():
    rows = filtered_assets(request.args)  # exports whatever the current filters show
    header = FIELDS + ["active", "warranty_status", "fault_count"]
    out = [[r[k] for k in FIELDS] + [r["active"], warranty_status(r["warranty_expiry"]), r["fault_count"]]
           for r in rows]
    return csv_response(header, out, "assets.csv")


@app.route("/export/maintenance.csv")
def export_maintenance():
    rows = get_db().execute("SELECT * FROM maintenance ORDER BY event_date DESC, id DESC").fetchall()
    return csv_response(rows[0].keys() if rows else
                        ["id", "asset_id", "event_date", "fault_category", "fault_description",
                         "action_taken", "technician", "cost"],
                        rows, "maintenance.csv")


@app.errorhandler(404)
def not_found(e):
    return render_template("message.html", title="Page not found", text="That page does not exist."), 404


@app.errorhandler(sqlite3.OperationalError)
def db_missing(e):
    return render_template("message.html", title="Database problem",
                           text=f"{e}. If inventory.db is missing, run: python seed.py"), 500


if __name__ == "__main__":
    if not DB_PATH.exists():
        print("inventory.db not found - run 'python seed.py' first.")
    app.run(debug=True)
