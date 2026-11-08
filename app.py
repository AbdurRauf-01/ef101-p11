"""EF101-P11 Computer Hardware Inventory and Maintenance System.
Run: python app.py   (or double-click start.command / start.bat). Data is kept in inventory.db in this folder.
Optional demo data: python seed.py
"""
import argparse
import csv
import io
import ipaddress
import math
import os
import re
import socket
import sqlite3
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

from flask import (Flask, Response, flash, redirect, render_template, request,
                   send_from_directory, url_for)

import queries
from db import BASE, DB_PATH, ensure_database, schema_problem
from queries import (CURRENT_ERROR_STATUSES, CURRENT_FAULT_FIELDS, FAULT_CATEGORIES, FIELDS,
                     RAM_TYPES, SORT_COLUMNS, WARRANTY_SOON_DAYS)

app = Flask(__name__)
GITHUB_URL = "https://github.com/AbdurRauf-01/ef101-p11"
# The key only signs the flash messages. A fresh random key per start is fine (messages are one-request only).
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(24).hex()
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024        # no form in this app is anywhere near 64 KB

MAX_FIELD = 500                                      # longest text any single form box may hold
MAX_COST = 1_000_000_000
ASSET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
LOCATION_MAX = 40
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]", "testserver"}

# Allowed values below must match the CHECK constraints in schema.sql.
STATUSES = ["Operational", "Faulty", "Under Repair"]
STORAGE_TYPES = ["SSD", "NVMe", "HDD", "Hybrid"]
STATUS_ORDER = ["Operational", "Under Repair", "Faulty"]   # clicks on the Status heading cycle through these
NEW_LOCATION = "__new__"    # drop-down value meaning "+ Add new location"
ASSET_COLUMNS = [("asset_id", "Asset ID"), ("model", "Model"), ("cpu", "CPU"), ("ram", "RAM"),
                 ("storage", "Storage"), ("gpu", "GPU"), ("location", "Location"), ("status", "Status"),
                 ("warranty", "Warranty"), ("faults", "Faults")]

FAULT_COLUMNS = [
    ("date", "Date"),
    ("asset_id", "Asset"),
    ("model", "Model"),
    ("location", "Location"),
    ("status", "Status now"),
    ("category", "Category"),
    ("description", "Fault"),
    ("action", "Action taken"),
    ("technician", "Technician"),
    ("cost", "Cost")
]


def fault_current_sort():
    """Sort column and direction from the URL for fault history. Default: date, descending."""
    key = request.args.get("sort")
    key = key if key in queries.FAULT_SORT_COLUMNS or key == "status" else "date"
    direction = "asc" if request.args.get("dir") == "asc" else "desc"
    return key, direction


def fault_current_first():
    """The status that is listed first when the fault history is sorted by Status."""
    first = request.args.get("first")
    return first if first in STATUS_ORDER else STATUS_ORDER[0]


def fault_sort_link(col):
    """Link for a column heading in Fault History. A new column starts ascending; the same column flips direction.
    The Status heading cycles the first status group. (The page itself opens sorted by date, newest first.)"""
    args = request.args.to_dict()
    key, direction = fault_current_sort()
    if col == "status":
        nxt = STATUS_ORDER[(STATUS_ORDER.index(fault_current_first()) + 1) % len(STATUS_ORDER)] if key == "status" \
            else STATUS_ORDER[0]
        args.update(sort="status", first=nxt)
        args.pop("dir", None)
    else:
        new = "desc" if (col == key and direction == "asc") else "asc"
        args.update(sort=col, dir=new)
        args.pop("first", None)
    return url_for("maintenance", **args)


def current_sort():
    """Sort column and direction from the URL. Default: Asset ID, ascending."""
    key = request.args.get("sort")
    key = key if key in SORT_COLUMNS or key == "status" else "asset_id"
    direction = "desc" if request.args.get("dir") == "desc" else "asc"
    return key, direction


def current_first():
    """The status that is listed first when the list is sorted by Status."""
    first = request.args.get("first")
    return first if first in STATUS_ORDER else STATUS_ORDER[0]


