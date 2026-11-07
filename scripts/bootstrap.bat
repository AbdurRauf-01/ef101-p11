@echo off
REM Windows bootstrap. Usage: bootstrap.bat start|reset
REM Makes sure Python 3.8+ exists (installing it per-user if not), then hands over to launcher.py.
setlocal
set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=start"
set "HERE=%~dp0"
cd /d "%~dp0.."
set "PYVER=3.12.10"
set "PYCMD="

REM Fast path: a working venv already exists.
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" -c "import sys" >nul 2>&1
    if not errorlevel 1 set PYCMD="%CD%\venv\Scripts\python.exe"
)
if defined PYCMD goto run

call :find_python
if defined PYCMD goto run

echo Python 3.8 or newer was not found. Installing it for your user account...
where winget >nul 2>&1
if errorlevel 1 goto download
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
call :find_python
if defined PYCMD goto run

:download
echo Downloading Python %PYVER% from python.org...
set "INSTALLER=%TEMP%\python-%PYVER%-installer.exe"
if exist "%INSTALLER%" del /q "%INSTALLER%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-amd64.exe' -OutFile '%INSTALLER%'; $s=Get-AuthenticodeSignature '%INSTALLER%'; if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'Python Software Foundation') { Remove-Item '%INSTALLER%' -Force; throw 'Installer signature check failed' }"
if errorlevel 1 goto failed
echo Installing Python (no admin rights needed)...
"%INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=0 Include_test=0
del /q "%INSTALLER%" >nul 2>&1
call :find_python
if defined PYCMD goto run

:failed
echo.
echo ERROR: Could not install Python automatically.
echo Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^) and run this again.
goto end

:run
%PYCMD% "%HERE%launcher.py" %ACTION%

:end
echo.
pause
endlocal
exit /b

:find_python
REM Sets PYCMD to a working Python 3.8+ command, or leaves it empty.
set "PYCMD="
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)" >nul 2>&1
if not errorlevel 1 (
    set PYCMD=py -3
    exit /b 0
)
python -c "import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)" >nul 2>&1
if not errorlevel 1 (
    set PYCMD=python
    exit /b 0
)
REM Just installed: PATH of this window is not refreshed yet, so look in the usual per-user locations.
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%~D\python.exe" (
        "%%~D\python.exe" -c "import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)" >nul 2>&1
        if not errorlevel 1 set PYCMD="%%~D\python.exe"
    )
)
exit /b 0
