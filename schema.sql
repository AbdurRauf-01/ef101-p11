-- EF101-P11 Computer Hardware Inventory and Maintenance System: database schema (SQLite)
-- locations (1) -- (N) assets (1) -- (N) maintenance
-- assets (1) -- (N) fault_steps: the work done while a fault is open (one history entry is written when it closes)
-- The cost of an open fault is ONE number: assets.status_cost (Updated Cost). Steps carry no cost.
-- Running this file wipes and recreates all tables.

DROP TABLE IF EXISTS fault_steps;
DROP TABLE IF EXISTS maintenance;
DROP TABLE IF EXISTS assets;
DROP TABLE IF EXISTS locations;

CREATE TABLE locations (
    name      TEXT PRIMARY KEY,                 -- shown in the drop-down, e.g. Lab 1
    prefix    TEXT NOT NULL UNIQUE,             -- start of automatic asset IDs, e.g. L1 -> L1-PC-001
    ip_subnet INTEGER NOT NULL UNIQUE           -- x in the place's IP range 192.168.x.n (1..254)
              CHECK (ip_subnet BETWEEN 1 AND 254)
);

CREATE TABLE assets (
    asset_id          TEXT PRIMARY KEY,                 -- permanent identity, e.g. L1-PC-001
    manufacturer      TEXT NOT NULL,
    model             TEXT NOT NULL,
    cpu               TEXT NOT NULL,                    -- full model, e.g. Intel Core i5-12400
    cpu_family        TEXT NOT NULL,                    -- grouping used for filtering, e.g. Core i5
    ram_gb            INTEGER NOT NULL CHECK (ram_gb > 0),
    ram_type          TEXT NOT NULL CHECK (ram_type IN ('DDR1','DDR2','DDR3','DDR4','DDR5')),
    storage_type      TEXT NOT NULL CHECK (storage_type IN ('SSD','HDD','NVMe','Hybrid')),
    storage_gb        INTEGER NOT NULL CHECK (storage_gb > 0),
    gpu               TEXT,                             -- NULL / empty = integrated only
    gpu_vram_gb       INTEGER CHECK (gpu_vram_gb IS NULL OR gpu_vram_gb > 0),
    os                TEXT NOT NULL,
    ip_address        TEXT,                             -- 192.168.x.n; unique inside a place
    location          TEXT NOT NULL REFERENCES locations(name),
    purchase_date     TEXT NOT NULL,                    -- ISO date YYYY-MM-DD
    warranty_expiry   TEXT NOT NULL,                    -- warranty status is COMPUTED from this
    status            TEXT NOT NULL DEFAULT 'Operational'
                      CHECK (status IN ('Operational','Faulty','Under Repair')),
    -- Current fault: filled while status is Faulty / Under Repair, all NULL when Operational
    status_category   TEXT,                             -- fault category, 'Unknown' if not given
    status_detail     TEXT,                             -- fault description ('' = not given)
    status_action     TEXT,                             -- actions to be taken ('' = not decided)
    status_technician TEXT,                             -- technician ('' = not assigned yet)
    status_date       TEXT,                             -- date the fault was recorded (ISO)
    status_cost       REAL CHECK (status_cost IS NULL OR status_cost >= 0), -- updated cost of the open fault
    active            INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),  -- 0 = retired (kept for history)
    CHECK ((status = 'Operational' AND status_category IS NULL AND status_detail IS NULL
            AND status_action IS NULL AND status_technician IS NULL AND status_date IS NULL
            AND status_cost IS NULL)
        OR (status <> 'Operational' AND status_category IS NOT NULL AND status_detail IS NOT NULL
            AND status_action IS NOT NULL AND status_technician IS NOT NULL AND status_date IS NOT NULL)),
    CHECK (warranty_expiry >= purchase_date),
    UNIQUE (location, ip_address)                       -- no two PCs in one place share an IP
);

CREATE TABLE maintenance (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id          TEXT NOT NULL REFERENCES assets(asset_id),
    event_date        TEXT NOT NULL,
    fault_category    TEXT NOT NULL,                  -- one of the fault category drop-down options
    fault_description TEXT NOT NULL,                  -- may be '' when not given
    action_taken      TEXT NOT NULL,                  -- may be '' when not given
    technician        TEXT NOT NULL,                  -- may be '' when not given
    cost_usd          REAL NOT NULL DEFAULT 0 CHECK (cost_usd >= 0)   -- US dollars
);

CREATE INDEX idx_maint_asset ON maintenance(asset_id);

-- Steps of an open fault (Faulty / Under Repair). They are not history yet: when the PC is Operational
-- again, all steps are combined into the Action taken of ONE maintenance entry and this table is emptied.
-- A step has no cost: the cost of the open fault is the single Updated Cost in assets.status_cost.
CREATE TABLE fault_steps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id    TEXT NOT NULL REFERENCES assets(asset_id),
    step_date   TEXT NOT NULL,                        -- ISO date of the step
    action      TEXT NOT NULL,                        -- what was done
    technician  TEXT NOT NULL                         -- who did it ('' = not recorded)
);

CREATE INDEX idx_steps_asset ON fault_steps(asset_id);
