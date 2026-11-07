# EF101-P11 Hardware Asset Inventory

Flask + SQLite web app that tracks computer assets (specs, location, warranty, status) and their maintenance/fault history, with a dashboard and CSV export. All data in `inventory.db` is **DEMO data**, not real assets.

## Project schema
![Project schema](docs/project-schema.png)

## Run
```
pip install flask
python seed.py        # optional: wipes and recreates inventory.db with demo data
python app.py         # open http://127.0.0.1:5000
python test_app.py    # runs 120 checks on a temporary database, prints a results table
```
No Internet is needed: the app uses no external libraries (plain CSS in `static/style.css`; dashboard charts are CSS bars).

## Files
| File | Purpose |
|---|---|
| `app.py` | Backend: routes, input validation, CSV export (no SQL) |
| `queries.py` | Database layer: every SQL statement (filters, saves, dashboard figures) |
| `db.py` | SQLite connection + schema creation |
| `schema.sql` | `assets` and `maintenance` tables (1 asset : many events) |
| `seed.py` | Demo data generator (fixed random seed, reproducible) |
| `test_app.py` | Test evidence (add, edit, retire, invalid input, duplicates, filters, history, dashboard, export) |
| `templates/`, `static/` | HTML (Jinja) pages and the stylesheet |

## Rules to document in the report
- **Needs attention** (active assets only): status is Faulty or Under Repair, and nothing else. Past fault events are listed separately in the dashboard's Fault history, whatever the status.
- **Current error**: a Faulty or Under Repair asset must choose an error category (the fault list plus N/A, Unknown, Other) and write the details (or `Unknown`). Operational assets show N/A. Set on the edit form or when logging an event.
- **Fault categories**: maintenance events use a fixed drop-down (Disk failure, Overheating, Network issue, RAM fault, OS corruption, Display issue, PSU failure, Other, Unknown). The failure chart always shows every category; anything unexpected is counted as Other.
- **Locations**: a place is chosen from a drop-down, or added with **+ Add new location** (it then appears on the dashboard, even while empty). Each place has a short code (for example Lab 4 = `L4`).
- **Asset IDs**: optional. If left blank, the next free ID for the chosen place is assigned (for example `LIB-PC-038`). Retired IDs are never reused.
- **GPU VRAM**: required (GB) whenever a GPU is listed; integrated machines show N/A.
- **Fault history**: every fault event is listed, whatever the asset's status. Once an asset is Operational again, its events show (Done). Status changed on the Edit form is not a history row; record faults with Log event.
- **RAM type**: DDR1 to DDR5 drop-down, required for every asset (12th to 14th gen Intel CPUs can take DDR4 or DDR5, so the type is recorded per machine).
- **Dashboard charts** are computed from the same active-asset query as the totals, so the charts always add up to the total.
- **Warranty label**: `WARRANTY_SOON_DAYS` (30) at the top of `queries.py` sets the Expiring soon window.
- **Warranty status** is computed from `warranty_expiry` (Active / Expiring soon / Expired), never stored.
- **Retire** sets `active = 0`; the row and its history are kept. Reactivate reverses it.
- **Validation**: required fields, RAM/storage positive integers, valid IP, valid dates, purchase date not in the future, warranty not before purchase, duplicate asset ID rejected, event cost_usd >= 0.
- All SQL uses `?` placeholders (no string-built values), so search input cannot inject SQL.

## Tool and AI disclosure (edit before submitting)
Built with Python, Flask, SQLite, Jinja templates and plain CSS. Code was drafted with help from Claude (Anthropic) and reviewed, tested and understood by all team members. List exactly which parts and what each member changed.

## Limitations
No login or user roles; data entry is manual; SQLite suits one site and few concurrent users; demo data is synthetic.
