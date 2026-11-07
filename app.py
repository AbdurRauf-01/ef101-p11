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
                   send_from_directory, url_for)

import queries
from db import BASE, DB_PATH
from queries import CURRENT_ERROR_STATUSES, FIELDS, WARRANTY_SOON_DAYS

app = Flask(__name__)
app.secret_key = "ef101-demo-key"  # demo only; no real secrets in this project


@app.template_filter("money")
def money(value):
    """Format a cost in US dollars, e.g. 1234.5 -> $1,234.50."""
    return f"${float(value or 0):,.2f}"

# The "needs attention" rule, CURRENT_ERROR_STATUSES and FIELDS live in queries.py,
# together with all the SQL.
# Allowed values below must match the CHECK constraints in schema.sql.
STATUSES = ["Operational", "Faulty", "Under Repair"]
STORAGE_TYPES = ["SSD", "NVMe", "HDD", "Hybrid"]
# Shown whenever Faulty / Under Repair is set (on the edit form or in a logged event).
NEED_REASON = ("Current error is required for Faulty or Under Repair. "
               "Describe the fault, or type Unknown if the cause is not known.")


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
    if c["status"] == "Operational":
        c["status_reason"] = None          # a working asset has no current error (shown as N/A)
    elif c["status"] in CURRENT_ERROR_STATUSES and not c["status_reason"]:
        errors.append(NEED_REASON)
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
        if queries.asset_exists(c["asset_id"]):
            errors.append(f"Asset ID '{c['asset_id']}' already exists.")
    return c, errors


@app.route("/")
def home():
    return redirect(url_for("dashboard"))


@app.route("/assets")
def assets():
    rows = queries.filtered_assets(request.args)
    opts = queries.filter_options()
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
            queries.insert_asset(c)
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
    a = queries.get_asset(asset_id)
    if not a:
        return render_template("message.html", title="Asset not found",
                               text=f"No asset with ID '{asset_id}'."), 404
    events = queries.get_events(asset_id)
    total_cost = sum(e["cost"] for e in events)
    return render_template("asset_detail.html", a=a, events=events, total_cost=total_cost,
                           warranty=warranty_status(a["warranty_expiry"]), today=today())


@app.route("/assets/<asset_id>/edit", methods=["GET", "POST"])
def asset_edit(asset_id):
    a = queries.get_asset(asset_id)
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
        queries.update_asset(asset_id, c)
        flash("Asset updated.", "success")
        return redirect(url_for("asset_detail", asset_id=asset_id))
    # the form template uses a.get(...), which a sqlite3.Row does not have: convert to a
    # dict and show empty (NULL) fields as blank instead of the text "None"
    a = {k: ("" if v is None else v) for k, v in dict(a).items()}
    return render_template("asset_form.html", a=a, new=False,
                           storage_types=STORAGE_TYPES, statuses=STATUSES)


@app.route("/assets/<asset_id>/retire", methods=["POST"])
def asset_retire(asset_id):
    queries.set_active(asset_id, 0)
    flash(f"{asset_id} retired. History is kept.", "success")
    return redirect(url_for("asset_detail", asset_id=asset_id))


@app.route("/assets/<asset_id>/reactivate", methods=["POST"])
def asset_reactivate(asset_id):
    queries.set_active(asset_id, 1)
    flash(f"{asset_id} reactivated.", "success")
    return redirect(url_for("asset_detail", asset_id=asset_id))


@app.route("/assets/<asset_id>/maintenance", methods=["POST"])
def maintenance_add(asset_id):
    if not queries.asset_exists(asset_id):
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
    new_status = request.form.get("set_status")
    if new_status not in STATUSES:
        new_status = None
    reason = (request.form.get("status_reason") or "").strip()
    if new_status in CURRENT_ERROR_STATUSES and not reason:
        errors.append(NEED_REASON)
    if errors:
        for e in errors:
            flash(e, "danger")
        return redirect(url_for("asset_detail", asset_id=asset_id))
    queries.add_event(asset_id, f, cost, new_status,
                      reason if new_status in CURRENT_ERROR_STATUSES else None)
    flash("Maintenance event logged.", "success")
    return redirect(url_for("asset_detail", asset_id=asset_id))


# ---- Dashboard ----------------------------------------------------------------
@app.route("/dashboard")
def dashboard():
    d = queries.dashboard_data()
    return render_template("dashboard.html", stats=d["stats"], charts=d["charts"],
                           fault_cats=d["fault_cats"], top_assets=d["top_assets"],
                           attention=d["attention"])


# ---- Maintenance and About pages -------------------------------------------------
@app.route("/maintenance")
def maintenance():
    events = queries.fault_history()
    return render_template("maintenance.html", events=events,
                           total_cost=sum(e["cost"] for e in events))


@app.route("/about/credits")
def about_credits():
    return render_template("about_credits.html")


@app.route("/about/privacy")
def about_privacy():
    return render_template("about_privacy.html")


@app.route("/about/schema")
def about_schema():
    return render_template("about_schema.html",
                           schema_text=(BASE / "schema.sql").read_text(encoding="utf-8"))


@app.route("/about/schema.sql")
def schema_download():
    return send_from_directory(str(BASE), "schema.sql", as_attachment=True)


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
    rows = queries.filtered_assets(request.args)  # exports whatever the current filters show
    header = FIELDS + ["active", "warranty_status", "fault_count"]
    out = [[r[k] for k in FIELDS] + [r["active"], warranty_status(r["warranty_expiry"]), r["fault_count"]]
           for r in rows]
    return csv_response(header, out, "assets.csv")


@app.route("/export/maintenance.csv")
def export_maintenance():
    rows = queries.all_events()
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
