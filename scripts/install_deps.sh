#!/bin/bash
# scripts/install_deps.sh
# Run on EC2 after cloning/uploading the project.
# Installs Python deps + Playwright into the venv.

set -euo pipefail
APP_DIR="${APP_DIR:-/opt/fiverr-keepalive}"

echo "Installing Python dependencies..."
"$APP_DIR/venv/bin/pip" install --upgrade pip wheel
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "Installing Playwright browsers..."
"$APP_DIR/venv/bin/playwright" install chromium
"$APP_DIR/venv/bin/playwright" install-deps chromium

echo "✓ Dependencies installed."
