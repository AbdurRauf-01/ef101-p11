#!/usr/bin/env bash
# Fedora / Ubuntu: installs what is missing (Python, packages), then starts the Hardware Inventory.
# Your data stays in this folder (inventory.db) and is never deleted by launching.
cd "$(dirname "$0")" || exit 1
exec bash scripts/bootstrap.sh start
