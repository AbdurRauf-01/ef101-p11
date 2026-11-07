# EF101-P11 Team split: Database, Backend, Frontend

Deadline: Monday 9 Nov 2026. The code is already built and tested (`python test_app.py` = 36/36 passed). Your job is to **own, understand, verify and make one small change** in your part, then explain it in the viva.

**Golden rule:** edit only your own files. That keeps GitHub free of merge conflicts. If you need a change in someone else's file, ask them.

| | Member A: Database | Member B: Backend | Member C: Frontend |
|---|---|---|---|
| Files | `schema.sql`, `db.py`, `queries.py`, `seed.py`, `inventory.db` | `app.py`, `test_app.py` | `templates/` (7 files), `static/style.css` |
| Size | ~330 lines | ~430 lines | ~280 lines |
| Report sections | 4 Data design, 7 Results | 5 Main code segments, 6 Testing | 1 Problem, 2 Architecture, 3 Technology, 8 Limitations |
| Extra | Demo dataset deliverable, data/storage description | README run steps, test evidence | Final report PDF + Classroom upload, demo video (optional), figures |

Put your real name and ID in the contribution table (section 9 of the report) and in the README disclosure. Be honest about what you wrote, changed and checked yourself.

---

## Member A: Database

**Files:** `schema.sql`, `db.py`, `queries.py`, `seed.py`, `inventory.db`

**What the code does**
- `schema.sql`: creates two tables, `assets` (primary key `asset_id`) and `maintenance` (foreign key `asset_id`), with `CHECK` rules (RAM > 0, allowed status and storage values, cost >= 0) and an index.
- `db.py`: `get_db()` opens `inventory.db` with foreign keys switched on; `init_db()` runs `schema.sql`.
- `queries.py`: **every SQL statement in the project.** `filtered_assets` (search and filters), `filter_options`, `insert_asset`, `update_asset`, `set_active` (retire = `active=0`), `get_events`, `add_event`, `all_events`, `dashboard_data` (COUNT, GROUP BY, JOIN) and `attention_sql` (the needs-attention rule, constants at the top).
- `seed.py`: builds 40 demo assets and 43 maintenance events with a fixed random seed. Faults are concentrated on a few assets so "most failing" is meaningful.

**What you must do**
1. Run `python seed.py`, then open `inventory.db` in DB Browser for SQLite. Write down both tables, their keys and the relationship.
2. In DB Browser, **Execute SQL** tab, write these 5 queries yourself and check they match the dashboard: total active assets (38), out of warranty (24), needs attention (7); the last two can change after today because warranty is computed from the current date, most failing asset (`L1-PC-009`, 6 events), top fault category (Disk failure, 14). Screenshot each result: this is your testing evidence for "dashboard numbers match the data".
3. **Your own change:** add `CHECK (warranty_expiry >= purchase_date)` to the `assets` table in `schema.sql`. Re-run `python seed.py` and `python test_app.py`. If tests fail, undo the change and tell the team.
4. Practise the viva demo: close the app, delete `inventory.db`, start `python app.py` and read the error page, then run `python seed.py` to recover.
5. Export `assets.csv` and `maintenance.csv` from the app and keep them as the "demo dataset" deliverable.
6. Write report sections 4 and 7 (the skeleton already has the tables and figures; check every number against your own queries).

**Viva questions you must answer**
- Asset ID vs IP address? (ID is permanent identity; IP can change.)
- Why retire instead of delete? (History and the foreign key stay valid; failure statistics need old records.)
- What is a foreign key / primary key / `CHECK`?
- Why `?` in SQL? (Values travel separately from the SQL text, so input cannot inject SQL.)
- What happens if `inventory.db` is deleted? Why SQLite, and what is its limit? (One file, no server; few simultaneous users.)

---

## Member B: Backend

**Files:** `app.py`, `test_app.py`

**What the code does**
- `app.py` contains **no SQL**; it calls `queries.py`.
- `validate_asset()` checks every field on the server: required values, positive numbers, valid IP, valid dates, purchase date not in the future, warranty not before purchase, duplicate ID rejected.
- `warranty_status()` computes Active / Expiring soon / Expired from `warranty_expiry`. `parse_date()` and `today()` are helpers.
- Routes: `/` and `/dashboard`, `/assets`, `/assets/new`, `/assets/<id>`, `/assets/<id>/edit`, `/assets/<id>/retire`, `/assets/<id>/reactivate`, `/assets/<id>/maintenance`, `/export/assets.csv`, `/export/maintenance.csv`.
- `csv_response()` builds the CSV download. Two error handlers show a friendly 404 page and a database-problem page.
- `test_app.py` runs 36 checks on a temporary database (your real data is never touched) and prints a results table.

