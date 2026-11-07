# EF101-P11 Hardware Asset Inventory

Flask + SQLite web app that tracks computer assets (specs, location, warranty, status) and their maintenance/fault history, with a dashboard and CSV export. All data in `inventory.db` is **DEMO data**, not real assets.

## Project schema
![Project schema](docs/project-schema.png)

## Run
```
pip install flask
python seed.py        # optional: wipes and recreates inventory.db with demo data
python app.py         # open http://127.0.0.1:5000
python test_app.py    # runs 142 checks on a temporary database, prints a results table
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
| `migrate.py` | Upgrades an existing `inventory.db` to the current version without losing data (backup kept) |
| `test_app.py` | Test evidence (add, edit, retire, invalid input, duplicates, filters, history, dashboard, export) |
| `templates/`, `static/` | HTML (Jinja) pages and the stylesheet |

## Upgrading an existing database
If you already have an `inventory.db` from an earlier version (version 6 or later), run `python migrate.py` once. It copies every row into the current structure, keeps a backup as `inventory.db.bak`, and reports any IP addresses it had to assign.

## Rules to document in the report
- **IP addresses**: each place has a range number x, so its PCs use 192.168.x.n. Places are numbered in the order they were created (Lab 1 = 1 ... Library = 6); a new place gets the next number. Two PCs in the same place can never share an IP; different places may. If the IP box is left blank, the system uses the number at the end of the asset ID as the host number (L1-PC-004 -> 192.168.1.4), or the lowest free number if that one is taken.
- **Needs attention** (active assets only): status is Faulty or Under Repair, and nothing else. Past fault events are listed separately in the dashboard's Fault history, whatever the status.
- **Current fault**: while an asset is Faulty or Under Repair, its fault (category, description, actions to be taken, technician, date) is shown in its Current fault section. All of these are optional on the Edit/Add form; a blank category is saved as Unknown. Each fault is also a history entry.
- **Closing a fault**: the fix is logged on the asset's Log form (fault category other than Unknown, description, action and technician required). The fault details are pre-filled from the Current fault section. Setting a faulty asset to Operational on the Edit form keeps the fault open and moves it to that Log form.
- **Sorting**: the asset list sorts by any column (click a heading; click again to reverse). The default is Asset ID ascending.
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
