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
from queries import (CURRENT_ERROR_CATEGORIES, CURRENT_ERROR_STATUSES, FAULT_CATEGORIES,
                     FIELDS, RAM_TYPES, WARRANTY_SOON_DAYS)

app = Flask(__name__)
GITHUB_URL = "https://github.com/AbdurRauf-01/ef101-fall2026-p11"
app.secret_key = "ef101-demo-key"  # demo only; no real secrets in this project

# Allowed values below must match the CHECK constraints in schema.sql.
STATUSES = ["Operational", "Faulty", "Under Repair"]
STORAGE_TYPES = ["SSD", "NVMe", "HDD", "Hybrid"]
NEW_LOCATION = "__new__"    # drop-down value meaning "+ Add new location"
NEED_CATEGORY = ("Choose the current error category for Faulty or Under Repair "
                 "(use N/A, Unknown or Other if needed).")
NEED_DETAIL = ("Describe the current error for Faulty or Under Repair. "
               "Type Unknown if the cause is not known.")


@app.context_processor
def option_lists():
    """Drop-down choices, available to every template."""
    return dict(storage_types=STORAGE_TYPES, statuses=STATUSES, ram_types=RAM_TYPES,
                fault_categories=FAULT_CATEGORIES, error_categories=CURRENT_ERROR_CATEGORIES,
                locations=queries.location_names(), NEW_LOCATION=NEW_LOCATION)


@app.template_filter("money")
def money(value):
    """Format a cost in US dollars, e.g. 1234.5 -> $1,234.50."""
    return f"${float(value or 0):,.2f}"


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


def current_error_problems(category, detail):
    """Faulty / Under Repair need a category from the list AND a written detail (may be 'Unknown')."""
    problems = []
    if category not in CURRENT_ERROR_CATEGORIES:
        problems.append(NEED_CATEGORY)
    if not detail:
        problems.append(NEED_DETAIL)
    return problems


def resolve_location(form):
    """Returns (place name, is_new). Choosing '+ Add new location' uses the typed name, unless a
    place with the same name (any upper/lower case) already exists, in which case that one is used."""
    chosen = (form.get("location") or "").strip()
    if chosen != NEW_LOCATION:
        return chosen, False
    typed = (form.get("new_location") or "").strip()
    existing = queries.find_location(typed) if typed else None
    if existing:
        return existing, False
    return typed, True


def existing_or_new_prefix(name, is_new):
    """Short code used for the asset ID of this place."""
    if is_new:
        return queries.suggest_prefix(name, queries.all_prefixes())
    return queries.location_prefix(name)


def validate_asset(f, is_new, subnet):
    """Server-side validation. Returns (clean_dict, errors_list).
    subnet = x of the chosen place's range 192.168.x.n, used when the IP box is left blank."""
    errors = []
    c = {k: (f.get(k) or "").strip() for k in FIELDS}
    for k in ["manufacturer", "model", "cpu", "cpu_family", "os", "location"]:
        if not c[k]:
            errors.append(f"{k.replace('_', ' ').title()} is required.")
    if not c["asset_id"]:
        errors.append("Choose a location so the asset ID can be assigned automatically, or type an ID.")
    for k in ["ram_gb", "storage_gb"]:
        try:
            c[k] = int(c[k])
            if c[k] <= 0:
                raise ValueError
        except ValueError:
            errors.append(f"{k.replace('_', ' ').upper()} must be a positive whole number.")
    if c["gpu"]:
        try:
            c["gpu_vram_gb"] = int(c["gpu_vram_gb"])
            if c["gpu_vram_gb"] <= 0:
                raise ValueError
        except ValueError:
            errors.append("GPU VRAM (GB) must be a positive whole number when a GPU is listed.")
    else:
        c["gpu_vram_gb"] = None          # integrated graphics: no VRAM value
    if c["ram_type"] not in RAM_TYPES:
        errors.append("Choose a RAM type (DDR1 to DDR5).")
    if c["storage_type"] not in STORAGE_TYPES:
        errors.append("Choose a valid storage type.")
    if c["status"] not in STATUSES:
        errors.append("Choose a valid status.")
    if c["status"] == "Operational":
        c["status_category"] = c["status_detail"] = None   # a working asset has no current error (N/A)
    elif c["status"] in CURRENT_ERROR_STATUSES:
        errors += current_error_problems(c["status_category"], c["status_detail"])

    # IP address: blank = assigned automatically. Two PCs in the same place never share an IP.
    loc, aid = c["location"], c["asset_id"]
    skip_self = "" if is_new else aid           # when editing, a PC does not conflict with itself
    if c["ip_address"]:
        try:
            ipaddress.IPv4Address(c["ip_address"])
        except ValueError:
            errors.append("IP address is not valid (example: 192.168.1.20).")
        else:
            owner = queries.ip_owner(loc, c["ip_address"], skip_self) if loc else None
            if owner:
                errors.append(f"IP {c['ip_address']} is already used by {owner} in {loc}. "
                              "Each PC in one place needs its own IP: clear the IP box to assign one automatically.")
    elif loc and aid and subnet:
        c["ip_address"] = queries.auto_ip(subnet, aid, loc, skip_self) or ""
        if not c["ip_address"]:
            errors.append(f"No free IP address is left in the range for {loc}.")
    c["ip_address"] = c["ip_address"] or None

    p, w = parse_date(c["purchase_date"]), parse_date(c["warranty_expiry"])
    if not p:
        errors.append("Purchase date must be a valid date (YYYY-MM-DD).")
    elif p > date.today():
        errors.append("Purchase date cannot be in the future.")
    if not w:
        errors.append("Warranty expiry must be a valid date (YYYY-MM-DD).")
    if p and w and w < p:
        errors.append("Warranty expiry cannot be before the purchase date.")
    if is_new and c["asset_id"] and queries.asset_exists(c["asset_id"]):
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
    return render_template("assets.html", assets=enriched, opts=opts, f=request.args)


