"""Automated test evidence for EF101-P11. Run: python test_app.py
Uses a temporary database (your inventory.db is never touched) and prints a
Test | Input | Expected | Result table you can paste into the report."""
import csv
import re
import sqlite3
import io
import os
import sys
import tempfile
from pathlib import Path

import db

TMP = Path(tempfile.mkdtemp()) / "test.db"
db.DB_PATH = TMP                       # redirect everything to a throwaway database
import seed                            # noqa: E402
import queries                         # noqa: E402
import app as appmod                   # noqa: E402

seed.main()
client = appmod.app.test_client()
results = []


def check(name, inp, expected, ok):
    results.append((name, inp, expected, "PASS" if ok else "FAIL"))


def count(sql, *p):
    c = db.get_db()
    n = c.execute(sql, p).fetchone()[0]
    c.close()
    return n


def chart(d, name):
    """A dashboard chart as {label: count}."""
    return {label: n for label, n, _ in d["charts"][name]}


def fault(d):
    return {label: n for label, n, _ in d["fault_cats"]}


def count_rows(sql):
    c = db.get_db()
    rows = c.execute(sql).fetchall()
    c.close()
    return rows


def sums_match_total(d):
    """Every distribution chart must add up to the number of active assets."""
    return all(sum(n for _, n, _ in rows) == d["stats"]["total"] for rows in d["charts"].values())


GOOD = dict(asset_id="TEST-001", manufacturer="Dell", model="OptiPlex 7090", cpu="Intel Core i5-12400",
            cpu_family="Core i5", ram_gb="16", ram_type="DDR4", storage_type="SSD", storage_gb="512", gpu="",
            os="Windows 11 Pro", ip_address="", location="Lab 1",
            purchase_date="2025-01-10", warranty_expiry="2028-01-10", status="Operational",
            fault_date="2026-03-10", fault_cost="0")

# 1. Dataset size
check("Demo data size", "seed.py", ">= 30 assets", count("SELECT COUNT(*) FROM assets") >= 30)

# 2. Add asset
before = count("SELECT COUNT(*) FROM assets")
client.post("/assets/new", data=GOOD)
check("Add asset", "valid form", "row count +1", count("SELECT COUNT(*) FROM assets") == before + 1)

