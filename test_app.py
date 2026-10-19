"""Automated test evidence for EF101-P11. Run: python test_app.py
Uses a temporary database (your inventory.db is never touched) and prints a
Test | Input | Expected | Result table you can paste into the report."""
import csv
import io
import os
import sys
import tempfile
from pathlib import Path

import db

TMP = Path(tempfile.mkdtemp()) / "test.db"
db.DB_PATH = TMP                       # redirect everything to a throwaway database
import seed                            # noqa: E402
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


GOOD = dict(asset_id="TEST-001", manufacturer="Dell", model="OptiPlex 7090", cpu="Intel Core i5-12400",
            cpu_family="Core i5", ram_gb="16", storage_type="SSD", storage_gb="512", gpu="",
            os="Windows 11 Pro", ip_address="192.168.1.50", location="Lab 1",
            purchase_date="2025-01-10", warranty_expiry="2028-01-10", status="Operational")

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

# 6. Log maintenance + history
ev = dict(event_date="2026-01-05", fault_category="Disk failure", fault_description="Clicking noise",
          action_taken="Replaced drive", technician="Ali", cost="45.5", set_status="Under Repair")
client.post("/assets/TEST-001/maintenance", data=ev)
check("Log maintenance event", "valid event", "row saved",
      count("SELECT COUNT(*) FROM maintenance WHERE asset_id='TEST-001'") == 1)
check("Event updates status", "set_status=Under Repair", "asset status changed",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='TEST-001' AND status='Under Repair'") == 1)
page = client.get("/assets/TEST-001").get_data(as_text=True)
check("Asset history page", "GET /assets/TEST-001", "shows the event", "Clicking noise" in page)
client.post("/assets/TEST-001/maintenance", data={**ev, "cost": "-5", "fault_description": "neg"})
client.post("/assets/TEST-001/maintenance", data={**ev, "event_date": "2999-01-01", "fault_description": "future"})
check("Invalid event rejected", "negative cost / future date", "nothing saved",
      count("SELECT COUNT(*) FROM maintenance WHERE asset_id='TEST-001'") == 1)

# 7. Retire (not delete) and reactivate
client.post("/assets/TEST-001/retire")
check("Retire asset", "POST retire", "active=0, row and history kept",
      count("SELECT COUNT(*) FROM assets WHERE asset_id='TEST-001' AND active=0") == 1
      and count("SELECT COUNT(*) FROM maintenance WHERE asset_id='TEST-001'") == 1)
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

# 9. Dashboard numbers match the data
dash = client.get("/dashboard")
body = dash.get_data(as_text=True)
total_active = count("SELECT COUNT(*) FROM assets WHERE active=1")
check("Dashboard loads", "GET /dashboard", "HTTP 200", dash.status_code == 200)
check("Dashboard total", "active assets", f"shows {total_active}", f">{total_active}<" in body)

# 10. Exports
a = client.get("/export/assets.csv")
m = client.get("/export/maintenance.csv")
arows = list(csv.reader(io.StringIO(a.get_data(as_text=True))))
mrows = list(csv.reader(io.StringIO(m.get_data(as_text=True))))
check("Export assets CSV", "GET /export/assets.csv", "header + one row per active asset",
      "csv" in a.mimetype and len(arows) - 1 == total_active)
check("Export maintenance CSV", "GET /export/maintenance.csv", "header + one row per event",
      len(mrows) - 1 == count("SELECT COUNT(*) FROM maintenance"))

# 11. Robustness
check("Unknown asset", "GET /assets/NOPE", "404 page, no crash", client.get("/assets/NOPE").status_code == 404)
check("Unknown URL", "GET /xyz", "404 page", client.get("/xyz").status_code == 404)

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
