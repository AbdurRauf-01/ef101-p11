#!/bin/bash
# Double-click on macOS: starts the Hardware Inventory from this folder (data stays in this folder).
cd "$(dirname "$0")" || exit 1
python3 app.py
