"""Cross-platform launcher used by start.* and reset-demo-data.* (standard library only).

    python scripts/launcher.py start    # set up venv + dependencies, start the app, open the browser
    python scripts/launcher.py reset    # set up venv + dependencies, replace the data with the demo data (seed.py)

Options:  --no-browser (start)   --yes (reset: skip the confirmation question)

Setup is idempotent: the virtual environment (venv/) and the packages in requirements.txt are only
created/installed when missing or out of date. Your data (inventory.db) is never touched by `start`.
"""
import hashlib
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / "venv"
REQS = ROOT / "requirements.txt"
STAMP = VENV / ".requirements.sha256"
MIN_PYTHON = (3, 8)
IS_WINDOWS = os.name == "nt"
VENV_PY = VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
DB_PATH = Path(os.environ.get("INVENTORY_DB") or (ROOT / "inventory.db"))


def say(msg=""):
    print(msg, flush=True)


def fail(msg):
    say(f"\nERROR: {msg}")
    sys.exit(1)


def venv_works():
    if not VENV_PY.exists():
        return False
    try:
        return subprocess.run([str(VENV_PY), "-c", "import sys"], capture_output=True).returncode == 0
    except OSError:
        return False


def ensure_venv():
    if venv_works():
        return
    if VENV.exists():
        # Broken, or made on another OS (the folder travels with the project). Only remove a real venv.
        if (VENV / "pyvenv.cfg").exists():
            say("Existing venv/ does not work on this computer - recreating it.")
            shutil.rmtree(VENV)
        else:
            fail(f"{VENV} exists but is not a virtual environment. Rename or delete it and run again.")
    say("Creating virtual environment (venv/) ...")
    r = subprocess.run([sys.executable, "-m", "venv", str(VENV)])
    if r.returncode != 0 or not venv_works():
        if VENV.exists() and (VENV / "pyvenv.cfg").exists():
            shutil.rmtree(VENV, ignore_errors=True)
        fail("Could not create the virtual environment. On Ubuntu/Debian install it with: "
             "sudo apt install python3-venv python3-pip")


def requirements_hash():
    return hashlib.sha256(REQS.read_bytes()).hexdigest()


def deps_ok():
    if subprocess.run([str(VENV_PY), "-c", "import flask"], capture_output=True).returncode != 0:
        return False
    try:
        return STAMP.read_text().strip() == requirements_hash()
    except OSError:
        return False


def ensure_deps():
    if deps_ok():
        return
    say("Installing dependencies from requirements.txt ...")
    r = subprocess.run([str(VENV_PY), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQS)])
    if r.returncode != 0:
        fail("Dependency install failed. Check your internet connection and run again.")
    STAMP.write_text(requirements_hash())


def setup():
    if sys.version_info < MIN_PYTHON:
        fail("Python %d.%d or newer is required (found %s)." % (MIN_PYTHON + (sys.version.split()[0],)))
    try:
        import sqlite3 as _s  # noqa: F401
    except ImportError:
        fail("This Python was built without SQLite support (the sqlite3 module). Install a standard Python.")
    ensure_venv()
    ensure_deps()


def port_is_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def open_browser_when_ready(port):
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):  # up to ~20 seconds
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    else:
        return
    try:
        webbrowser.open(url)
    except Exception:
        pass


def start(no_browser):
    port = next((p for p in range(5000, 5100) if port_is_free(p)), None)
    if port is None:
        fail("No free port found between 5000 and 5099.")
    if not no_browser:
        threading.Thread(target=open_browser_when_ready, args=(port,), daemon=True).start()
    say("Starting the Hardware Inventory ... (close this window or press Ctrl+C to stop)\n")
    try:
        return subprocess.call([str(VENV_PY), "app.py", "--port", str(port)], cwd=str(ROOT))
    except KeyboardInterrupt:
        return 0


def asset_count():
    if not DB_PATH.exists():
        return 0
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            return conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def reset(assume_yes):
    existing = asset_count()
    backup = None
    if DB_PATH.exists() and DB_PATH.stat().st_size > 0:
        say(f"Data file: {DB_PATH}")
        if existing and not assume_yes:
            say(f"It holds {existing} assets. Resetting ERASES them and loads the demo data (40 assets).")
            say("A backup copy of the current data is saved next to it first.")
            if input("Type YES to continue: ").strip() != "YES":
                say("Cancelled. Nothing was changed.")
                return 0
        backup = DB_PATH.with_name(f"{DB_PATH.stem}.before-reset-{datetime.now():%Y%m%d-%H%M%S}{DB_PATH.suffix}")
        shutil.copy2(DB_PATH, backup)
    # Same as `python seed.py`, minus its own YES question (already answered above).
    r = subprocess.run([str(VENV_PY), "-c", "import seed; seed.main()"], cwd=str(ROOT))
    if r.returncode != 0:
        if backup:
            say(f"Seeding failed. Your previous data is still in {backup.name}.")
        return r.returncode
    if backup:
        say(f"Previous data backed up as {backup.name} (delete it if you do not need it).")
    say("Done. Demo data restored.")
    return 0


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    if len(args) != 1 or args[0] not in ("start", "reset"):
        fail("usage: launcher.py start|reset [--no-browser] [--yes]")
    os.chdir(ROOT)
    setup()
    if args[0] == "start":
        return start("--no-browser" in flags or bool(os.environ.get("NO_BROWSER")))
    return reset("--yes" in flags)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
