@echo off
REM Double-click on Windows: installs what is missing (Python, packages), then starts the Hardware Inventory.
REM Your data stays in this folder (inventory.db) and is never deleted by launching.
call "%~dp0scripts\bootstrap.bat" start
