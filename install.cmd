@echo off
:: ─────────────────────────────────────────────────────────────────────────────
:: Mistral Laptop Assistant — Windows CMD Installer
:: ─────────────────────────────────────────────────────────────────────────────
:: Usage (drop into your cloned folder or run directly):
::   install.cmd
::   install.cmd YOUR_API_KEY
:: ─────────────────────────────────────────────────────────────────────────────

setlocal EnableDelayedExpansion

set "APP_DIR=%~dp0"
set "APP_DIR=%APP_DIR:~0,-1%"

echo.
echo ▸ Checking for uv...
where uv >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo   Installing uv...
    powershell -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
    :: Refresh PATH for this session
    for /f "tokens=2*" %%a in ('reg query HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment /v Path 2^>nul') do set "MachinePath=%%b"
    for /f "tokens=2*" %%a in ('reg query HKCU\Environment /v Path 2^>nul') do set "UserPath=%%b"
    set "PATH=%MachinePath%;%UserPath%;%PATH%"
)
echo   [OK] uv found

echo.
echo ▸ Installing dependencies with uv sync...
cd /d "%APP_DIR%"
call uv sync
if %ERRORLEVEL% neq 0 (
    echo   FAILED: uv sync returned %ERRORLEVEL%
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo ▸ Configuring API key...

:: Check if key already set
findstr /C:"MLA_MISTRAL_API_KEY=" "%APP_DIR%\.env" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    :: .env doesn't exist, create from example
    if exist "%APP_DIR%\.env.example" (
        copy "%APP_DIR%\.env.example" "%APP_DIR%\.env" >nul 2>&1
    ) else (
        echo MLA_MISTRAL_API_KEY= > "%APP_DIR%\.env"
    )
)

:: Check current key value
for /f "tokens=1,* delims==" %%a in ('findstr /C:"MLA_MISTRAL_API_KEY=" "%APP_DIR%\.env" 2^>nul') do set "CURRENT_KEY=%%b"
if defined CURRENT_KEY (
    echo   [OK] API key already configured
) else (
    echo.
    echo   Get your free API key at: https://console.mistral.ai/
    echo.
    if "%~1"=="" (
        set /p PASTED_KEY="   Paste your MLA_MISTRAL_API_KEY: "
    ) else (
        set PASTED_KEY=%~1
    )
    if defined PASTED_KEY (
        powershell -NoProfile -Command "(Get-Content '%APP_DIR%\.env') -replace 'MLA_MISTRAL_API_KEY=.*','MLA_MISTRAL_API_KEY=%PASTED_KEY%' | Set-Content '%APP_DIR%\.env'"
        echo   [OK] API key saved to .env
    ) else (
        echo   No key entered — add MLA_MISTRAL_API_KEY to .env and run again.
    )
)

echo.
echo ▸ Starting server...
start "" cmd /c "cd /d "%APP_DIR%" && uv run mla --open"
echo.
echo   Done! Browser should open at http://127.0.0.1:8000
echo.
pause