**What you must do**
1. Trace every route on paper: URL, function in `app.py`, function in `queries.py` it calls, template it renders. Put it in a table (it goes in report section 5).
2. Run `python test_app.py`. Be able to explain one test from each group: validation, retire/reactivate, filters, export.
3. **Your own change:** add one validation rule in `validate_asset()` (for example RAM must be 1024 GB or less, with a clear message). Add a matching line to the `bad_cases` dictionary in `test_app.py`, run the tests, and screenshot the rejected form.
4. Capture manual test evidence with screenshots: blank field rejected, duplicate asset ID, invalid IP, a filter result, the CSV opened in LibreOffice Calc or Excel.
5. Change `ATTENTION_FAULT_COUNT` (top of `queries.py`, coordinate with Member A) from 3 to 2, watch the dashboard number change, then change it back. Be ready to do this live.
6. Write report sections 5 and 6 (shorten the test table to the 8 to 10 most important rows; the full run stays in the repository).

**Viva questions you must answer**
- What happens when you press Save? (The six steps in the schema image.)
- GET vs POST? Why validate on the server and not only in the browser?
- What is a route? What does Flask do?
- Where is the needs-attention rule, and how do you change it?
- What needs the Internet? (Nothing.) Name one limitation (no login).

---

## Member C: Frontend

**Files:** `templates/base.html`, `assets.html`, `asset_form.html`, `asset_detail.html`, `dashboard.html`, `message.html`, `static/style.css`

**What the code does**
- `base.html`: the page frame: navigation bar, flash messages (green success, red error), and `{% block content %}` that the other pages fill in with `{% extends "base.html" %}`.
- `assets.html`: the filter form (sent as GET, so filters appear in the URL) and the asset table with status and warranty badges.
- `asset_form.html`: one form used for both add and edit (the `new` variable switches the title and makes the ID read-only on edit).
- `asset_detail.html`: asset information, maintenance history table, log-event form, retire/reactivate buttons.
- `dashboard.html`: stat cards, bar charts, most-failing tables. The bars are plain CSS: each bar's width is `{{ w }}%` computed by the backend (no chart library).
- `static/style.css`: all styling, with colours defined once at the top (`:root`).
- Jinja basics to know: `{{ value }}` prints, `{% for %}` loops, `{% if %}` branches, `url_for()` builds links.

**What you must do**
1. Click through every page and take clean screenshots for the report: dashboard, asset list with a filter applied, add form with an error, asset detail with history.
2. Map each page to its template and the data `app.py` passes in (for example `/assets` passes `assets`, `opts`, `f`).
3. **Your own change:** add an OS column to the asset table in `assets.html` (one `<th>OS</th>` in the header row and one `<td>{{ a.os }}</td>` in the same position in each row), and change one colour variable in `:root` of `style.css`. Check all pages still look right.
4. Own the final report: assemble the sections from A and B, insert the schema image from `docs/project-schema.png` as Figure 1, export to PDF, check the page count (short report), and upload to Google Classroom.
5. Optional: record a 2 to 3 minute demo video (add an asset, log a fault, filter, dashboard, export).
6. Write report sections 1, 2, 3 and 8.

**Viva questions you must answer**
- How does the browser get the dashboard charts? (CSS bar widths calculated from the data, no Internet or library.)
- What is a template, and why does `base.html` exist?
- Why does the form use POST for saving but GET for filtering?
- How are the filter drop-downs filled? (`filter_options()` in `queries.py` returns the distinct values.)
- What happens if a user types bad data? (The backend rejects it and the form is shown again with messages.)

---

## Everyone

**GitHub:** Member A creates the repository and adds B and C under Settings, Collaborators. Then each member clones it once:
```
git clone <repo URL>
cd ef101-p11
```
Every time you finish work (replace the file names with your own):
```
git pull --rebase
git add schema.sql db.py queries.py seed.py inventory.db     # Member A
git add app.py test_app.py                                   # Member B
git add templates static                                     # Member C
git commit -m "describe what you changed"
git push
```

**Plan**

| Dates | Do |
|---|---|
| Fri 30 Oct to Sun 1 Nov | Install, run, run tests. Read your own files completely. |
| Mon 2 to Wed 4 Nov | Make your own change, collect your screenshots and evidence, write your report sections. |
| Thu 5 Nov | Each member explains their part to the others for 15 minutes. Ask each other the viva questions. |
| Fri 6 to Sat 7 Nov | Member C assembles the report. Everyone reviews it. Clone the repo into a fresh folder and run it. |
| Sun 8 Nov | Final check: `python seed.py` and `python test_app.py`, push, upload the report. Laptop ready. |
| Mon 9 Nov | Submission. |

**Be ready for live changes in the viva:** add a record, change a filter, change the needs-attention rule, enter bad data and show the validation.
