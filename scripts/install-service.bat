@echo off
REM ============================================================================
REM steprtool service installer (uses NSSM, the Non-Sucking Service Manager).
REM
REM Prerequisites:
REM   1. Python 3.10+ installed and on PATH.
REM   2. NSSM installed and on PATH (download from https://nssm.cc/).
REM   3. A virtual environment created at .\.venv with deps installed:
REM        py -m venv .venv
REM        .\.venv\Scripts\python.exe -m pip install -r requirements.txt
REM   4. .env present in the project root (copy from .env.example).
REM
REM Run this script from an Administrator command prompt.
REM ============================================================================

setlocal

set SERVICE_NAME=steprtool
set ROOT=%~dp0..
pushd "%ROOT%"
set ROOT=%CD%
popd

set PYTHON_EXE=%ROOT%\.venv\Scripts\python.exe
set RUN_SCRIPT=%ROOT%\run.py

if not exist "%PYTHON_EXE%" (
    echo ERROR: %PYTHON_EXE% not found.
    echo Create the venv first:  py -m venv .venv ^&^& .venv\Scripts\python -m pip install -r requirements.txt
    exit /b 1
)
if not exist "%ROOT%\.env" (
    echo ERROR: .env not found in %ROOT%.
    echo Copy .env.example to .env and edit it before installing the service.
    exit /b 1
)

where nssm >nul 2>&1
if errorlevel 1 (
    echo ERROR: nssm.exe not found on PATH.
    echo Install NSSM from https://nssm.cc/ and ensure it is on PATH.
    exit /b 1
)

echo Installing service %SERVICE_NAME%...
nssm install %SERVICE_NAME% "%PYTHON_EXE%" "%RUN_SCRIPT%"
if errorlevel 1 (
    echo nssm install failed.
    exit /b 1
)

nssm set %SERVICE_NAME% AppDirectory   "%ROOT%"
nssm set %SERVICE_NAME% DisplayName    "Jefferson ARC StepIR Antenna Control"
nssm set %SERVICE_NAME% Description    "Web server controlling Step 100 and DCU-2 via serial."
nssm set %SERVICE_NAME% Start          SERVICE_AUTO_START
nssm set %SERVICE_NAME% AppStdout      "%ROOT%\logs\service.out.log"
nssm set %SERVICE_NAME% AppStderr      "%ROOT%\logs\service.err.log"
nssm set %SERVICE_NAME% AppRotateFiles 1
nssm set %SERVICE_NAME% AppRotateBytes 2000000

echo.
echo Service %SERVICE_NAME% installed.
echo Start with:   sc start %SERVICE_NAME%
echo Stop with:    sc stop %SERVICE_NAME%
echo Uninstall:    .\scripts\uninstall-service.bat

endlocal