@app.route("/assets/new", methods=["GET", "POST"])
def asset_new():
    if request.method == "POST":
        form = dict(request.form)
        name, is_new_loc = resolve_location(request.form)
        form["location"] = name
        subnet = queries.next_subnet() if is_new_loc else queries.location_subnet(name)
        errors = []
        if is_new_loc and not name:
            errors.append("Type the name of the new location (for example Lab 4).")
        if name and not (form.get("asset_id") or "").strip():
            prefix = existing_or_new_prefix(name, is_new_loc)
            if prefix:
                form["asset_id"] = queries.next_asset_id(prefix)   # automatic ID
        c, more = validate_asset(form, True, subnet)
        errors += more
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("asset_form.html", a=form, new=True)
        try:
            if is_new_loc:
                queries.add_location(name, existing_or_new_prefix(name, True), subnet)
            queries.insert_asset(c)
        except sqlite3.IntegrityError as e:
            flash(f"Database rejected the record: {e}", "danger")
            return render_template("asset_form.html", a=form, new=True)
        flash(f"Asset {c['asset_id']} added (IP {c['ip_address']}).", "success")
        return redirect(url_for("asset_detail", asset_id=c["asset_id"]))
    return render_template("asset_form.html", a={}, new=True)


@app.route("/assets/<asset_id>")
def asset_detail(asset_id):
    a = queries.get_asset(asset_id)
    if not a:
        return render_template("message.html", title="Asset not found",
                               text=f"No asset with ID '{asset_id}'."), 404
    events = queries.get_events(asset_id)
    total_cost = sum(e["cost_usd"] for e in events)
    return render_template("asset_detail.html", a=a, events=events, total_cost=total_cost,
                           warranty=warranty_status(a["warranty_expiry"]), today=today())


@app.route("/assets/<asset_id>/edit", methods=["GET", "POST"])
def asset_edit(asset_id):
    a = queries.get_asset(asset_id)
    if not a:
        return render_template("message.html", title="Asset not found", text=asset_id), 404
    if request.method == "POST":
        form = dict(request.form)
        form["asset_id"] = asset_id                       # primary key is never changed
        name, is_new_loc = resolve_location(request.form)
        form["location"] = name
        subnet = queries.next_subnet() if is_new_loc else queries.location_subnet(name)
        errors = []
        if is_new_loc and not name:
            errors.append("Type the name of the new location (for example Lab 4).")
        c, more = validate_asset(form, False, subnet)
        errors += more
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("asset_form.html", a=form, new=False)
        if is_new_loc:
            queries.add_location(name, existing_or_new_prefix(name, True), subnet)
        queries.update_asset(asset_id, c)
        flash("Asset updated.", "success")
        return redirect(url_for("asset_detail", asset_id=asset_id))
    # the form template uses a.get(...), which a sqlite3.Row does not have: convert to a
    # dict and show empty (NULL) fields as blank instead of the text "None"
    a = {k: ("" if v is None else v) for k, v in dict(a).items()}
    return render_template("asset_form.html", a=a, new=False)


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
    if f["fault_category"] not in FAULT_CATEGORIES:
        errors.append("Choose a fault category from the list.")
    for k in ["fault_description", "action_taken", "technician"]:
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
        new_status = None                       # blank = no status change
    cat = (request.form.get("status_category") or "").strip()
    detail = (request.form.get("status_detail") or "").strip()
    if new_status in CURRENT_ERROR_STATUSES:
        errors += current_error_problems(cat, detail)
    if errors:
        for e in errors:
            flash(e, "danger")
        return redirect(url_for("asset_detail", asset_id=asset_id))
    current = new_status in CURRENT_ERROR_STATUSES
    queries.add_event(asset_id, f, cost, new_status,
                      cat if current else None, detail if current else None)
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
                           total_cost=sum(e["cost_usd"] for e in events))


@app.route("/about/credits")
def about_credits():
    return render_template("about_credits.html")


@app.route("/about/privacy")
def about_privacy():
    return render_template("about_privacy.html")


@app.route("/about/contact")
def about_contact():
    return render_template("about_contact.html")


@app.route("/about/schema")
def about_schema():
    return render_template("about_schema.html")


@app.route("/about/schema.svg")
def schema_image():
    download = request.args.get("download") == "1"
    return send_from_directory(str(BASE / "docs"), "project-schema.svg", as_attachment=download)


@app.route("/github")
def github():
    return redirect(GITHUB_URL)


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
    return csv_response(rows[0].keys() if rows else queries.EVENT_FIELDS, rows, "maintenance.csv")


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
