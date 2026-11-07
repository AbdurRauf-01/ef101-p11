@echo off
REM Double-click on Windows: starts the Hardware Inventory from this folder (data stays in this folder).
cd /d "%~dp0"
python app.py
pause