# 3. Duplicate ID rejected
client.post("/assets/new", data=GOOD)
check("Duplicate asset ID", "TEST-001 again", "rejected, still 1 row",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='TEST-001'") == 1)

# 4. Invalid / blank input rejected
bad_cases = {
    "Blank manufacturer": {"manufacturer": ""},
    "RAM = 0": {"ram_gb": "0"},
    "RAM = text": {"ram_gb": "abc"},
    "Negative storage": {"storage_gb": "-5"},
    "Bad IP": {"ip_address": "999.1.1.1"},
    "Bad date": {"purchase_date": "2025-13-45"},
    "Future purchase date": {"purchase_date": "2999-01-01"},
    "Warranty before purchase": {"warranty_expiry": "2020-01-01"},
    "Invalid storage type": {"storage_type": "Floppy"},
    "Missing RAM type": {"ram_type": ""},
    "Unknown RAM type": {"ram_type": "DDR6"},
    "GPU without VRAM": {"gpu": "NVIDIA RTX 3060", "gpu_vram_gb": ""},
    "Unknown location": {"location": "Mars"},
    "Faulty, category not in list": {"status": "Faulty", "status_category": "Gremlins", "status_detail": "x"},
}
for i, (label, change) in enumerate(bad_cases.items()):
    form = {**GOOD, **change, "asset_id": f"BAD-{i}"}
    client.post("/assets/new", data=form)
    check(label, str(change), "rejected, nothing saved",
          count("SELECT COUNT(*) FROM assets WHERE asset_id=?", f"BAD-{i}") == 0)

# 5. Edit asset
client.post("/assets/TEST-001/edit", data={**GOOD, "ram_gb": "32", "location": "Lab 2"})
check("Edit asset", "RAM 16 -> 32, Lab 2", "values updated",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='TEST-001' AND ram_gb=32 AND location='Lab 2'") == 1)

# 5b. Edit page must open for existing assets (regression: sqlite3.Row has no .get)
c = db.get_db()
ids = [r[0] for r in c.execute("SELECT asset_id FROM assets ORDER BY asset_id")]
nullgpu = c.execute("SELECT asset_id FROM assets WHERE gpu IS NULL OR gpu='' LIMIT 1").fetchone()
c.close()
bad = [i for i in ids if client.get(f"/assets/{i}/edit").status_code != 200]
check("Edit page opens", "GET /assets/<id>/edit for every asset", "HTTP 200 for all", not bad)
if nullgpu:
    html = client.get(f"/assets/{nullgpu[0]}/edit").get_data(as_text=True)
    check("Edit form blank fields", "asset with no GPU", "no text 'None' in inputs", 'value="None"' not in html)

# 6. Log maintenance + history
ev = dict(event_date="2026-01-05", fault_category="Disk failure", fault_description="Clicking noise",
          action_taken="Replaced drive", technician="Ali", cost="45.5", set_status="Under Repair",
          status_category="Disk failure", status_detail="Drive clicking, replacement pending")
client.post("/assets/TEST-001/maintenance", data=ev)
check("Under Repair on the Log form opens the fault", "valid event with status Under Repair",
      "the action is a step; no history row yet",
      count("SELECT COUNT(*) FROM fault_steps WHERE asset_id='TEST-001' AND action='Replaced drive'") == 1
      and count("SELECT COUNT(*) FROM maintenance WHERE asset_id='TEST-001'") == 0)
check("Event updates status", "set_status=Under Repair", "asset status changed",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='TEST-001' AND status='Under Repair'") == 1)
page = client.get("/assets/TEST-001").get_data(as_text=True)
check("Asset history page", "GET /assets/TEST-001", "shows the event", "Clicking noise" in page)
client.post("/assets/TEST-001/maintenance", data={**ev, "cost": "-5", "fault_description": "neg"})
client.post("/assets/TEST-001/maintenance", data={**ev, "event_date": "2999-01-01", "fault_description": "future"})
client.post("/assets/TEST-001/maintenance", data={**ev, "fault_category": "Gremlins", "fault_description": "bogus"})
check("Invalid event rejected", "negative cost / future date / unknown category", "nothing saved",
      count("SELECT COUNT(*) FROM maintenance WHERE fault_description IN ('neg','future','bogus')") == 0)

# 6b. Fault rules on the Log form and the Edit form
client.post("/assets/TEST-001/maintenance", data={**ev, "set_status": "Under Repair", "technician": "",
                                                    "fault_description": "tech missing"})
check("Under Repair needs a technician", "Under Repair, technician blank", "rejected, nothing saved",
      count("SELECT COUNT(*) FROM maintenance WHERE fault_description='tech missing'") == 0)
client.post("/assets/TEST-001/maintenance", data={"event_date": "2026-01-06", "fault_category": "",
            "fault_description": "", "action_taken": "", "technician": "", "cost": "", "set_status": "Faulty"})
check("Faulty needs no fault details", "Faulty, every fault field blank", "saved; category becomes Unknown",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='TEST-001' AND status='Faulty' AND status_category='Unknown'") == 1)
client.post("/assets/TEST-001/maintenance", data={"event_date": "2026-01-07", "fault_category": "Unknown",
            "fault_description": "fix attempt", "action_taken": "x", "technician": "Ali", "cost": "0", "set_status": "Operational"})
check("Fix needs a fault category other than Unknown", "set Operational, category Unknown", "rejected",
      count("SELECT COUNT(*) FROM maintenance WHERE fault_description='fix attempt'") == 0)
client.post("/assets/TEST-001/maintenance", data={"event_date": "2026-01-07", "fault_category": "Disk failure",
            "fault_description": "no status", "action_taken": "x", "technician": "Ali", "cost": "0", "set_status": ""})
check("Status must be chosen on the Log form", "no status chosen", "rejected",
      count("SELECT COUNT(*) FROM maintenance WHERE fault_description='no status'") == 0)
client.post("/assets/TEST-001/edit", data={**GOOD, "status": "Operational", "status_category": "Unknown",
            "status_detail": "", "status_action": "", "status_technician": ""})
check("Edit form: closing needs every field", "status Operational, fields blank", "rejected, status stays Faulty",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='TEST-001' AND status='Faulty'") == 1)
client.post("/assets/TEST-001/edit", data={**GOOD, "status": "Faulty", "status_category": "Overheating",
            "status_detail": "Hot CPU", "status_action": "Clean fans", "status_technician": "Sara"})
detail = client.get("/assets/TEST-001").get_data(as_text=True)
check("Current fault section shows the entry", "GET /assets/TEST-001", "Hot CPU, Clean fans, Sara shown",
      "Hot CPU" in detail and "Clean fans" in detail and "Sara" in detail)
check("Log form is prefilled from the current fault", "GET /assets/TEST-001", "Overheating selected, fields filled",
      "<option selected>Overheating</option>" in detail and 'value="Hot CPU"' in detail
      and 'value="Clean fans"' in detail and 'value="Sara"' in detail)
client.post("/assets/TEST-001/edit", data={**GOOD, "status": "Operational", "status_category": "Overheating",
            "status_detail": "Hot CPU", "status_action": "Clean fans", "status_technician": "Sara"})
check("Edit form closes the fault", "status Operational with all fields", "status Operational, fault fields NULL",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='TEST-001' AND status='Operational' AND status_category IS NULL "
            "AND status_detail IS NULL AND status_action IS NULL AND status_technician IS NULL AND status_date IS NULL") == 1)
check("The fix is in the history", "the closing edit", "one history row with the fix details",
      count("SELECT COUNT(*) FROM maintenance WHERE asset_id='TEST-001' AND fault_description='Hot CPU' "
            "AND action_taken LIKE '%Clean fans%' AND technician='Sara'") == 1)
detail = client.get("/assets/TEST-001").get_data(as_text=True)
check("Current fault shows dashes when Operational", "GET /assets/TEST-001", "a row of '-' in every column",
      "<td>-</td><td>-</td><td>-</td><td>-</td><td>-</td>" in detail)
OTHER = [i for i in ids if i != "TEST-001"][0]   # a seeded asset, so TEST-001 keeps its events
client.post(f"/assets/{OTHER}/maintenance", data={**ev, "fault_category": "Other", "fault_description": "Board not posting",
                                                   "action_taken": "Ordered board", "technician": "Ali", "set_status": "Faulty"})
check("Log form stores the current fault", "set Faulty, category Other", "category, detail and action saved",
      count("SELECT COUNT(*) FROM assets WHERE asset_id=? AND status='Faulty' AND status_category='Other' "
            "AND status_detail='Board not posting' AND status_action='Ordered board'", OTHER) == 1)

# 7. Retire (not delete) and reactivate
client.post("/assets/TEST-001/retire")
check("Retire asset", "POST retire", "active=0, row kept",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='TEST-001' AND active=0") == 1)
check("Retired hidden from list", "GET /assets", "TEST-001 not shown",
      'href="/assets/TEST-001"' not in client.get("/assets").get_data(as_text=True))
check("Retired visible on request", "GET /assets?show=retired", "TEST-001 shown",
      'href="/assets/TEST-001"' in client.get("/assets?show=retired").get_data(as_text=True))
client.post("/assets/TEST-001/reactivate")
check("Reactivate asset", "POST reactivate", "active=1",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='TEST-001' AND active=1") == 1)


# 8. Filters: page rows must match direct SQL counts
def rows_in(url):
    html = client.get(url).get_data(as_text=True)
    return html.count('href="/assets/') - html.count('href="/assets/new"')   # row links only


c = db.get_db()
fam = c.execute("SELECT cpu_family FROM assets WHERE active=1 LIMIT 1").fetchone()[0]
for label, qs, sql, param in [
    ("Filter CPU family", f"cpu_family={fam}", "SELECT COUNT(*) FROM assets WHERE active=1 AND cpu_family=?", fam),
    ("Filter RAM", "ram_gb=16", "SELECT COUNT(*) FROM assets WHERE active=1 AND ram_gb=?", 16),
    ("Filter RAM type", "ram_type=DDR4", "SELECT COUNT(*) FROM assets WHERE active=1 AND ram_type=?", "DDR4"),
    ("Filter storage type", "storage_type=HDD", "SELECT COUNT(*) FROM assets WHERE active=1 AND storage_type=?", "HDD"),
    ("Filter status", "status=Faulty", "SELECT COUNT(*) FROM assets WHERE active=1 AND status=?", "Faulty"),
    ("Filter warranty expired", "warranty=Expired",
     "SELECT COUNT(*) FROM assets WHERE active=1 AND warranty_expiry < date('now') AND ?", 1),
]:
    expected = c.execute(sql, (param,)).fetchone()[0]
    got = rows_in("/assets?" + qs)
    check(label, qs, f"{expected} rows", got == expected)
c.close()
check("Search", "q=OptiPlex", "all rows contain OptiPlex",
      all("OptiPlex" in r for r in [client.get("/assets?q=OptiPlex").get_data(as_text=True)]))
check("SQL injection attempt", "q=' OR 1=1 --", "no crash (parameterised SQL)",
      client.get("/assets?q=%27+OR+1%3D1+--").status_code == 200)

# 9. Dashboard: numbers, order of cards and charts, and the drop-down options
dash = client.get("/dashboard")
body = dash.get_data(as_text=True)
total_active = count("SELECT COUNT(*) FROM assets WHERE active=1")
check("Dashboard loads", "GET /dashboard", "HTTP 200", dash.status_code == 200)
check("Dashboard total", "active assets", f"shows {total_active}", f">{total_active}<" in body)
faulty_or_repair = count("SELECT COUNT(*) FROM assets WHERE active=1 AND status IN ('Faulty','Under Repair')")
check("Attention = current errors only", "GET /dashboard", f"heading shows {faulty_or_repair}",
      f"Assets needing attention ({faulty_or_repair})" in body)
stat_labels = ['class="l">Total active assets', 'class="l">Operational', 'class="l">Needing attention',
               'class="l">Retired', 'class="l">Faulty (', 'class="l">Fault events', 'class="l">Out of warranty',
               'class="l">Total maintenance cost']
spots = [body.find(s) for s in stat_labels]
check("Stat card order", "8 cards", "Total, Operational, Needing attention, Retired, Faulty, Fault events, "
      "Out of warranty, Maintenance cost", all(p >= 0 for p in spots) and spots == sorted(spots))
chart_titles = ["CPU family", "GPU", "RAM (GB)", "Storage type", "Assets per location", "Warranty"]
spots = [body.find(f"<h2>{t}</h2>") for t in chart_titles]
check("Chart order", "6 charts", "CPU family, GPU, RAM, Storage type, Assets per location, Warranty",
      all(p >= 0 for p in spots) and spots == sorted(spots))
check("Fault chart shows Other and Unknown", "GET /dashboard", "both entries shown",
      '>Other</span>' in body and '>Unknown</span>' in body)
check("Fault history not on dashboard", "GET /dashboard", "no Fault History heading there",
      "<h2>Fault History" not in body)
check("Dashboard sits in a 4-column stat grid", "GET /dashboard", "class stats4 present",
      'class="grid stats4"' in body)
check("Dashboard total cost in dollars", "GET /dashboard", "amounts start with $", "$" in body)

edit_page = client.get(f"/assets/{OTHER}").get_data(as_text=True)
check("Fault category drop-down", f"GET /assets/{OTHER}", "select with Other and Unknown",
      '<select name="fault_category"' in edit_page and ">Other</option>" in edit_page
      and ">Unknown</option>" in edit_page)
check("Current fault drop-down", f"GET /assets/{OTHER}/edit", "fault category select with Unknown and Other",
      '<select name="status_category"' in client.get(f"/assets/{OTHER}/edit").get_data(as_text=True)
      and "<option>Unknown</option>" in client.get(f"/assets/{OTHER}/edit").get_data(as_text=True))
check("RAM type drop-down", "GET /assets/new", "DDR1 to DDR5 options",
      all(f"<option>DDR{n}</option>" in client.get("/assets/new").get_data(as_text=True) for n in range(1, 6)))

# 10. Exports
a = client.get("/export/assets.csv")
m = client.get("/export/maintenance.csv")
arows = list(csv.reader(io.StringIO(a.get_data(as_text=True))))
mrows = list(csv.reader(io.StringIO(m.get_data(as_text=True))))
check("Export assets CSV", "GET /export/assets.csv", "header + one row per active asset",
      "csv" in a.mimetype and len(arows) - 1 == total_active)
check("Assets CSV has new fields", "header", "ram_type and status_category columns",
      "ram_type" in arows[0] and "status_category" in arows[0] and "status_detail" in arows[0])
check("Export maintenance CSV", "GET /export/maintenance.csv", "header + one row per event",
      len(mrows) - 1 == count("SELECT COUNT(*) FROM maintenance"))
check("Maintenance CSV uses cost_usd", "header", "cost_usd column", "cost_usd" in mrows[0])

# 11. Robustness
check("Unknown asset", "GET /assets/NOPE", "404 page, no crash", client.get("/assets/NOPE").status_code == 404)
check("Unknown URL", "GET /xyz", "404 page", client.get("/xyz").status_code == 404)

# 12. Sidebar pages, theme and resizer
last = db.get_db().execute("SELECT fault_description FROM maintenance ORDER BY event_date DESC, id DESC LIMIT 1").fetchone()
maint_page = client.get("/maintenance")
check("Fault History page", "GET /maintenance", "HTTP 200 and newest event listed",
      maint_page.status_code == 200 and last[0] in maint_page.get_data(as_text=True))
for url, label in [("/about/credits", "Credits"), ("/about/privacy", "Privacy Policy"),
                   ("/about/schema", "Project Schema"), ("/about/contact", "Contact")]:
    check(f"About: {label}", f"GET {url}", "HTTP 200", client.get(url).status_code == 200)
svg_dl = client.get("/about/schema.svg?download=1")
check("Schema SVG download", "GET /about/schema.svg?download=1", "attachment containing the diagram",
      "attachment" in svg_dl.headers.get("Content-Disposition", "") and b"<svg" in svg_dl.data)
svg_view = client.get("/about/schema.svg")
check("Schema SVG shown on page", "GET /about/schema.svg", "inline SVG, not a download",
      svg_view.status_code == 200 and b"<svg" in svg_view.data
      and "attachment" not in svg_view.headers.get("Content-Disposition", ""))
check("Project Schema page embeds the diagram", "GET /about/schema", "img tag pointing at the SVG",
      'src="/about/schema.svg"' in client.get("/about/schema").get_data(as_text=True))
contact = client.get("/about/contact").get_data(as_text=True)
check("Contact page", "GET /about/contact", "email and repository link present",
      "0h0kgsuss@mozmail.com" in contact and "ef101-fall2026-p11" in contact)
gh = client.get("/github")
check("GitHub redirect", "GET /github", "302 to the repository",
      gh.status_code == 302 and gh.headers.get("Location") == "https://github.com/AbdurRauf-01/ef101-fall2026-p11")
credits = client.get("/about/credits").get_data(as_text=True)
check("Credits content", "GET /about/credits", "all four people listed",
      all(n in credits for n in ["Dr. Fezan Rafique", "Irteza Raza", "Abdur Rauf", "Hassan Majid"]))
privacy = client.get("/about/privacy").get_data(as_text=True)
check("Privacy Policy content", "GET /about/privacy", "effective date and offline statement",
      "October 30, 2026" in privacy and "zero outbound internet requests" in privacy)
check("Sidebar navigation", "GET /dashboard", "Maintenance and About links present",
      'href="/maintenance"' in body and 'href="/about/schema"' in body)
check("Theme toggle and resizer", "GET /dashboard", "controls present",
      'id="theme-toggle"' in body and 'id="resizer"' in body)

# 13. Dashboard stays in sync with the asset data: every change shows up in the figures
base = queries.dashboard_data()
check("Charts add up to total", "current data", "every chart sums to the active-asset total",
      sums_match_total(base))
S = "SYNC-1"
client.post("/assets/new", data={**GOOD, "asset_id": S, "ram_gb": "128", "ram_type": "DDR5",
                                  "gpu": "NVIDIA RTX 4090", "gpu_vram_gb": "24", "purchase_date": "2020-01-01",
                                  "warranty_expiry": "2021-01-01", "location": "Lab 3"})
d1 = queries.dashboard_data()
check("Add asset: RAM chart", "128 GB DDR5 added", "128 GB count +1",
      chart(d1, "RAM (GB)").get("128 GB", 0) == chart(base, "RAM (GB)").get("128 GB", 0) + 1)
check("Add asset: GPU chart", "GPU NVIDIA RTX 4090", "shows the card, count 1",
      chart(d1, "GPU").get("NVIDIA RTX 4090", 0) == 1)
check("Add asset: total", "active assets", "total +1", d1["stats"]["total"] == base["stats"]["total"] + 1)
check("Add asset: warranty", "expired warranty", "out-of-warranty +1 in stat and chart",
      d1["stats"]["out_of_warranty"] == base["stats"]["out_of_warranty"] + 1
      and chart(d1, "Warranty")["Out of warranty"] == chart(base, "Warranty")["Out of warranty"] + 1)
check("Add asset: charts still add up", "after add", "every chart sums to the total", sums_match_total(d1))

client.post(f"/assets/{S}/edit", data={**GOOD, "asset_id": S, "ram_gb": "64", "ram_type": "DDR4", "gpu": "",
                                       "purchase_date": "2020-01-01", "warranty_expiry": "2028-01-01",
                                       "location": "Lab 3"})
d2 = queries.dashboard_data()
check("Edit RAM and GPU: charts move", "128 GB -> 64 GB, GPU -> integrated",
      "old size -1, new size +1, GPU moves to Integrated", chart(d2, "RAM (GB)").get("128 GB", 0) == chart(base, "RAM (GB)").get("128 GB", 0)
      and chart(d2, "RAM (GB)").get("64 GB", 0) == chart(base, "RAM (GB)").get("64 GB", 0) + 1
      and "NVIDIA RTX 4090" not in chart(d2, "GPU")
      and chart(d2, "GPU").get("Integrated", 0) == chart(base, "GPU").get("Integrated", 0) + 1)
check("Edit warranty: charts move", "expiry moved to 2028", "back in warranty",
      d2["stats"]["out_of_warranty"] == base["stats"]["out_of_warranty"]
      and chart(d2, "Warranty")["In warranty"] == chart(base, "Warranty")["In warranty"] + 1)

client.post(f"/assets/{S}/retire")
d3 = queries.dashboard_data()
check("Retire: totals and charts drop it", "retire", "total and 64 GB back to base, retired +1",
      d3["stats"]["total"] == base["stats"]["total"]
      and chart(d3, "RAM (GB)").get("64 GB", 0) == chart(base, "RAM (GB)").get("64 GB", 0)
      and d3["stats"]["retired"] == base["stats"]["retired"] + 1)
client.post(f"/assets/{S}/reactivate")
d4 = queries.dashboard_data()
check("Reactivate: totals and charts restore it", "reactivate", "total +1 again",
      d4["stats"]["total"] == base["stats"]["total"] + 1 and sums_match_total(d4))

client.post(f"/assets/{S}/maintenance", data={"event_date": "2026-02-01", "fault_category": "Other",
                                              "fault_description": "Unexplained reboot",
                                              "action_taken": "Logged, monitored", "technician": "Ali",
                                              "cost": "12", "set_status": "Operational"})
d5 = queries.dashboard_data()
check("Log event: failure chart", "fault category Other", "Other +1 in the failure chart",
      fault(d5)["Other"] == fault(base)["Other"] + 1)
check("Log event: fault events stat", "one more event", "fault events +1 and cost +12",
      d5["stats"]["events"] == base["stats"]["events"] + 1
      and abs(d5["stats"]["total_cost"] - base["stats"]["total_cost"] - 12) < 0.001)
check("Log event: no status change", "set_status blank", "attention unchanged",
      d5["stats"]["attention"] == base["stats"]["attention"])

client.post(f"/assets/{S}/edit", data={**GOOD, "asset_id": S, "ram_gb": "64", "ram_type": "DDR4", "gpu": "",
                                       "purchase_date": "2020-01-01", "warranty_expiry": "2028-01-01",
                                       "location": "Lab 3", "status": "Faulty",
                                       "status_category": "Unknown", "status_detail": "Unknown"})
d6 = queries.dashboard_data()
check("Faulty: stats and attention list", "status Faulty, category Unknown",
      "faulty +1, attention +1, asset listed", d6["stats"]["faulty"] == base["stats"]["faulty"] + 1
      and d6["stats"]["attention"] == base["stats"]["attention"] + 1
      and any(r["asset_id"] == S for r in d6["attention"]))
check("Faulty: charts still add up", "after status change", "every chart sums to the total",
      sums_match_total(d6))
client.post(f"/assets/{S}/maintenance", data={"event_date": "2026-03-20", "fault_category": "Disk failure",
            "fault_description": "Replaced part", "action_taken": "Swapped", "technician": "Ali", "cost": "0",
            "set_status": "Operational"})
d7 = queries.dashboard_data()
check("Back to Operational: stats restore", "status Operational", "faulty and attention back to base",
      d7["stats"]["faulty"] == base["stats"]["faulty"]
      and d7["stats"]["attention"] == base["stats"]["attention"])

# 14. Features added in this round: GPU VRAM, locations, automatic IDs, show-more, (Done) in history
client.post("/assets/new", data={**GOOD, "asset_id": "GPU-1", "gpu": "NVIDIA RTX 4060", "gpu_vram_gb": "8"})
check("GPU VRAM saved", "RTX 4060 with 8 GB", "gpu_vram_gb = 8",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='GPU-1' AND gpu_vram_gb=8") == 1)
check("GPU VRAM shown on detail page", "GET /assets/GPU-1", "GPU VRAM row shows 8 GB",
      "<dt>GPU VRAM</dt><dd>8 GB</dd>" in client.get("/assets/GPU-1").get_data(as_text=True))
client.post("/assets/new", data={**GOOD, "asset_id": "GPU-2", "gpu": "", "gpu_vram_gb": "8"})
check("VRAM ignored without a GPU", "no GPU, VRAM typed", "saved with VRAM NULL",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='GPU-2' AND gpu_vram_gb IS NULL") == 1)

# 14b. Location drop-down, adding a new place, automatic asset IDs
form_page = client.get("/assets/new").get_data(as_text=True)
check("Location drop-down", "GET /assets/new", "existing places and '+ Add new location' offered",
      "<option>Lab 1</option>" in form_page and f'value="{appmod.NEW_LOCATION}"' in form_page)
n_loc = count("SELECT COUNT(*) FROM locations")
n_assets = count("SELECT COUNT(*) FROM assets")
client.post("/assets/new", data={**GOOD, "asset_id": "", "location": appmod.NEW_LOCATION, "new_location": "   "})
check("Blank new location rejected", "'+ Add new location' with no name", "rejected, nothing saved",
      count("SELECT COUNT(*) FROM assets") == n_assets and count("SELECT COUNT(*) FROM locations") == n_loc)
client.post("/assets/new", data={**GOOD, "asset_id": "", "location": appmod.NEW_LOCATION, "new_location": "Lab 4"})
check("Add new location", "'+ Add new location', name Lab 4", "location saved with prefix L4",
      count("SELECT COUNT(*) FROM locations WHERE name='Lab 4' AND prefix='L4'") == 1)
check("Asset in a new place gets an automatic ID", "asset ID left blank", "L4-PC-001 created in Lab 4",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='L4-PC-001' AND location='Lab 4'") == 1)
client.post("/assets/new", data={**GOOD, "asset_id": "", "location": appmod.NEW_LOCATION, "new_location": "lab 4"})
check("Typed name matches an existing place", "'lab 4' typed", "no duplicate place; second asset is L4-PC-002",
      count("SELECT COUNT(*) FROM locations") == n_loc + 1
      and count("SELECT COUNT(*) FROM assets WHERE asset_id='L4-PC-002' AND location='Lab 4'") == 1)
check("Prefix for a new place", "Lab 4 when L4 is taken", "L42", queries.suggest_prefix("Lab 4", {"L4"}) == "L42")
check("Prefix for Server Room", "Server Room", "SR", queries.suggest_prefix("Server Room", set()) == "SR")
exp_id = queries.next_asset_id("LIB")
client.post("/assets/new", data={**GOOD, "asset_id": "", "location": "Library"})
check("Automatic asset ID for an existing place", "Library, ID left blank", f"{exp_id} assigned",
      count("SELECT COUNT(*) FROM assets WHERE asset_id=? AND location='Library'", exp_id) == 1)

d_lab = queries.dashboard_data()
check("New place on the dashboard", "Lab 4 has 2 assets", "Assets per location lists Lab 4 with 2",
      chart(d_lab, "Assets per location").get("Lab 4") == 2)
check("Charts add up after new places", "after new places", "every chart sums to the total",
      sums_match_total(d_lab))

before = chart(queries.dashboard_data(), "Assets per location")
client.post("/assets/GPU-1/edit", data={**GOOD, "gpu": "NVIDIA RTX 4060", "gpu_vram_gb": "8",
                                        "location": appmod.NEW_LOCATION, "new_location": "Lab 5"})
mid = chart(queries.dashboard_data(), "Assets per location")
check("Edit moves an asset to a new place", "GPU-1 moved to new Lab 5", "Lab 5 = 1 and Lab 1 down by 1",
      mid.get("Lab 5") == 1 and mid["Lab 1"] == before["Lab 1"] - 1)
client.post("/assets/GPU-1/edit", data={**GOOD, "gpu": "NVIDIA RTX 4060", "gpu_vram_gb": "8", "location": "Lab 1"})
after = chart(queries.dashboard_data(), "Assets per location")
check("Empty place stays listed", "Lab 5 now empty", "Lab 5 still shown with 0; Lab 1 back to before",
      after.get("Lab 5") == 0 and after["Lab 1"] == before["Lab 1"])
check("Asset ID stays the same after a move", "GPU-1 moved and back", "still GPU-1 (permanent identity)",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='GPU-1' AND location='Lab 1'") == 1)
check("List filter matches dashboard", "GET /assets?location=Lab+4", "rows = dashboard count for Lab 4",
      rows_in("/assets?location=Lab+4") == chart(queries.dashboard_data(), "Assets per location")["Lab 4"])

# 14c. Show all / show top 5 on the dashboard
d_now = queries.dashboard_data()
body_now = client.get("/dashboard").get_data(as_text=True)
long_lists = [rows for rows in d_now["charts"].values() if len(rows) > 5]
if len(d_now["fault_cats"]) > 5:
    long_lists.append(d_now["fault_cats"])
check("Show all on long lists", "every list longer than 5", "one 'Show all N' per long list",
      body_now.count('<details class="more">') == len(long_lists)
      and all(f"Show all {len(r)}" in body_now for r in long_lists))

# 14d. An open fault is steps; one history entry when the PC is Operational again
ev_before = count("SELECT COUNT(*) FROM maintenance")
client.post("/assets/new", data={**GOOD, "asset_id": "HIST-1"})
client.post("/assets/HIST-1/maintenance", data={"event_date": "2026-03-01", "fault_category": "PSU failure",
            "fault_description": "Will not power on", "action_taken": "Replaced faulty PSU", "technician": "Ali",
            "cost": "40", "set_status": "Faulty"})
check("Open fault is not in the history yet", "log fault, status Faulty",
      "no history row; its action is a step",
      count("SELECT COUNT(*) FROM maintenance") == ev_before
      and count("SELECT COUNT(*) FROM fault_steps WHERE asset_id='HIST-1' AND action='Replaced faulty PSU'") == 1)
client.post("/assets/HIST-1/maintenance", data={"event_date": "2026-03-05", "fault_category": "PSU failure",
            "fault_description": "Will not power on", "action_taken": "Swapped in new PSU unit", "technician": "Ali",
            "cost": "0", "set_status": "Operational"})
check("Closing writes one history entry with every step", "log the fix, status Operational",
      "one entry; action lists both steps; cost is the total; steps cleared",
      count("SELECT COUNT(*) FROM maintenance WHERE asset_id='HIST-1'") == 1
      and count("SELECT COUNT(*) FROM maintenance WHERE asset_id='HIST-1' AND action_taken LIKE '%Replaced faulty PSU%' "
                "AND action_taken LIKE '%Swapped in new PSU unit%' AND cost_usd=40") == 1
      and count("SELECT COUNT(*) FROM fault_steps WHERE asset_id='HIST-1'") == 0)
h2 = client.get("/maintenance").get_data(as_text=True)
check("Closed fault is on the Fault History page", "GET /maintenance", "the one entry shows, with (Done)",
      "Swapped in new PSU unit" in h2 and '<span class="done">(Done)</span>' in h2)

client.post("/assets/new", data={**GOOD, "asset_id": "HIST-2"})
client.post("/assets/HIST-2/edit", data={**GOOD, "asset_id": "HIST-2", "status": "Faulty", "status_category": "Display issue",
            "status_detail": "No picture", "status_action": "Check cable", "status_technician": "",
            "fault_date": "2026-03-08", "fault_cost": "0"})
check("Fault set on the Edit form stays open", "Operational -> Faulty on the Edit form",
      "no history row; the action is a step",
      count("SELECT COUNT(*) FROM maintenance WHERE asset_id='HIST-2'") == 0
      and count("SELECT COUNT(*) FROM fault_steps WHERE asset_id='HIST-2' AND action='Check cable'") == 1)
client.post("/assets/HIST-2/edit", data={**GOOD, "asset_id": "HIST-2", "status": "Under Repair", "status_category": "Display issue",
            "status_detail": "No picture", "status_action": "Replaced panel", "status_technician": "",
            "fault_date": "2026-03-12", "fault_cost": "0"})
check("Under Repair on the Edit form needs a technician", "Under Repair, technician blank", "rejected, still Faulty",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='HIST-2' AND status='Faulty'") == 1)
client.post("/assets/HIST-2/edit", data={**GOOD, "asset_id": "HIST-2", "status": "Under Repair", "status_category": "Display issue",
            "status_detail": "No picture", "status_action": "Replaced panel", "status_technician": "Ali",
            "fault_date": "2026-03-12", "fault_cost": "0"})
check("Progress on the Edit form is a step, not history", "Faulty -> Under Repair with a new action",
      "no history row; new step; status updated",
      count("SELECT COUNT(*) FROM maintenance WHERE asset_id='HIST-2'") == 0
      and count("SELECT COUNT(*) FROM fault_steps WHERE asset_id='HIST-2' AND action='Replaced panel'") == 1
      and count("SELECT COUNT(*) FROM assets WHERE asset_id='HIST-2' AND status='Under Repair'") == 1)
steps_now = count("SELECT COUNT(*) FROM fault_steps WHERE asset_id='HIST-2'")
client.post("/assets/HIST-2/edit", data={**GOOD, "asset_id": "HIST-2", "status": "Under Repair", "status_category": "Display issue",
            "status_detail": "No picture", "status_action": "Replaced panel", "status_technician": "Ali",
            "fault_date": "2026-03-12", "fault_cost": "0"})
check("Saving again without changes adds no step", "same data saved twice", "no new step",
      count("SELECT COUNT(*) FROM fault_steps WHERE asset_id='HIST-2'") == steps_now)
client.post("/assets/HIST-2/edit", data={**GOOD, "asset_id": "HIST-2", "status": "Operational", "status_category": "Display issue",
            "status_detail": "No picture", "status_action": "Fitted panel", "status_technician": "",
            "fault_date": "2026-03-20", "fault_cost": "15"})
check("Closing on the Edit form needs every field", "Operational, technician blank", "rejected, still open",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='HIST-2' AND status='Under Repair'") == 1)
client.post("/assets/HIST-2/edit", data={**GOOD, "asset_id": "HIST-2", "status": "Operational", "status_category": "Display issue",
            "status_detail": "No picture", "status_action": "Fitted panel", "status_technician": "Ali",
            "fault_date": "2026-03-20", "fault_cost": "15"})
check("Closing on the Edit form writes one entry", "Operational with every field",
      "one entry with all steps; cost 15; steps cleared",
      count("SELECT COUNT(*) FROM maintenance WHERE asset_id='HIST-2'") == 1
      and count("SELECT COUNT(*) FROM maintenance WHERE asset_id='HIST-2' AND action_taken LIKE '%Check cable%' "
                "AND action_taken LIKE '%Replaced panel%' AND action_taken LIKE '%Fitted panel%' AND cost_usd=15") == 1
      and count("SELECT COUNT(*) FROM fault_steps WHERE asset_id='HIST-2'") == 0)

# 14e. Final sync check: every view agrees with the same data
d_end = queries.dashboard_data()
check("Final: charts add up", "end state", "every chart sums to the total", sums_match_total(d_end))
check("Final: list matches dashboard", "GET /assets", "row count = dashboard total",
      rows_in("/assets") == d_end["stats"]["total"])
check("Final: attention list matches its card", "GET /dashboard", "list length = Needing attention",
      len(d_end["attention"]) == d_end["stats"]["attention"])
exp_csv = list(csv.reader(io.StringIO(client.get("/export/assets.csv").get_data(as_text=True))))
check("Final: assets CSV matches dashboard", "GET /export/assets.csv", "rows = total; has gpu_vram_gb and location",
      len(exp_csv) - 1 == d_end["stats"]["total"] and "gpu_vram_gb" in exp_csv[0] and "location" in exp_csv[0])
mexp = list(csv.reader(io.StringIO(client.get("/export/maintenance.csv").get_data(as_text=True))))
check("Final: maintenance CSV matches history", "GET /export/maintenance.csv", "rows = fault events",
      len(mexp) - 1 == d_end["stats"]["events"])

# 15. IP addresses: unique inside a place, automatic from the asset ID and the place's range
c = db.get_db()
lab1 = c.execute("SELECT ip_address FROM assets WHERE location='Lab 1' AND active=1 AND ip_address IS NOT NULL "
                 "ORDER BY asset_id LIMIT 1").fetchone()[0]
lib_ips = {r[0] for r in c.execute("SELECT ip_address FROM assets WHERE location='Library'")}
lab1_free_in_library = next(r[0] for r in c.execute("SELECT ip_address FROM assets WHERE location='Lab 1' AND ip_address IS NOT NULL ORDER BY asset_id")
                            if r[0] not in lib_ips)
c.close()
n_before = count("SELECT COUNT(*) FROM assets")
client.post("/assets/new", data={**GOOD, "asset_id": "", "location": "Lab 1", "ip_address": lab1})
check("Same IP in the same place rejected", f"Lab 1 PC with {lab1}", "rejected, nothing saved",
      count("SELECT COUNT(*) FROM assets") == n_before)
client.post("/assets/new", data={**GOOD, "asset_id": "", "location": "Library", "ip_address": lab1_free_in_library})
check("Same IP in a different place allowed", f"Library PC with {lab1_free_in_library}", "saved",
      count("SELECT COUNT(*) FROM assets") == n_before + 1)
client.post("/assets/new", data={**GOOD, "asset_id": "LIB-PC-077", "location": "Library", "ip_address": ""})
check("Blank IP stays Not Assigned", "LIB-PC-077, IP blank", "stored as NULL, page shows Not Assigned",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='LIB-PC-077' AND ip_address IS NULL") == 1
      and "Not Assigned" in client.get("/assets/LIB-PC-077").get_data(as_text=True))
check("Place ranges are 1 to 6 for the demo places", "locations table", "Lab 1 = 1, Library = 6",
      count("SELECT ip_subnet FROM locations WHERE name='Lab 1'") == 1
      and count("SELECT ip_subnet FROM locations WHERE name='Library'") == 6)
check("Lab 4 gets the next range (7)", "Lab 4 added earlier", "ip_subnet = 7",
      count("SELECT COUNT(*) FROM locations WHERE name='Lab 4' AND ip_subnet=7") == 1)
check("New place PCs are not auto-assigned an IP", "L4-PC-001 in Lab 4", "IP stays NULL",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='L4-PC-001' AND ip_address IS NULL") == 1)
c = db.get_db()
nxt = c.execute("SELECT COALESCE(MAX(ip_subnet),0)+1 FROM locations").fetchone()[0]
c.close()
client.post("/assets/new", data={**GOOD, "asset_id": "", "location": appmod.NEW_LOCATION, "new_location": "Lab 9"})
check("Next new place gets the next range", "new place 'Lab 9'", f"range {nxt} kept; its PC has no IP",
      count("SELECT COUNT(*) FROM locations WHERE name='Lab 9' AND ip_subnet=?", nxt) == 1
      and count("SELECT COUNT(*) FROM assets WHERE location='Lab 9' AND ip_address IS NULL") == 1)
check("No two PCs share an IP inside a place", "all places", "0 duplicates",
      count("SELECT COUNT(*) FROM (SELECT location, ip_address FROM assets WHERE ip_address IS NOT NULL "
            "GROUP BY location, ip_address HAVING COUNT(*) > 1)") == 0)
c = db.get_db()
pair = c.execute("SELECT a.asset_id, b.asset_id, a.ip_address FROM assets a JOIN assets b "
                 "ON a.location=b.location AND a.asset_id<b.asset_id AND a.ip_address IS NOT NULL LIMIT 1").fetchone()
try:
    c.execute("UPDATE assets SET ip_address=? WHERE asset_id=?", (pair[2], pair[1]))
    c.commit()
    db_refused = False
except sqlite3.IntegrityError:
    c.rollback()
    db_refused = True
c.close()
check("Database also refuses a duplicate IP in a place", f"copy {pair[2]} to {pair[1]}", "IntegrityError",
      db_refused)
client.post("/assets/new", data={**GOOD, "asset_id": "", "location": "Lab 1", "ip_address": "10.0.0.999"})
check("Invalid IP still rejected", "10.0.0.999", "rejected", count("SELECT COUNT(*) FROM assets WHERE ip_address='10.0.0.999'") == 0)

# 16. Sorting, the current fault box and the fault rules on new assets
def ids_in(html):
    return re.findall(r'<a href="/assets/([^"]+)">', html)


def column_values(html, pattern):
    return [m for m in re.findall(pattern, html)]


html = client.get("/assets").get_data(as_text=True)
check("Default sort: Asset ID ascending", "GET /assets", "rows in Asset ID order",
      ids_in(html) == [r[0] for r in count_rows("SELECT asset_id FROM assets WHERE active=1 ORDER BY asset_id")])
html = client.get("/assets?sort=ram&dir=desc").get_data(as_text=True)
rams = [int(x) for x in column_values(html, r"<td>(\d+) GB")]
check("Sort by RAM, descending", "sort=ram&dir=desc", "RAM values never increase down the list",
      rams == sorted(rams, reverse=True) and len(rams) > 1)
check("Descending arrow shown", "sort=ram&dir=desc", "down arrow on RAM heading", "\u25bc" in html)
html = client.get("/assets?sort=faults&dir=asc").get_data(as_text=True)
faults = [int(x) for x in column_values(html, r"</td>\s*<td>(\d+)</td>\s*</tr>")]
check("Sort by faults, ascending", "sort=faults&dir=asc", "fault counts never decrease",
      faults == sorted(faults) and len(faults) > 1)
html = client.get("/assets?sort=DROP%20TABLE").get_data(as_text=True)
check("Unknown sort column ignored", "sort=DROP TABLE", "page loads in default order",
      html and ids_in(html) == [r[0] for r in count_rows("SELECT asset_id FROM assets WHERE active=1 ORDER BY asset_id")])
form_new = client.get("/assets/new").get_data(as_text=True)
check("Fault box on the Add form", "GET /assets/new", "box present, category defaults to Unknown",
      'id="fault-box"' in form_new and "<option selected>Unknown</option>" in form_new
      and "Actions to be taken" in form_new and 'name="status_technician"' in form_new)
client.post("/assets/new", data={**GOOD, "asset_id": "FLT-1", "status": "Faulty", "status_category": "",
                                 "status_detail": "", "status_action": "", "status_technician": ""})
check("New faulty asset needs no fault details", "status Faulty, every fault field blank", "saved as Unknown; not in history until closed",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='FLT-1' AND status='Faulty' AND status_category='Unknown'") == 1
      and count("SELECT COUNT(*) FROM maintenance WHERE asset_id='FLT-1'") == 0)
client.post("/assets/new", data={**GOOD, "asset_id": "OPS-1", "status": "Operational"})
check("Operational asset shows dashes", "GET /assets/OPS-1", "a row of '-' in every column",
      "<td>-</td><td>-</td><td>-</td><td>-</td><td>-</td>" in client.get("/assets/OPS-1").get_data(as_text=True))
check("Operational asset has no current fault in the database", "OPS-1", "all fault fields NULL",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='OPS-1' AND status_category IS NULL AND status_date IS NULL") == 1)

# 17. Status sorting, Not Assigned IPs, and the Log form rules
STATUS_CLASS = {"Operational": "Operational", "Under Repair": "Under-Repair", "Faulty": "Faulty"}
for first in ["Operational", "Under Repair", "Faulty"]:
    html = client.get("/assets?sort=status&first=" + first.replace(" ", "+")).get_data(as_text=True)
    expect = [r[0] for r in count_rows(
        "SELECT asset_id FROM assets WHERE active=1 ORDER BY CASE WHEN status='" + first + "' THEN 0 ELSE 1 END, status, asset_id")]
    check(f"Status sort: {first} first", f"sort=status, first={first}", "that status first, rest alphabetical by status",
          ids_in(html) == expect)
    check(f"Status arrow colour: {first}", f"sort=status, first={first}", f"arrow st-{STATUS_CLASS[first]}",
          f'arrow st-{STATUS_CLASS[first]}' in html)
html = client.get("/assets?sort=status&first=Operational").get_data(as_text=True)
check("Status heading cycles to Under Repair next", "heading link while first=Operational",
      "link asks for first=Under Repair", re.search(r'sort=status&amp;first=Under|first=Under', html) is not None)
client.post("/assets/new", data={**GOOD, "asset_id": "RULE-1", "status": "Operational"})
rules_before = count("SELECT COUNT(*) FROM maintenance WHERE asset_id='RULE-1'")
client.post("/assets/RULE-1/maintenance", data={"event_date": "2026-03-02", "fault_category": "RAM fault",
            "fault_description": "Checked", "action_taken": "", "technician": "Ali", "cost": "5", "set_status": "Operational"})
check("Operational event needs the action", "Operational, action blank", "rejected",
      count("SELECT COUNT(*) FROM maintenance WHERE asset_id='RULE-1'") == rules_before)
client.post("/assets/RULE-1/maintenance", data={"event_date": "2026-03-02", "fault_category": "RAM fault",
            "fault_description": "Checked", "action_taken": "Reseated", "technician": "Ali", "cost": "", "set_status": "Operational"})
check("Operational event: cost may be left empty", "Operational, cost blank", "saved with cost 0",
      count("SELECT COUNT(*) FROM maintenance WHERE asset_id='RULE-1' AND cost_usd=0") == rules_before + 1)
detail = client.get("/assets/RULE-1").get_data(as_text=True)
check("Log form status is required with a choose option", "GET /assets/RULE-1",
      "'-- choose --' option first in the status drop-down",
      '<select name="set_status" id="ev-status">\n    <option value="">— choose —</option>' in detail
      or '<option value="">— choose —</option>\n    <option>Operational</option>' in detail)
check("Not Assigned shown for a blank IP", "GET /assets/RULE-1", "Not Assigned in the hardware card",
      "<dd>Not Assigned</dd>" in detail)

# 18. Several steps while a PC is being fixed: one history entry with all the steps
client.post("/assets/new", data={**GOOD, "asset_id": "STEP-1"})
client.post("/assets/STEP-1/maintenance", data={"event_date": "2026-04-01", "fault_category": "", "fault_description": "",
            "action_taken": "Step A", "technician": "", "cost": "0", "set_status": "Faulty"})
check("Faulty step is saved, not history", "Faulty, action Step A", "step saved; no history row",
      count("SELECT COUNT(*) FROM fault_steps WHERE asset_id='STEP-1' AND action='Step A'") == 1
      and count("SELECT COUNT(*) FROM maintenance WHERE asset_id='STEP-1'") == 0)
client.post("/assets/STEP-1/maintenance", data={"event_date": "2026-04-02", "fault_category": "RAM fault",
            "fault_description": "Memory errors", "action_taken": "Step B", "technician": "Ali",
            "cost": "10", "set_status": "Under Repair"})
client.post("/assets/STEP-1/edit", data={**GOOD, "asset_id": "STEP-1", "status": "Under Repair", "status_category": "RAM fault",
            "status_detail": "Memory errors", "status_action": "Step B", "status_technician": "Ali",
            "fault_date": "2026-04-03", "fault_cost": "0"})
check("Unchanged action on the Edit form adds nothing", "Step B again, cost 0", "no new step",
      count("SELECT COUNT(*) FROM fault_steps WHERE asset_id='STEP-1'") == 2)
client.post("/assets/STEP-1/edit", data={**GOOD, "asset_id": "STEP-1", "status": "Under Repair", "status_category": "RAM fault",
            "status_detail": "Memory errors", "status_action": "Step B", "status_technician": "Ali",
            "fault_date": "2026-04-03", "fault_cost": "5"})
check("A cost alone is a step without repeating the action", "same action, cost 5", "cost-only step (action blank)",
      count("SELECT COUNT(*) FROM fault_steps WHERE asset_id='STEP-1' AND action='' AND cost_usd=5") == 1)
client.post("/assets/STEP-1/maintenance", data={"event_date": "2026-04-05", "fault_category": "RAM fault",
            "fault_description": "Memory errors", "action_taken": "Step C", "technician": "Ali",
            "cost": "2", "set_status": "Operational"})
rows = count("SELECT COUNT(*) FROM maintenance WHERE asset_id='STEP-1'")
text = client.get("/assets/STEP-1").get_data(as_text=True)
check("Closing combines every step into one entry", "close with Step C, cost 2", "one entry; Step B listed once; cost 17",
      rows == 1
      and count("SELECT COUNT(*) FROM maintenance WHERE asset_id='STEP-1' AND action_taken LIKE '%Step A%' "
                "AND action_taken LIKE '%Step C%' AND cost_usd=17") == 1
      and count("SELECT COUNT(*) FROM maintenance WHERE asset_id='STEP-1' AND action_taken LIKE '%Step B%'") == 1
      and count("SELECT COUNT(*) FROM fault_steps WHERE asset_id='STEP-1'") == 0)
check("Closed PC shows dashes in Current fault", "GET /assets/STEP-1", "a row of '-'",
      "<td>-</td><td>-</td><td>-</td><td>-</td><td>-</td>" in text)

# ---- report ----
w = [max(len(str(r[i])) for r in results + [("Test", "Input", "Expected", "Result")]) for i in range(4)]
line = lambda r: " | ".join(str(x).ljust(w[i]) for i, x in enumerate(r))
print(line(("Test", "Input", "Expected", "Result")))
print("-+-".join("-" * x for x in w))
for r in results:
    print(line(r))
fails = [r for r in results if r[3] == "FAIL"]
print(f"\n{len(results) - len(fails)}/{len(results)} passed")
os.remove(TMP)
sys.exit(1 if fails else 0)
