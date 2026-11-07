"""Generate DEMO data (not real assets). Run: python seed.py
Re-running wipes and recreates inventory.db. Uses a fixed random seed so
results are reproducible. Faults are deliberately concentrated on a few
assets and categories so the 'most frequently failing' result is meaningful.
"""
import random
from datetime import date, timedelta

from db import get_db, init_db

random.seed(101)
# Fixed reference date (not date.today()) so the demo data is identical on every run and every day.
TODAY = date(2026, 10, 9)

# Places, and the short code used for their asset IDs (order matters: it keeps the demo reproducible).
from db import DEFAULT_PLACES
LOCATION_PREFIX = DEFAULT_PLACES
LOCATIONS = list(LOCATION_PREFIX)
# Range number x of each place's IP range 192.168.x.n, in creation order (Lab 1 -> 1 ... Library -> 6).
LOCATION_SUBNET = {name: i + 1 for i, name in enumerate(LOCATION_PREFIX)}

# GPU memory in GB for the demo GPUs (integrated machines have no VRAM value).
GPU_VRAM = {"NVIDIA GTX 1650": 4, "NVIDIA RTX 3060": 12, "NVIDIA RTX 3070": 8}

# (manufacturer, model, cpu, cpu_family, ram, storage_type, storage_gb, gpu, os)
CONFIGS = [
    ("Dell", "OptiPlex 3090", "Intel Core i5-10505", "Core i5", 8, "SSD", 256, "", "Windows 10 Pro"),
    ("Dell", "OptiPlex 7010", "Intel Core i7-12700", "Core i7", 16, "NVMe", 512, "NVIDIA GTX 1650", "Windows 11 Pro"),
    ("HP", "ProDesk 400 G7", "Intel Core i3-10100", "Core i3", 4, "HDD", 500, "", "Windows 10 Pro"),
    ("HP", "EliteDesk 800 G6", "Intel Core i7-10700", "Core i7", 16, "SSD", 512, "", "Windows 11 Pro"),
    ("Lenovo", "ThinkCentre M70s", "Intel Core i5-12400", "Core i5", 16, "NVMe", 512, "", "Windows 11 Pro"),
    ("Lenovo", "ThinkPad T14", "AMD Ryzen 5 PRO 5650U", "Ryzen 5", 16, "NVMe", 512, "", "Windows 11 Pro"),
    ("Lenovo", "ThinkCentre M900", "Intel Core i5-6500", "Core i5", 8, "HDD", 1000, "", "Windows 10 Pro"),
    ("Acer", "Veriton X", "Intel Core i3-9100", "Core i3", 8, "HDD", 1000, "", "Ubuntu 22.04"),
    ("ASUS", "ExpertCenter D7", "AMD Ryzen 7 5700G", "Ryzen 7", 32, "NVMe", 1000, "NVIDIA RTX 3060", "Windows 11 Pro"),
    ("Custom", "Lab Workstation", "AMD Ryzen 9 5900X", "Ryzen 9", 64, "NVMe", 2000, "NVIDIA RTX 3070", "Ubuntu 22.04"),
]
FAULTS = {
    "Disk failure": ("Drive reporting SMART errors / not detected", "Replaced drive and reinstalled OS", 55),
    "Overheating": ("Random shutdowns under load", "Cleaned fans, replaced thermal paste", 15),
    "PSU failure": ("Will not power on", "Replaced power supply unit", 40),
    "RAM fault": ("Blue screens, memtest errors", "Replaced RAM module", 30),
    "OS corruption": ("Fails to boot, boot loop", "Reinstalled operating system", 0),
    "Network issue": ("No network / limited connectivity", "Replaced NIC cable and reset adapter", 10),
    "Display issue": ("No video output", "Reseated GPU, replaced cable", 12),
}
TECHS = ["A. Khan", "S. Ahmed", "R. Ali"]

# Current error (category, details) given to the Faulty / Under Repair demo assets, in order.
CURRENT_ERRORS = [
    ("RAM fault", "Intermittent errors, memtest failing"),
    ("Unknown", "Unknown"),
    ("Disk failure", "Drive not detected by BIOS, replacement ordered"),
    ("Overheating", "Overheating under load, fan replacement pending"),
    ("Display issue", "No video output, GPU being checked"),
    ("Unknown", "Unknown"),
]


def rand_date(start, end):
    return start + timedelta(days=random.randint(0, (end - start).days))


