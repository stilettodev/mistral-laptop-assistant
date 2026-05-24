# ─────────────────────────────────────────────────────────────────────────────
# Mistral Laptop Assistant — PowerShell installer for Windows
# ─────────────────────────────────────────────────────────────────────────────
# Usage:
#   irm https://yourrepo/install.ps1 | iex
#   .\install.ps1
#
# What it does:
#   1. Installs uv (if not present)
#   2. Runs uv sync
#   3. Creates .env from .env.example
#   4. Prompts for API key
#   5. Starts the server and opens the browser
# ─────────────────────────────────────────────────────────────────────────────

$ErrorActionPreference = "Stop"
$APP_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $APP_DIR) { $APP_DIR = $PWD }
$REPO = Split-Path -Leaf $APP_DIR

function Write-Info($msg) { Write-Host "  $msg" -ForegroundColor Cyan }
function Write-Ok($msg)  { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Step($msg){ Write-Host "`n▸ $msg" -ForegroundColor White -NoNewline; Write-Host "" }

# ── Install uv ────────────────────────────────────────────────────────────────
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Step "Installing uv (Astral's fast Python package manager)…"
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    # Refresh path for this session
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $UV_INSTALL = "$env:LOCALAPPDATA\Programs\uv\uv.exe"
    if (Test-Path $UV_INSTALL) { $env:Path += ";$((Split-Path $UV_INSTALL))" }
}
Write-Ok "uv found"

# ── Install deps ─────────────────────────────────────────────────────────────
Write-Step "Installing dependencies with uv sync…"
Set-Location $APP_DIR
& uv sync
if ($LASTEXITCODE -ne 0) { exit 1 }

# ── Configure API key ──────────────────────────────────────────────────────────
$ENV_FILE = Join-Path $APP_DIR ".env"
if (-not (Test-Path $ENV_FILE)) {
    $EXAMPLE = Join-Path $APP_DIR ".env.example"
    if (Test-Path $EXAMPLE) { Copy-Item $EXAMPLE $ENV_FILE }
}

Write-Step "Configuring API key…"
Write-Host ""
Write-Host "  🤖  Get your free API key at: https://console.mistral.ai/" -ForegroundColor Yellow
Write-Host ""
$KeyLine = Select-String -Path $ENV_FILE -Pattern "MLA_MISTRAL_API_KEY=" -ErrorAction SilentlyContinue
$CurrentKey = ""
if ($KeyLine) {
    $CurrentKey = ($KeyLine -split "=", 2)[1].Trim()
}
if (-not $CurrentKey) {
    $PastKey = Read-Host "  Paste your MLA_MISTRAL_API_KEY (hidden)" -AsSecureString
    $PastKey = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($PastKey)).Trim()
    if ($PastKey) {
        $Body = "MLA_MISTRAL_API_KEY=$PastKey"
        $Extra = Read-Host "  Add fallback keys (comma-separated, or Enter to skip)"
        if ($Extra -and $Extra.Trim()) {
            $Body += "`nMLA_MISTRAL_API_KEYS=$($Extra.Trim())"
        }
        Set-Content -Path $ENV_FILE -Value $Body -NoNewline
        Write-Ok "API key saved to .env"
    }
} else {
    Write-Ok "API key already configured"
}

# ── Launch ────────────────────────────────────────────────────────────────────
Write-Step "Starting server…"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$APP_DIR'; uv run mla --open"
Write-Host ""
Write-Ok "Done! Browser should open at http://127.0.0.1:8000"