def sort_link(col):
    """Link for a column heading. A new column starts ascending; the same column flips direction.
    The Status heading cycles the first status group. Filters and search stay in the link."""
    args = request.args.to_dict()
    key, direction = current_sort()
    if col == "status":
        nxt = STATUS_ORDER[(STATUS_ORDER.index(current_first()) + 1) % len(STATUS_ORDER)] if key == "status" \
            else STATUS_ORDER[0]
        args.update(sort="status", first=nxt)
        args.pop("dir", None)
    else:
        new = "desc" if (col == key and direction == "asc") else "asc"
        args.update(sort=col, dir=new)
        args.pop("first", None)
    return url_for("assets", **args)


@app.before_request
def guard_requests():
    """Protections for a local-only app: only answer requests addressed to this computer (blocks DNS rebinding),
    refuse form posts that come from another website (blocks cross-site request forgery), and cap field size."""
    try:
        hostname = urlsplit("//" + request.host).hostname
    except ValueError:
        hostname = None
    extra = {h.strip().lower() for h in os.environ.get("INVENTORY_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if hostname not in LOCAL_HOSTS | extra:
        return render_template("message.html", title="Wrong address",
                               text="Open this app at http://127.0.0.1 (the address printed when it starts)."), 400
    if request.method == "POST":
        site = request.headers.get("Sec-Fetch-Site")      # sent by every current browser
        origin = request.headers.get("Origin")
        # Another website or another local port ("same-site") is refused. Browsers without Sec-Fetch-Site are
        # judged by the Origin header instead.
        if site in ("cross-site", "same-site") or \
                (site is None and origin is not None and urlsplit(origin).netloc != request.host):
            return render_template("message.html", title="Request refused",
                                   text="This form was sent from another website, so it was not saved."), 403
        for value in request.form.values():
            if len(value) > MAX_FIELD:
                return render_template("message.html", title="Text too long",
                                       text=f"A box holds at most {MAX_FIELD} characters. Go back and shorten it."), 400


@app.after_request
def security_headers(resp):
    """The app never loads anything from the internet; the policy makes the browser enforce that."""
    resp.headers.setdefault("Content-Security-Policy",
                            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                            "img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'self'")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")      # "no-referrer" makes browsers send Origin: null
    return resp


def flash_errors(errors):
    """Show each distinct message once, in the order found."""
    for e in dict.fromkeys(errors):
        flash(e, "danger")


@app.context_processor
def option_lists():
    """Drop-down choices and helpers, available to every template."""
    return dict(storage_types=STORAGE_TYPES, statuses=STATUSES, ram_types=RAM_TYPES,
                fault_categories=FAULT_CATEGORIES, locations=queries.location_names(),
                NEW_LOCATION=NEW_LOCATION, sort_link=sort_link, today=date.today().isoformat(),
                github_url=GITHUB_URL)


@app.template_filter("money")
def money(value):
    """Format a cost in US dollars, e.g. 1234.5 -> $1,234.50."""
    return f"${float(value or 0):,.2f}"


def today():
    return date.today().isoformat()


def warranty_status(expiry):
    t = date.today()
    try:
        exp = date.fromisoformat(expiry)
    except (TypeError, ValueError):
        return "Unknown"         # a damaged date must never break the whole list
    if exp < t:
        return "Expired"
    if exp <= t + timedelta(days=WARRANTY_SOON_DAYS):
        return "Expiring soon"
    return "Active"


def parse_date(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            pass
    return None


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


def location_problems(name, is_new_loc):
    """Problems with the place chosen or typed on the Add / Edit form."""
    if is_new_loc:
        if not name:
            return ["Type the name of the new location (for example Lab 4)."]
        if name == NEW_LOCATION or len(name) > LOCATION_MAX:
            return [f"A location name can have at most {LOCATION_MAX} characters."]
        if queries.next_subnet() > 254:
            return ["No more places can be added: the IP range numbers 1 to 254 are all used."]
    elif name and name not in queries.location_names():
        return ["Choose a place from the list."]
    return []


def existing_or_new_prefix(name, is_new):
    """Short code used for the asset IDs of this place."""
    if is_new:
        return queries.suggest_prefix(name, queries.all_prefixes())
    return queries.location_prefix(name)


def fix_problems(category, description, action, technician):
    """Closing a fault (status Operational) needs every field except cost; the category may not be Unknown."""
    problems = []
    if category not in FAULT_CATEGORIES or category == "Unknown":
        problems.append("To mark the asset Operational, the fault category must be specified (not Unknown).")
    for label, value in [("Fault description", description), ("Action taken", action), ("Technician", technician)]:
        if not value:
            problems.append(f"{label} is required to mark the asset Operational.")
    return problems


def parse_cost(text):
    """Returns (cost, error). Blank = 0."""
    try:
        value = float(text or 0)
        if not math.isfinite(value) or value < 0 or value > MAX_COST:
            raise ValueError
        return value, None
    except ValueError:
        return 0.0, "Cost must be a number, zero or more."


def parse_optional_cost(text):
    """Returns (cost, error). A blank box gives (None, None): 'keep the current Updated Cost'.
    A typed 0 is a real cost of 0."""
    if text is None or str(text).strip() == "":
        return None, None
    return parse_cost(text)


def fault_box(form):
    """The Current fault box on the Edit / Add form, as typed, plus its date and cost."""
    box = {k: (form.get(k) or "").strip() for k in
           ["status_category", "status_detail", "status_action", "status_technician", "fault_date", "cost"]}
    box["date"] = parse_date(box["fault_date"])
    box["cost"], box["cost_error"] = parse_optional_cost(box["cost"])   # single Updated Cost; None = blank
    return box


def box_problems(box, closing):
    """Date and cost of the fault box; closing also needs every fault field."""
    problems = []
    if box["date"] is None:
        problems.append("Date must be a valid date (YYYY-MM-DD).")
    elif box["date"] > date.today():
        problems.append("Date cannot be in the future.")
    if box["cost_error"]:
        problems.append(box["cost_error"])
    if closing:
        problems += fix_problems(box["status_category"], box["status_detail"],
                                 box["status_action"], box["status_technician"])
    return problems


def first_step(box, was_open, prev_action):
    """The step to save for a submit, or None when nothing is new. Only a changed action is a step:
    a change of the cost alone updates the Updated Cost and adds no step."""
    if not box["status_action"] or box["status_action"] == (prev_action or ""):
        return None
    return {"step_date": box["date"].isoformat(), "action": box["status_action"],
            "technician": box["status_technician"]}


FIELD_LABELS = {"manufacturer": "Manufacturer", "model": "Model", "cpu": "CPU", "cpu_family": "CPU family",
                "os": "Operating system", "location": "Location", "ram_gb": "RAM (GB)", "storage_gb": "Storage (GB)"}


def validate_asset(f, is_new, subnet, prev=None):
    """Server-side validation. Returns (clean_dict, errors_list).
    subnet is not used here (IP addresses are never assigned automatically); it is only passed along for new places.
    prev = the asset as it is in the database (None for a new asset)."""
    errors = []
    c = {k: (f.get(k) or "").strip() for k in FIELDS}
    for k in ["manufacturer", "model", "cpu", "cpu_family", "os", "location"]:
        if not c[k]:
            errors.append(f"{FIELD_LABELS[k]} is required.")
    if not c["asset_id"]:
        errors.append("Choose a location so the asset ID can be assigned automatically, or type an ID.")
    elif is_new and not ASSET_ID_RE.match(c["asset_id"]):
        errors.append("Asset ID may use letters, digits, - . and _ only (no spaces, at most 40 characters).")
    for k in ["ram_gb", "storage_gb"]:
        try:
            c[k] = int(c[k])
            if c[k] <= 0:
                raise ValueError
        except ValueError:
            errors.append(f"{FIELD_LABELS[k]} must be a positive whole number.")
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

    # Current fault. Everything here is optional on this form: a blank category becomes "Unknown".
    if c["status"] == "Operational":
        for k in CURRENT_FAULT_FIELDS:
            c[k] = None                  # a working asset has no current fault (shown as "-")
    elif c["status"] in CURRENT_ERROR_STATUSES:
        c["status_category"] = c["status_category"] or "Unknown"
        if c["status_category"] not in FAULT_CATEGORIES:
            errors.append("Choose a fault category from the list.")
        if c["status"] == "Under Repair" and not c["status_technician"]:
            errors.append("Technician is required when the PC is Under Repair.")
        was_faulty = prev is not None and prev["status"] in CURRENT_ERROR_STATUSES
        c["status_date"] = prev["status_date"] if (was_faulty and prev["status_date"]) else today()
        c["status_cost"], cost_error = parse_cost(f.get("cost"))
        if cost_error:
            errors.append(cost_error)

    # IP address: optional. Blank stays "Not Assigned" (no automatic assignment).
    # Two PCs in the same place never share an IP.
    loc = c["location"]
    skip_self = "" if is_new else c["asset_id"]  # when editing, a PC does not conflict with itself
    if c["ip_address"]:
        try:
            ipaddress.IPv4Address(c["ip_address"])
        except ValueError:
            errors.append("IP address is not valid (example: 192.168.1.20).")
        else:
            owner = queries.ip_owner(loc, c["ip_address"], skip_self) if loc else None
            if owner:
                errors.append(f"IP {c['ip_address']} is already used by {owner} in {loc}. "
                              "Each PC in one place needs its own IP.")
    c["ip_address"] = c["ip_address"] or None

    p, w = parse_date(c["purchase_date"]), parse_date(c["warranty_expiry"])
    if p:
        c["purchase_date"] = p.isoformat()          # always stored as YYYY-MM-DD, whatever format was typed
    if w:
        c["warranty_expiry"] = w.isoformat()
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
    key, direction = current_sort()
    return render_template("assets.html", assets=enriched, opts=opts, f=request.args,
                           sort=key, direction=direction, first=current_first(), columns=ASSET_COLUMNS)


@app.route("/api/next-asset-id")
def api_next_asset_id():
    loc = (request.args.get("location") or "").strip()
    if not loc:
        return {"asset_id": ""}
    is_new = loc not in queries.location_names()
    prefix = existing_or_new_prefix(loc, is_new)
    next_id = queries.next_asset_id(prefix) if prefix else ""
    return {"asset_id": next_id}


@app.route("/assets/new", methods=["GET", "POST"])
def asset_new():
    if request.method == "POST":
        form = dict(request.form)
        name, is_new_loc = resolve_location(request.form)
        form["location"] = name
        shown = dict(form, location=NEW_LOCATION, new_location=name) if is_new_loc else form
        errors = location_problems(name, is_new_loc)
        if name and not (form.get("asset_id") or "").strip():
            prefix = existing_or_new_prefix(name, is_new_loc)
            if prefix:
                form["asset_id"] = queries.next_asset_id(prefix)   # automatic ID
        box = fault_box(form)
        opens = form.get("status") in CURRENT_ERROR_STATUSES
        if opens:
            errors += box_problems(box, closing=False)
        subnet = queries.next_subnet() if is_new_loc else None
        c, more = validate_asset(form, True, subnet)
        errors += more
        if errors:
            flash_errors(errors)
            return render_template("asset_form.html", a=shown, new=True, current_status=None)
        step = None
        if opens:
            c["status_date"] = box["date"].isoformat()
            c["status_cost"] = box["cost"] if box["cost"] is not None else 0.0
            step = first_step(box, was_open=False, prev_action=None)
        try:
            if is_new_loc:
                queries.add_location(name, existing_or_new_prefix(name, True), subnet)
            queries.insert_asset(c, step=step)
        except sqlite3.IntegrityError as e:
            message = "Choose a place from the list." if "FOREIGN KEY" in str(e) else f"Database rejected the record: {e}"
            flash(message, "danger")
            return render_template("asset_form.html", a=shown, new=True, current_status=None)
        flash(f"Asset {c['asset_id']} added (IP {c['ip_address'] or 'Not Assigned'}).", "success")
        return redirect(url_for("asset_detail", asset_id=c["asset_id"]))
    return render_template("asset_form.html", a={}, new=True, current_status=None)


@app.route("/assets/<asset_id>")
def asset_detail(asset_id):
    a = queries.get_asset(asset_id)
    if not a:
        return render_template("message.html", title="Asset not found",
                               text=f"No asset with ID '{asset_id}'."), 404
    events = queries.get_events(asset_id)
    total_cost = sum(e["cost_usd"] for e in events)
    return render_template("asset_detail.html", a=a, events=events, total_cost=total_cost,
                           warranty=warranty_status(a["warranty_expiry"]), today=today(),
                           steps=queries.get_steps(asset_id),
                           open_total=queries.open_fault_cost(asset_id) if a["status"] in CURRENT_ERROR_STATUSES else 0)


@app.route("/assets/<asset_id>/edit", methods=["GET", "POST"])
def asset_edit(asset_id):
    a = queries.get_asset(asset_id)
    if not a:
        return render_template("message.html", title="Asset not found", text=asset_id), 404
    current_status = a["status"]
    open_total = queries.open_fault_cost(asset_id) if current_status in CURRENT_ERROR_STATUSES else 0
    if request.method == "POST":
        form = dict(request.form)
        form["asset_id"] = asset_id                       # primary key is never changed
        name, is_new_loc = resolve_location(request.form)
        form["location"] = name
        shown = dict(form, location=NEW_LOCATION, new_location=name) if is_new_loc else form
        errors = location_problems(name, is_new_loc)
        box = fault_box(form)
        was_open = current_status in CURRENT_ERROR_STATUSES
        closing = was_open and form.get("status") == "Operational"
        if closing or form.get("status") in CURRENT_ERROR_STATUSES:
            errors += box_problems(box, closing=closing)
        subnet = queries.next_subnet() if is_new_loc else None
        c, more = validate_asset(form, False, subnet, a)
        errors += more
        if errors:
            flash_errors(errors)
            return render_template("asset_form.html", a=shown, new=False, current_status=current_status, open_total=open_total)
        if is_new_loc:
            queries.add_location(name, existing_or_new_prefix(name, True), subnet)
        if closing:
            close = {"event_date": box["date"].isoformat(), "fault_category": box["status_category"],
                     "fault_description": box["status_detail"], "action": box["status_action"],
                     "technician": box["status_technician"], "cost": box["cost"]}
            queries.update_asset(asset_id, c, close=close)
            flash("Asset is Operational. The fault is recorded in the history as one entry.", "success")
            return redirect(url_for("asset_detail", asset_id=asset_id))
        step = None
        if c["status"] in CURRENT_ERROR_STATUSES:
            if not was_open:
                c["status_date"] = box["date"].isoformat()     # the fault starts on this date
            if box["cost"] is not None:
                c["status_cost"] = box["cost"]                 # typed, 0 included
            else:                                              # blank: keep the current Updated Cost
                c["status_cost"] = float(a["status_cost"] or 0) if was_open else 0.0
            step = first_step(box, was_open=was_open, prev_action=a["status_action"] if was_open else None)
        queries.update_asset(asset_id, c, step=step)
        flash("Asset updated.", "success")
        return redirect(url_for("asset_detail", asset_id=asset_id))
    # the form template uses a.get(...), which a sqlite3.Row does not have: convert to a
    # dict and show empty (NULL) fields as blank instead of the text "None"
    a = {k: ("" if v is None else v) for k, v in dict(a).items()}
    return render_template("asset_form.html", a=a, new=False, current_status=current_status, open_total=open_total)


@app.route("/assets/<asset_id>/retire", methods=["POST"])
def asset_retire(asset_id):
    if not queries.get_asset(asset_id):
        return render_template("message.html", title="Asset not found", text=f"No asset with ID '{asset_id}'."), 404
    queries.set_active(asset_id, 0)
    flash(f"{asset_id} retired. History is kept.", "success")
    return redirect(url_for("asset_detail", asset_id=asset_id))


@app.route("/assets/<asset_id>/reactivate", methods=["POST"])
def asset_reactivate(asset_id):
    if not queries.get_asset(asset_id):
        return render_template("message.html", title="Asset not found", text=f"No asset with ID '{asset_id}'."), 404
    queries.set_active(asset_id, 1)
    flash(f"{asset_id} reactivated.", "success")
    return redirect(url_for("asset_detail", asset_id=asset_id))


@app.route("/assets/<asset_id>/fault/edit", methods=["GET", "POST"])
def asset_fault_edit(asset_id):
    a = queries.get_asset(asset_id)
    if not a:
        return render_template("message.html", title="Asset not found", text=asset_id), 404
    if a["status"] == "Operational":
        flash("This asset is Operational and has no open fault.", "info")
        return redirect(url_for("asset_detail", asset_id=asset_id))
    if request.method == "POST":
        f = {k: (request.form.get(k) or "").strip() for k in
             ["status_date", "status_category", "status_detail", "status_action", "status_technician", "cost"]}
        errors = []
        d = parse_date(f["status_date"])
        if not d:
            errors.append("Fault date must be a valid date (YYYY-MM-DD).")
        elif d > date.today():
            errors.append("Fault date cannot be in the future.")
        if f["status_category"] and f["status_category"] not in FAULT_CATEGORIES:
            errors.append("Choose a valid fault category from the list.")
        if a["status"] == "Under Repair" and not f["status_technician"]:
            errors.append("Technician is required when Under Repair.")
        cost, cost_error = parse_optional_cost(f["cost"])     # blank = keep the current Updated Cost
        if cost_error:
            errors.append(cost_error)
        if errors:
            flash_errors(errors)
            return render_template("asset_fault_edit.html", a=dict(a, **f), fault_categories=FAULT_CATEGORIES)
        if cost is None:
            cost = float(a["status_cost"] or 0)
        queries.update_current_fault(asset_id, d.isoformat(), f["status_category"] or "Unknown",
                                     f["status_detail"], f["status_action"], f["status_technician"], cost)
        flash("Current fault updated.", "success")
        return redirect(url_for("asset_detail", asset_id=asset_id))
    cur_dict = dict(a)
    cur_dict["cost"] = a["status_cost"] if a["status_cost"] is not None else queries.open_fault_cost(asset_id)
    return render_template("asset_fault_edit.html", a=cur_dict, fault_categories=FAULT_CATEGORIES)


@app.route("/steps/<int:step_id>/edit", methods=["GET", "POST"])
def step_edit(step_id):
    s = queries.get_step(step_id)
    if not s:
        return render_template("message.html", title="Step not found", text=f"No step with ID {step_id}"), 404
    if request.method == "POST":
        f = {k: (request.form.get(k) or "").strip() for k in ["step_date", "action", "technician"]}
        errors = []
        d = parse_date(f["step_date"])
        if not d:
            errors.append("Step date must be a valid date (YYYY-MM-DD).")
        elif d > date.today():
            errors.append("Step date cannot be in the future.")
        if errors:
            flash_errors(errors)
            return render_template("step_edit.html", s=dict(s, **f))
        queries.update_step(step_id, d.isoformat(), f["action"], f["technician"])
        flash("Fault step updated.", "success")
        return redirect(url_for("asset_detail", asset_id=s["asset_id"]))
    return render_template("step_edit.html", s=dict(s))


@app.route("/steps/<int:step_id>/delete", methods=["POST"])
def step_delete(step_id):
    s = queries.get_step(step_id)
    if not s:
        return render_template("message.html", title="Step not found", text=f"No step with ID {step_id}"), 404
    aid = s["asset_id"]
    queries.delete_step(step_id)
    flash("Fault step removed.", "success")
    return redirect(url_for("asset_detail", asset_id=aid))


@app.route("/maintenance/<int:event_id>/edit", methods=["GET", "POST"])
def maintenance_event_edit(event_id):
    ev = queries.get_maintenance_event(event_id)
    if not ev:
        return render_template("message.html", title="Event not found", text=f"No maintenance event with ID {event_id}."), 404
    asset = queries.get_asset(ev["asset_id"])
    if request.method == "POST":
        f = {k: (request.form.get(k) or "").strip() for k in
             ["event_date", "fault_category", "fault_description", "action_taken", "technician", "cost"]}
        errors = []
        d = parse_date(f["event_date"])
        if not d:
            errors.append("Event date must be a valid date (YYYY-MM-DD).")
        elif d > date.today():
            errors.append("Event date cannot be in the future.")
        if not f["fault_category"] or f["fault_category"] not in FAULT_CATEGORIES:
            errors.append("Choose a valid fault category from the list.")
        if not f["fault_description"]:
            errors.append("Fault description is required.")
        if not f["action_taken"]:
            errors.append("Action taken is required.")
        if not f["technician"]:
            errors.append("Technician is required.")
        cost, cost_error = parse_cost(f["cost"])
        if cost_error:
            errors.append(cost_error)
        if errors:
            flash_errors(errors)
            return render_template("maintenance_edit.html", ev=dict(f, id=event_id, asset_id=ev["asset_id"]),
                                   event_id=event_id, asset=asset, fault_categories=FAULT_CATEGORIES)
        queries.update_maintenance_event(event_id, d.isoformat(), f["fault_category"],
                                         f["fault_description"], f["action_taken"], f["technician"], cost)
        flash("Maintenance history entry updated.", "success")
        return redirect(url_for("asset_detail", asset_id=ev["asset_id"]))
    ev_dict = dict(ev)
    ev_dict["cost"] = ev["cost_usd"]
    return render_template("maintenance_edit.html", ev=ev_dict, event_id=event_id, asset=asset, fault_categories=FAULT_CATEGORIES)


@app.route("/maintenance/<int:event_id>/delete", methods=["POST"])
def maintenance_event_delete(event_id):
    ev = queries.get_maintenance_event(event_id)
    if not ev:
        return render_template("message.html", title="Event not found", text=f"No event {event_id}"), 404
    aid = ev["asset_id"]
    queries.delete_maintenance_event(event_id)
    flash("Maintenance event deleted.", "success")
    return redirect(url_for("asset_detail", asset_id=aid))


@app.route("/assets/<asset_id>/maintenance", methods=["POST"])
def maintenance_add(asset_id):
    asset = queries.get_asset(asset_id)
    if not asset:
        return render_template("message.html", title="Asset not found", text=asset_id), 404
    f = {k: (request.form.get(k) or "").strip() for k in
         ["event_date", "fault_category", "fault_description", "action_taken", "technician", "cost"]}
    new_status = request.form.get("set_status")
    errors = []
    d = parse_date(f["event_date"])
    if not d:
        errors.append("Event date must be a valid date (YYYY-MM-DD).")
    elif d > date.today():
        errors.append("Event date cannot be in the future.")
    else:
        f["event_date"] = d.isoformat()
    if new_status not in STATUSES:
        errors.append("Choose the status the asset will have: Operational, Faulty or Under Repair.")
    if f["fault_category"] and f["fault_category"] not in FAULT_CATEGORIES:
        errors.append("Choose a fault category from the list.")
    was_open = asset["status"] in CURRENT_ERROR_STATUSES
    # A typed cost (0 included) is used as it is. A blank box gives None: the open fault keeps its cost.
    updated_cost, cost_error = parse_optional_cost(f["cost"])
    if cost_error:
        errors.append(cost_error)
    if new_status == "Operational":
        errors += fix_problems(f["fault_category"], f["fault_description"], f["action_taken"], f["technician"])
    elif new_status == "Under Repair" and not f["technician"]:
        errors.append("Technician is required when the PC is Under Repair.")
    if new_status in CURRENT_ERROR_STATUSES:
        f["fault_category"] = f["fault_category"] or "Unknown"
    if errors:
        flash_errors(errors)
        return redirect(url_for("asset_detail", asset_id=asset_id))
    current = None
    if new_status in CURRENT_ERROR_STATUSES:
        current = {"category": f["fault_category"], "detail": f["fault_description"],
                   "action": f["action_taken"], "technician": f["technician"],
                   "date": asset["status_date"] if (was_open and asset["status_date"]) else f["event_date"]}
    queries.log_event(asset_id, f, updated_cost, new_status, current)
    if new_status == "Operational" and was_open:
        flash("Fault closed. The asset is Operational and the fault is recorded in the history as one entry.", "success")
    elif new_status == "Operational":
        flash("Maintenance event logged.", "success")
    else:
        flash("Saved. The fault stays open; it goes into the history when the PC is Operational again.", "success")
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
    key, direction = fault_current_sort()
    first = fault_current_first()
    events = queries.fault_history(sort=key, direction=direction, first=first)
    total_cost = sum(e["cost_usd"] for e in events)
    return render_template("maintenance.html", events=events, total_cost=total_cost,
                           sort=key, direction=direction, first=first,
                           columns=FAULT_COLUMNS, sort_link=fault_sort_link)


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
def csv_safe(value):
    """A cell that starts with = + - @ would run as a formula when the file is opened in Excel / Calc.
    A leading apostrophe makes it plain text."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def csv_response(header, rows, filename):
    buf = io.StringIO()
    buf.write("\ufeff")                      # byte-order mark: Excel then reads the file as UTF-8
    w = csv.writer(buf)
    w.writerow([csv_safe(h) for h in header])
    for r in rows:
        w.writerow([csv_safe(v) for v in r])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.route("/export/assets.csv")
def export_assets():
    rows = queries.filtered_assets(request.args)  # exports whatever the current filters and sort show
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


@app.errorhandler(sqlite3.IntegrityError)
def db_rejected(e):
    return render_template("message.html", title="Not saved",
                           text=f"The database rejected the data ({e}). Go back and check the form."), 400


@app.errorhandler(413)
def too_large(e):
    return render_template("message.html", title="Too much data", text="That request is too large."), 413


@app.errorhandler(405)
def not_allowed(e):
    return render_template("message.html", title="Not allowed", text="That address cannot be used this way."), 405


@app.errorhandler(500)
def server_error(e):
    return render_template("message.html", title="Something went wrong",
                           text="The page could not be built. Nothing was lost; go back and try again."), 500


@app.errorhandler(sqlite3.OperationalError)
def db_missing(e):
    return render_template("message.html", title="Database problem",
                           text=f"{e}. If inventory.db is missing, run: python seed.py"), 500


def free_port(start=5000, tries=100):
    """The first free port from `start` on this computer, so several copies of the app can run side by side."""
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit(f"No free port found between {start} and {start + tries - 1}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EF101-P11 Hardware Inventory")
    parser.add_argument("--port", type=int, help="port to use (default: the first free port from 5000)")
    args = parser.parse_args()
    created = ensure_database()           # first launch only: an empty database; existing data is kept
    problem = schema_problem()
    if problem:                           # an old copy of inventory.db: say what to do instead of failing later
        raise SystemExit(f"\n{problem}\nUpgrade it without losing data:  python migrate.py\n"
                         "(or start fresh with demo data:  python seed.py)")
    port = args.port or int(os.environ.get("PORT", 0)) or free_port()
    print("Hardware Inventory")
    print(f"  Open in your browser:  http://127.0.0.1:{port}")
    print(f"  Data file:             {DB_PATH}")
    if created:
        print("  A new, empty database was created. Add assets, or run 'python seed.py' for demo data.")
    print("  Press Ctrl+C to stop.")
    # debug mode (browser console that can run code) is off unless you ask for it: set FLASK_DEBUG=1
    app.run(host="127.0.0.1", port=port, debug=os.environ.get("FLASK_DEBUG") == "1", use_reloader=False)
