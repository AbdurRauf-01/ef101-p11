#!/usr/bin/env bash
# Fedora / Ubuntu: replaces ALL data with the 40 demo assets (asks first, keeps a backup copy).
cd "$(dirname "$0")" || exit 1
exec bash scripts/bootstrap.sh reset
