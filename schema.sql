-- EF101-P11 Hardware Asset Inventory: database schema (SQLite)
-- One asset has many maintenance events (1 : N via maintenance.asset_id).
-- Running this file wipes and recreates both tables.

DROP TABLE IF EXISTS maintenance;
DROP TABLE IF EXISTS assets;

CREATE TABLE assets (
    asset_id        TEXT PRIMARY KEY,                 -- permanent identity, e.g. LAB1-PC-001
    manufacturer    TEXT NOT NULL,
    model           TEXT NOT NULL,
    cpu             TEXT NOT NULL,                    -- full model, e.g. Intel Core i5-12400
    cpu_family      TEXT NOT NULL,                    -- grouping used for filtering, e.g. Core i5
    ram_gb          INTEGER NOT NULL CHECK (ram_gb > 0),
    storage_type    TEXT NOT NULL CHECK (storage_type IN ('SSD','HDD','NVMe','Hybrid')),
    storage_gb      INTEGER NOT NULL CHECK (storage_gb > 0),
    gpu             TEXT,                             -- NULL / empty = integrated only
    os              TEXT NOT NULL,
    ip_address      TEXT,                             -- changeable network address (NOT identity)
    location        TEXT NOT NULL,
    purchase_date   TEXT NOT NULL,                    -- ISO date YYYY-MM-DD
    warranty_expiry TEXT NOT NULL,                    -- warranty status is COMPUTED from this
    status          TEXT NOT NULL DEFAULT 'Operational'
                    CHECK (status IN ('Operational','Faulty','Under Repair')),
    active          INTEGER NOT NULL DEFAULT 1        -- 0 = retired (kept for history)
);

CREATE TABLE maintenance (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id         TEXT NOT NULL REFERENCES assets(asset_id),
    event_date       TEXT NOT NULL,
    fault_category   TEXT NOT NULL,
    fault_description TEXT NOT NULL,
    action_taken     TEXT NOT NULL,
    technician       TEXT NOT NULL,
    cost             REAL NOT NULL DEFAULT 0 CHECK (cost >= 0)
);

CREATE INDEX idx_maint_asset ON maintenance(asset_id);
