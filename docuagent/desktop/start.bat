@echo off
setlocal
cd /d "%~dp0"

if not exist "..\web-next\index.html" (
    echo [1/3] Building DocuAgent frontend...
    call npm.cmd --prefix ..\frontend ci
    if errorlevel 1 exit /b 1
    pushd ..
    call npm.cmd --prefix frontend run build
    if errorlevel 1 exit /b 1
    popd
)

if not exist "node_modules\electron\dist\electron.exe" (
    echo [2/3] Installing Electron...
    call npm.cmd install
    if errorlevel 1 exit /b 1
)

echo [3/3] Starting DocuAgent desktop...
call npm.cmd start
