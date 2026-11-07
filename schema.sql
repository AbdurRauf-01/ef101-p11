-- EF101-P11 Hardware Asset Inventory: database schema (SQLite)
-- locations (1) -- (N) assets (1) -- (N) maintenance
-- Running this file wipes and recreates all three tables.

DROP TABLE IF EXISTS maintenance;
DROP TABLE IF EXISTS assets;
DROP TABLE IF EXISTS locations;

CREATE TABLE locations (
    name    TEXT PRIMARY KEY,                 -- shown in the drop-down, e.g. Lab 1
    prefix  TEXT NOT NULL UNIQUE              -- start of automatic asset IDs, e.g. L1 -> L1-PC-001
);

CREATE TABLE assets (
    asset_id        TEXT PRIMARY KEY,                 -- permanent identity, e.g. L1-PC-001
    manufacturer    TEXT NOT NULL,
    model           TEXT NOT NULL,
    cpu             TEXT NOT NULL,                    -- full model, e.g. Intel Core i5-12400
    cpu_family      TEXT NOT NULL,                    -- grouping used for filtering, e.g. Core i5
    ram_gb          INTEGER NOT NULL CHECK (ram_gb > 0),
    ram_type        TEXT NOT NULL CHECK (ram_type IN ('DDR1','DDR2','DDR3','DDR4','DDR5')),
    storage_type    TEXT NOT NULL CHECK (storage_type IN ('SSD','HDD','NVMe','Hybrid')),
    storage_gb      INTEGER NOT NULL CHECK (storage_gb > 0),
    gpu             TEXT,                             -- NULL / empty = integrated only
    gpu_vram_gb     INTEGER CHECK (gpu_vram_gb IS NULL OR gpu_vram_gb > 0),  -- GPU memory; NULL = no discrete GPU
    os              TEXT NOT NULL,
    ip_address      TEXT,                             -- changeable network address (NOT identity)
    location        TEXT NOT NULL REFERENCES locations(name),
    purchase_date   TEXT NOT NULL,                    -- ISO date YYYY-MM-DD
    warranty_expiry TEXT NOT NULL,                    -- warranty status is COMPUTED from this
    status          TEXT NOT NULL DEFAULT 'Operational'
                    CHECK (status IN ('Operational','Faulty','Under Repair')),
    status_category TEXT,                             -- current error category; NULL when Operational (shown N/A)
    status_detail   TEXT,                             -- current error details; NULL when Operational
    active          INTEGER NOT NULL DEFAULT 1,       -- 0 = retired (kept for history)
    -- an Operational asset has no current error; a Faulty / Under Repair asset must state one
    CHECK ((status = 'Operational' AND status_category IS NULL AND status_detail IS NULL)
        OR (status <> 'Operational' AND status_category IS NOT NULL AND status_detail IS NOT NULL))
);

CREATE TABLE maintenance (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id          TEXT NOT NULL REFERENCES assets(asset_id),
    event_date        TEXT NOT NULL,
    fault_category    TEXT NOT NULL,                  -- one of the fault category drop-down options
    fault_description TEXT NOT NULL,
    action_taken      TEXT NOT NULL,
    technician        TEXT NOT NULL,
    cost_usd          REAL NOT NULL DEFAULT 0 CHECK (cost_usd >= 0)   -- US dollars
);

CREATE INDEX idx_maint_asset ON maintenance(asset_id);
