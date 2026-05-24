#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Mistral Laptop Assistant — one-shot installer
# ─────────────────────────────────────────────────────────────────────────────
# Usage:
#   curl -LsSf https://yourcdn/install.sh | bash
#   bash <(curl -LsSf https://yourcdn/install.sh)
#   ./install.sh          # if already cloned
#
# What it does:
#   1. Installs uv (if not present)
#   2. Clones the repo (or cd into it)
#   3. Runs uv sync
#   4. Creates .env from .env.example
#   5. Prints instructions and opens browser
# ─────────────────────────────────────────────────────────────────────────────

set -e

APP_DIR="${MLA_DIR:-$HOME/mistral-laptop-assistant}"
REPO_URL="${MLA_REPO:-https://github.com/youruser/mistral-laptop-assistant.git}"

# ── colours ───────────────────────────────────────────────────────────────────
BOLD='\033[1m'
ORANGE='\033[38;5;202m'
GREEN='\033[32m'
RED='\033[31m'
RESET='\033[0m'

info()  { echo -e "${ORANGE}→${RESET} $*"; }
ok()    { echo -e "${GREEN}✓${RESET} $*"; }
warn()  { echo -e "${RED}!${RESET} $*"; }
step()  { echo -e "\n${BOLD}▸ $*${RESET}"; }

# ── detect uv ─────────────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
    step "Installing uv (Astral's fast Python package manager)…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Add uv to PATH for this session
    export PATH="$HOME/.local/bin:$PATH"
fi

# ── clone / update ─────────────────────────────────────────────────────────────
if [[ -d "$APP_DIR/.git" ]]; then
    step "Updating existing clone at $APP_DIR"
    cd "$APP_DIR" && git pull --quiet
else
    step "Cloning repository into $APP_DIR"
    git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

# ── install deps ────────────────────────────────────────────────────────────────
step "Installing dependencies with uv sync…"
uv sync

# ── configure ──────────────────────────────────────────────────────────────────
step "Configuring API key…"
ENV_FILE="$APP_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    if [[ -f "$APP_DIR/.env.example" ]]; then
        cp "$APP_DIR/.env.example" "$ENV_FILE"
        info "Created .env from .env.example"
    else
        echo "# MLA_MISTRAL_API_KEY=your-key-here" > "$ENV_FILE"
        info "Created blank .env"
    fi
fi

# Interactive key prompt
KEY_LINE=$(grep "MLA_MISTRAL_API_KEY=" "$ENV_FILE" 2>/dev/null || echo "")
CURRENT_KEY="${KEY_LINE#*=}"

if [[ -z "$CURRENT_KEY" ]]; then
    echo ""
    echo -e "${BOLD}🤖  Mistral Laptop Assistant setup${RESET}"
    echo ""
    echo "  Get your free API key at: https://console.mistral.ai/"
    echo ""
    read -rp "  Paste your MLA_MISTRAL_API_KEY: " PASTED_KEY
    PASTED_KEY=$(echo "$PASTED_KEY" | sed 's/[[:space:]]//g')
    if [[ -n "$PASTED_KEY" ]]; then
        if grep -q "MLA_MISTRAL_API_KEY=" "$ENV_FILE" 2>/dev/null; then
            sed -i.bak "s|^MLA_MISTRAL_API_KEY=.*|MLA_MISTRAL_API_KEY=$PASTED_KEY|" "$ENV_FILE"
        else
            echo "MLA_MISTRAL_API_KEY=$PASTED_KEY" >> "$ENV_FILE"
        fi
        ok "API key saved to .env"
    else
        warn "No key entered — you can add it to .env later"
    fi
else
    ok "API key already configured"
fi

# ── done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}✅  Installation complete!${RESET}"
echo ""
echo "  Start the assistant with:"
echo ""
echo "    cd $APP_DIR"
echo "    uv run mla --open"
echo ""
echo "  Or in one command from anywhere:"
echo ""
echo "    cd $APP_DIR && uv run mla --open"
echo ""
echo "  Run without browser:"
echo "    uv run mla"
echo ""
echo "  Available commands:"
echo "    uv run mla --open       # web UI"
echo "    uv run mla --window     # native window (needs --extra desktop)"
echo "    uv run mla --tray       # system tray icon"
echo "    uv run mla audit --tail  # watch audit log"
echo ""

# ── auto-start (optional) ───────────────────────────────────────────────────────
if [[ "${1:-}" == "--start" ]]; then
    step "Starting server on http://127.0.0.1:8000 …"
    nohup uv run mla > /tmp/mla.log 2>&1 &
    sleep 2
    if curl -s --max-time 3 http://127.0.0.1:8000/api/status > /dev/null 2>&1; then
        ok "Server is running at http://127.0.0.1:8000"
        if command -v xdg-open &>/dev/null; then
            xdg-open http://127.0.0.1:8000
        elif command -v open &>/dev/null; then
            open http://127.0.0.1:8000
        fi
    else
        warn "Server may still be starting — check /tmp/mla.log"
    fi
fi