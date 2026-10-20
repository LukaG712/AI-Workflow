#!/usr/bin/env bash
# install_service.sh — Install Junie Orchestrator as a systemd background service
# Detects your user and project path automatically — no manual editing needed.
# Must be run with sudo.

set -euo pipefail

# ── Must be run with sudo ─────────────────────────────────────────────────────

if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run with sudo."
    echo ""
    echo "  sudo ./install_service.sh"
    echo ""
    echo "This is needed so the script can write the systemd service file to"
    echo "/etc/systemd/system/ and start the service."
    exit 1
fi

# ── Detect the user who invoked sudo ──────────────────────────────────────────

# SUDO_USER is set automatically by sudo. If it's missing, the script was
# probably run as root directly (e.g., SSH'd in as root), which won't work
# because Junie needs a normal user's home directory for its login session.
if [ -z "${SUDO_USER:-}" ]; then
    echo "Error: SUDO_USER is not set. This script must be run with sudo,"
    echo "not as root directly."
    echo ""
    echo "Log in as your normal user and run:"
    echo ""
    echo "  sudo ./install_service.sh"
    exit 1
fi

RUN_USER="$SUDO_USER"
RUN_HOME=$(eval echo "~$RUN_USER")

echo "Detected user: $RUN_USER"
echo "Home directory: $RUN_HOME"

# ── Detect the project directory ──────────────────────────────────────────────

# When run via sudo ./install_service.sh, the script lives in the project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Project directory: $SCRIPT_DIR"

# ── Read PORT from .env (if it exists) ────────────────────────────────────────

PORT=8000
if [ -f "$SCRIPT_DIR/.env" ]; then
    env_port=$(grep -E '^PORT=' "$SCRIPT_DIR/.env" | head -1 | cut -d'=' -f2-)
    if [ -n "$env_port" ]; then
        PORT="$env_port"
    fi
fi

echo "Service port: $PORT"

# ── Build the systemd service file ────────────────────────────────────────────

SERVICE_NAME="juniehub"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

VENV_UVICORN="$SCRIPT_DIR/.venv/bin/uvicorn"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=JunieHub — coding-agent orchestration backend
After=network.target

[Service]
Type=simple
User=$RUN_USER
Environment=HOME=$RUN_HOME
WorkingDirectory=$SCRIPT_DIR
EnvironmentFile=$SCRIPT_DIR/.env
ExecStart=$VENV_UVICORN orchestrator:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo ""
echo "Created: $SERVICE_FILE"
echo ""

# ── Enable and start the service ──────────────────────────────────────────────

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"

echo "Service '$SERVICE_NAME' is now running."
echo ""
echo "Useful commands:"
echo "  sudo systemctl status $SERVICE_NAME   # Check status"
echo "  sudo systemctl stop $SERVICE_NAME     # Stop the service"
echo "  sudo systemctl restart $SERVICE_NAME  # Restart after config changes"
echo "  journalctl -u $SERVICE_NAME -f        # Follow logs"
echo ""
