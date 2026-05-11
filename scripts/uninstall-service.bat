@echo off
setlocal
set SERVICE_NAME=steprtool

where nssm >nul 2>&1
if errorlevel 1 (
    echo ERROR: nssm.exe not found on PATH.
    exit /b 1
)

nssm stop %SERVICE_NAME%
nssm remove %SERVICE_NAME% confirm
echo Service %SERVICE_NAME% removed.
endlocal
