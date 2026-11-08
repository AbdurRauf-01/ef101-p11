# Changelog

## v18 (8 Nov 2026)

Review and hardening release. No change to the data model's meaning; the shipped `inventory.db` has the same data. Tests: 229 of 229 pass.

### Fixed
- Dates typed in other formats (for example 10/01/2025) are now saved as ISO `YYYY-MM-DD`, so the warranty-after-purchase rule always compares correctly.
- Costs that are `nan`, infinite or above 1,000,000,000 are rejected.
- Asset IDs are limited to letters, digits, `- . _` and 40 characters; duplicate IDs are detected ignoring upper/lower case.
- Each text box is limited to 500 characters; location names to 40; request size to 64 KB.
- A new place typed on the Add/Edit form is kept when the form is shown again after an error.
- Repeated identical error messages are shown once.
- Retire and Reactivate on a missing asset now return a 404 page.
- Editing a fault with a blank Updated Cost keeps the current total (a typed 0 is still a real 0).
- Search: `%` and `_` typed by the user are matched literally.
- "Today" for warranty and date checks uses the local date.
- Warranty status shows Unknown (with its own colour) instead of crashing on an unreadable date.
- Database connections are closed after every query (Python 3.13 ResourceWarning).
- Asset ID numbering ignores non-ASCII digits.
- The Fault History "Action taken" column shows "Not recorded" when empty.
- Removed an unused constant in `queries.py`.

### Security
- Requests with a Host other than 127.0.0.1 or localhost are refused (DNS rebinding).
- Form posts from another site are refused (uses `Sec-Fetch-Site`, falls back to `Origin`).
- Added Content-Security-Policy, X-Content-Type-Options, X-Frame-Options and `Referrer-Policy: same-origin`.
- Debug mode is off unless `FLASK_DEBUG=1`; the secret key is random per start.
- CSV export: cells starting with `=`, `+`, `-` or `@` are prefixed with an apostrophe; files start with a UTF-8 byte-order mark.
- Removed the Google Fonts import and preconnect links; the app makes no outside requests, matching the Privacy page.
- Friendly pages for 400, 405, 413, 500 and database constraint errors.

### Database and upgrade
- `assets.active` now has `CHECK (active IN (0,1))`; `inventory.db` regenerated from `seed.py` (same data).
- Startup checks the schema and tells the user to run `migrate.py` when an old database is found.
- `migrate.py` stops cleanly and leaves the original database untouched if copying fails.
- `scripts/bootstrap.sh` (macOS) downloads into a temporary folder.

### Interface
- Form labels are linked to their inputs on the asset form, asset page, fault, step and history edit pages and the filter form.
- The GPU memory (VRAM) field is built without inserting raw HTML.

### Tests
- About 37 new checks (229 in total) for the items above, including a live-server and browser run with no console errors and no outside requests.

### Documentation
- GitHub link updated to `https://github.com/AbdurRauf-01/ef101-p11` in the app and About pages.
- README and TEAM_SPLIT corrected: test counts, four tables, 14 templates, validation and security notes, Fault History description, launcher notes.
- Schema diagram (`docs/project-schema.svg` and `.png`) no longer shows a test count.
- Report updated to version 18.
- Project title now matches the handbook ("Computer Hardware Inventory and Maintenance System") in the README, `schema.sql` heading, schema diagram and report.
- Validation messages now use readable field names ("CPU is required.", "Operating system is required.", "Storage (GB) must be...") instead of "Cpu", "Os" and "STORAGE GB". One more test (229 in total).