def main():
    init_db()
    conn = get_db()
    conn.executemany("INSERT INTO locations (name,prefix,ip_subnet) VALUES (?,?,?)",
                     [(n, p, LOCATION_SUBNET[n]) for n, p in LOCATION_PREFIX.items()])
    assets = []
    for i in range(1, 41):
        c = random.choice(CONFIGS)
        loc = random.choice(LOCATIONS)
        aid = f"{LOCATION_PREFIX[loc]}-PC-{i:03d}"
        purchase = rand_date(date(2019, 1, 1), date(2025, 12, 31))
        warranty_years = random.choice([1, 3, 3, 3, 5])
        try:
            expiry = purchase.replace(year=purchase.year + warranty_years)
        except ValueError:
            expiry = purchase.replace(year=purchase.year + warranty_years, day=28)
        random.randint(1, 4)        # draws kept so the rest of the demo data does not change
        random.randint(10, 250)
        ip = f"192.168.{LOCATION_SUBNET[loc]}.{i}"    # same pattern as the ID: L1-PC-004 -> 192.168.1.4
        assets.append([aid, c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7], c[8], ip,
                       loc, purchase.isoformat(), expiry.isoformat(), "Operational", 1])

    # A few assets are the "problem machines" (many faults). Old HDD machines fail most.
    hdd_assets = [a for a in assets if a[6] in ("HDD",)]
    problem = random.sample(hdd_assets, min(3, len(hdd_assets)))
    problem_ids = {a[0] for a in problem}

    events = []
    for a in assets:
        aid = a[0]
        if aid in problem_ids:
            n = random.randint(4, 6)
        else:
            n = random.choice([0, 0, 0, 1, 1, 2])
        purchase = date.fromisoformat(a[12])
        for _ in range(n):
            if aid in problem_ids or a[6] == "HDD":
                cat = random.choices(list(FAULTS), weights=[6, 2, 2, 1, 2, 1, 0], k=1)[0]
            else:
                cat = random.choice(list(FAULTS))
            desc, action, cost = FAULTS[cat]
            d = rand_date(max(purchase, date(2022, 1, 1)), TODAY)
            events.append([aid, d.isoformat(), cat, desc, action,
                           random.choice(TECHS), cost + random.choice([0, 0, 5, 10])])

    # Statuses: a handful currently faulty / under repair
    candidates = [a for a in assets if a[0] in problem_ids] + random.sample(assets, 6)
    for a in candidates[:3]:
        a[14] = "Faulty"
    for a in candidates[3:6]:
        a[14] = "Under Repair"
    # Retire two old assets (kept in DB, hidden from default list)
    for a in sorted(assets, key=lambda x: x[12])[:2]:
        a[15] = 0
        a[14] = "Faulty"

    # Current fault: every Faulty / Under Repair asset gets one; Operational assets stay NULL (shown as "-").
    # Open faults are not history yet (they become one history entry when the PC is Operational again).
    current = {}
    for n, a in enumerate(x for x in assets if x[14] != "Operational"):
        cat, detail = CURRENT_ERRORS[n % len(CURRENT_ERRORS)]
        current[a[0]] = (cat, detail)
    rows = []
    for a in assets:
        cur = current.get(a[0])
        # RAM type: every CPU in the demo list supports DDR4, so DDR4 is used for all demo assets.
        ram_type = "DDR4"
        vram = GPU_VRAM.get(a[8]) if a[8] else None
        cat, detail = cur if cur else (None, None)
        # an open fault goes into the history only when it is closed; Under Repair always has a technician
        tech = (TECHS[int(a[0].split("-")[-1]) % len(TECHS)] if a[14] == "Under Repair" else "") if cur else None
        action, when = ("", TODAY.isoformat()) if cur else (None, None)
        cost = 0 if cur else None
        rows.append([a[0], a[1], a[2], a[3], a[4], a[5], ram_type, a[6], a[7], a[8], vram, a[9], a[10],
                     a[11], a[12], a[13], a[14], cat, detail, action, tech, when, cost, a[15]])
    conn.executemany(
        "INSERT INTO assets (asset_id,manufacturer,model,cpu,cpu_family,ram_gb,ram_type,storage_type,"
        "storage_gb,gpu,gpu_vram_gb,os,ip_address,location,purchase_date,warranty_expiry,status,status_category,"
        "status_detail,status_action,status_technician,status_date,status_cost,active) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.executemany(
        "INSERT INTO maintenance (asset_id,event_date,fault_category,fault_description,"
        "action_taken,technician,cost_usd) VALUES (?,?,?,?,?,?,?)", events)
    conn.commit()
    conn.close()
    print(f"Seeded {len(assets)} demo assets and {len(events)} maintenance events.")


if __name__ == "__main__":
    from db import DB_PATH, has_tables
    if has_tables():
        conn = get_db()
        existing = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        conn.close()
        if existing:
            answer = input(f"{DB_PATH} already holds {existing} assets. Demo data ERASES them. Type YES to continue: ")
            if answer.strip() != "YES":
                print("Cancelled. Nothing was changed.")
                raise SystemExit(0)
    main()
