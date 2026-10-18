#!/usr/bin/env bash
# setup.sh — One-time setup for Junie Orchestrator
# Run this after cloning the repo to get everything ready.
# Safe to run again — it won't overwrite your work.

set -euo pipefail

# ── Colours for friendly output ───────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Colour

info()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn()  { echo -e "${YELLOW}[setup]${NC} $*"; }
error() { echo -e "${RED}[setup]${NC} $*"; }
note()  { echo -e "${CYAN}[setup]${NC} $*"; }

# ── 1. Check prerequisites ───────────────────────────────────────────────────

missing=0

# Python 3.11+
if command -v python3 &>/dev/null; then
    pyver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    pyminor=$(echo "$pyver" | cut -d. -f2)
    if [ "$pyminor" -ge 11 ]; then
        info "Python $pyver is installed."
    else
        error "Python $pyver found, but 3.11+ is required."
        echo "      Install it with: sudo apt install python3.11 python3.11-venv"
        missing=1
    fi
else
    error "Python 3 is not installed."
    echo "      Install it with: sudo apt install python3 python3-venv"
    missing=1
fi

# Junie CLI
if command -v junie &>/dev/null; then
    info "Junie CLI is installed ($(junie --version 2>/dev/null || echo 'unknown version'))."
else
    error "Junie CLI is not installed or not in your PATH."
    echo "      Install it from: https://github.com/junie-ai/junie"
    missing=1
fi

# Ollama
if command -v ollama &>/dev/null; then
    info "Ollama is installed."
else
    error "Ollama is not installed."
    echo "      Install it from: https://ollama.ai/"
    missing=1
fi

if [ "$missing" -eq 1 ]; then
    error "Please install the missing prerequisites and run this script again."
    exit 1
fi

# ── 2. Create virtual environment ────────────────────────────────────────────

if [ -d ".venv" ]; then
    info "Virtual environment (.venv) already exists — skipping creation."
else
    info "Creating virtual environment (.venv)..."
    python3 -m venv .venv
    info "Virtual environment created."
fi

# Activate it for the rest of this script
source .venv/bin/activate

# ── 3. Install Python dependencies ───────────────────────────────────────────

info "Installing Python dependencies..."
pip install -q -r requirements.txt
info "Dependencies installed."

# ── 4. Copy .env.example → .env (never overwrite) ────────────────────────────

if [ -f ".env" ]; then
    info ".env already exists — keeping your current configuration."
else
    cp .env.example .env
    info "Created .env from .env.example."
fi

# ── 5. Generate API_KEY if missing ───────────────────────────────────────────

# Read the current API_KEY from .env (if the line exists)
current_key=$(grep -E '^API_KEY=' .env | head -1 | cut -d'=' -f2-)

if [ -z "$current_key" ] || [ "$current_key" = "your-secret-key-here" ]; then
    # Generate a random 32-byte hex key
    new_key=$(head -c 16 /dev/urandom | xxd -p)
    if grep -qE '^API_KEY=' .env; then
        # Replace the existing line
        sed -i "s|^API_KEY=.*|API_KEY=$new_key|" .env
    else
        # Append it
        echo "API_KEY=$new_key" >> .env
    fi
    note "Generated a new API key:"
    echo ""
    echo "  API_KEY=$new_key"
    echo ""
    warn "Save this key — you'll need it for Open WebUI."
else
    info "API_KEY is already set in .env."
fi

# ── 6. Ask for WORKSPACE_PATH ────────────────────────────────────────────────

current_workspace=$(grep -E '^WORKSPACE_PATH=' .env | head -1 | cut -d'=' -f2-)
default_workspace="${current_workspace:-~/workspace}"

read -rp "  Workspace path (project work, memory, session logs) [$default_workspace]: " user_workspace
user_workspace="${user_workspace:-$default_workspace}"

if grep -qE '^WORKSPACE_PATH=' .env; then
    sed -i "s|^WORKSPACE_PATH=.*|WORKSPACE_PATH=$user_workspace|" .env
else
    echo "WORKSPACE_PATH=$user_workspace" >> .env
fi
info "WORKSPACE_PATH set to: $user_workspace"

# ── 7. Ask for VAULT_PATH ────────────────────────────────────────────────────

current_vault=$(grep -E '^VAULT_PATH=' .env | head -1 | cut -d'=' -f2-)
default_vault="${current_vault:-/mnt/vault}"

read -rp "  Obsidian vault path (searchable notes) [$default_vault]: " user_vault
user_vault="${user_vault:-$default_vault}"

if grep -qE '^VAULT_PATH=' .env; then
    sed -i "s|^VAULT_PATH=.*|VAULT_PATH=$user_vault|" .env
else
    echo "VAULT_PATH=$user_vault" >> .env
fi
info "VAULT_PATH set to: $user_vault"

# ── 8. Ask for OLLAMA_MODEL ──────────────────────────────────────────────────

current_model=$(grep -E '^OLLAMA_MODEL=' .env | head -1 | cut -d'=' -f2-)
default_model="${current_model:-gemma4:12b}"

read -rp "  Ollama model [$default_model]: " user_model
user_model="${user_model:-$default_model}"

if grep -qE '^OLLAMA_MODEL=' .env; then
    sed -i "s|^OLLAMA_MODEL=.*|OLLAMA_MODEL=$user_model|" .env
else
    echo "OLLAMA_MODEL=$user_model" >> .env
fi
info "OLLAMA_MODEL set to: $user_model"

# ── 9. Check if the model is pulled, offer to pull it ────────────────────────

# List pulled models and check if ours is there
pulled_models=$(ollama list 2>/dev/null || true)
if echo "$pulled_models" | grep -q "$user_model"; then
    info "Ollama model '$user_model' is already pulled."
else
    read -rp "  Model '$user_model' is not pulled locally. Pull it now? [Y/n] " pull_choice
    pull_choice="${pull_choice:-Y}"
    if [[ "$pull_choice" != [nN]* ]]; then
        info "Pulling '$user_model' — this may take a few minutes..."
        ollama pull "$user_model"
        info "Model pulled."
    else
        warn "Skipping model pull. Remember to run: ollama pull $user_model"
    fi
fi

# ── 10. Summary ────────────────────────────────────────────────────────────────

echo ""
echo "=========================================="
echo "  Setup complete! Here's what's next:"
echo "=========================================="
echo ""
echo "  1. Start the server:"
echo "     ./start.sh"
echo ""
echo "  2. In Open WebUI, add a new OpenAI API connection:"
echo "     Base URL : http://<this-machine-ip>:8000/v1"
echo "     API Key  : $new_key"
echo ""
echo "  3. (Optional) Run as a background service:"
echo "     sudo ./install_service.sh"
echo ""
echo "  For more details, see README.md"
echo ""
