# EF101-P11 Hardware Asset Inventory

Flask + SQLite web app that tracks computer assets (specs, location, warranty, status) and their maintenance/fault history, with a dashboard and CSV export. All data in `inventory.db` is **DEMO data**, not real assets.

## Project schema
![Project schema](docs/project-schema.png)

## Run
Start the app with `python app.py`, or double-click `start.command` (macOS) / `start.bat` (Windows).
The app prints its address (http://127.0.0.1:5000, or the next free port if 5000 is busy) and the location of its data file.

- **Your data is kept in this folder**, in `inventory.db`. Launching never deletes it. To move the project, move the whole folder, data included.
- **First launch:** an empty database is created with the six standard places. Add assets from the app.
- **Demo data (optional):** `python seed.py` replaces the data with 40 demo assets. It asks you to type YES first when there is data.
- **Several copies at once:** each copy has its own data file and its own address, so you can run an older version (for example v7) next to this one to compare.
- **Tests:** `python test_app.py` runs 171 checks on a temporary database and prints a results table.

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
If you already have an `inventory.db` from an earlier version (version 8 or later), run `python migrate.py` once. It removes incomplete history entries (a blank or Unknown field), turns valid entries logged while a fault was open into its steps, and lists anything it could not resolve. It copies every row into the current structure, keeps a backup as `inventory.db.bak`, and reports any IP addresses it had to assign.

## Rules to document in the report
- **Cost**: *Original Cost* is what has been spent on an open fault so far (read-only; it is the sum of its steps). *Increase in Cost* is the new spend, which is added as a step. On a Log form for an Operational PC, *Original Cost* is the cost of the event. Closing the fault writes one history entry whose cost is the Original Cost plus the Increase.
- **IP addresses**: optional. A blank IP is shown as Not Assigned; nothing is assigned automatically. Two PCs in the same place can never share an IP (a PC's IP is checked against the others in its place); different places may share one.
- **Needs attention** (active assets only): status is Faulty or Under Repair, and nothing else. Past fault events are listed separately in the dashboard's Fault history, whatever the status.
- **Current fault**: while a PC is Faulty or Under Repair, its fault (category, description, actions to be taken, technician, date) is shown in its Current fault section. Nothing is written to the history while the fault is open. Under Repair requires a technician. A blank category is saved as Unknown.
- **Steps of an open fault**: each save with a new action, or with a cost, adds a step (its date, action, technician and cost). The steps are listed under Current fault. Saving the same action again adds nothing.
- **Closing a fault**: the PC is closed by setting it to Operational. Every fault field (category other than Unknown, description, action taken, technician) and the date are required; the cost is optional. The fault is then written to the history as ONE entry: its Action taken lists every step with its date, and its cost is the total of all steps. A history entry is never written for an open fault. This applies on the Log form and on the Edit form.
- **Sorting**: the asset list sorts by any column (click a heading; click again to reverse). The Status heading cycles which status comes first: Operational (green), Under Repair (yellow), Faulty (red); the other statuses follow alphabetically. The default is Asset ID, ascending.
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
