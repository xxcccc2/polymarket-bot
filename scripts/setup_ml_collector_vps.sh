#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
APP_USER="${APP_USER:-${SUDO_USER:-$USER}}"
APP_GROUP="${APP_GROUP:-$(id -gn "$APP_USER")}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
COLLECTOR_SYMBOL="${COLLECTOR_SYMBOL:-btcusdt}"
SERVICE_NAME="${SERVICE_NAME:-ml-microstructure-collector}"
DB_PATH="${DB_PATH:-$APP_DIR/data/ml/collectors/binance_microstructure.sqlite}"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

if ! command -v sudo >/dev/null 2>&1; then
  echo "sudo is required to install packages and the systemd service."
  exit 1
fi

echo "==> Installing system packages"
sudo apt-get update
sudo apt-get install -y chrony curl git python3 python3-venv python3-pip sqlite3

echo "==> Setting timezone to UTC"
sudo timedatectl set-timezone UTC

echo "==> Enabling clock sync"
sudo systemctl enable chrony
sudo systemctl restart chrony

echo "==> Ensuring app directory exists: $APP_DIR"
mkdir -p "$APP_DIR/data/ml/collectors"

echo "==> Creating virtual environment"
if [ ! -d "$APP_DIR/.venv" ]; then
  "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
fi

echo "==> Installing Python dependencies"
"$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
"$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

echo "==> Writing systemd service: $SERVICE_PATH"
sudo tee "$SERVICE_PATH" >/dev/null <<EOF
[Unit]
Description=ML Binance microstructure collector
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python -m scripts.collect_binance_microstructure --symbol ${COLLECTOR_SYMBOL} --db-path ${DB_PATH}
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

echo "==> Reloading systemd"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"

cat <<EOF

Collector service installed.

Start:
  sudo systemctl start ${SERVICE_NAME}

Status:
  sudo systemctl status ${SERVICE_NAME}

Logs:
  journalctl -u ${SERVICE_NAME} -f

Quality check:
  ${APP_DIR}/.venv/bin/python -m scripts.check_microstructure_quality --db-path ${DB_PATH}

Time sync:
  timedatectl
  chronyc tracking

EOF
