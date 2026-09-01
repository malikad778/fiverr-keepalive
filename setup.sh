#!/bin/bash
# =============================================================
#  setup.sh — One-shot EC2 Ubuntu 22.04 bootstrap script
#  Run as root (or with sudo) on a fresh EC2 instance.
#
#  Usage:
#    chmod +x setup.sh && sudo ./setup.sh
# =============================================================
set -euo pipefail

APP_DIR="/opt/fiverr-keepalive"
APP_USER="keepalive"
PYTHON_VERSION="3.11"

echo "=================================================="
echo "  Fiverr Keepalive — EC2 Bootstrap"
echo "=================================================="

# ── 1. System Updates ─────────────────────────────────────
echo "[1/9] Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq \
    curl wget git unzip \
    python3.11 python3.11-venv python3.11-dev python3-pip \
    xvfb \
    libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 libgbm1 \
    libasound2 libxshmfence1 libgtk-3-0 libgles2 \
    fonts-liberation libappindicator3-1 \
    build-essential libssl-dev libffi-dev \
    jq htop screen tmux

# ── 2. Create dedicated user ──────────────────────────────
echo "[2/9] Creating app user..."
if ! id "$APP_USER" &>/dev/null; then
    useradd -r -m -s /bin/bash "$APP_USER"
    echo "Created user: $APP_USER"
fi

# ── 3. Set up app directory ───────────────────────────────
echo "[3/9] Setting up app directory..."
mkdir -p "$APP_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# ── 4. Python virtual environment ────────────────────────
echo "[4/9] Creating Python virtual environment..."
sudo -u "$APP_USER" python3.11 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip wheel

# ── 5. Install Python dependencies ───────────────────────
echo "[5/9] Installing Python dependencies..."
if [ -f "$APP_DIR/requirements.txt" ]; then
    sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"
fi

# ── 6. Install Playwright & Chromium ─────────────────────
echo "[6/9] Installing Playwright + Chromium..."
sudo -u "$APP_USER" "$APP_DIR/venv/bin/playwright" install chromium
"$APP_DIR/venv/bin/playwright" install-deps chromium

# ── 7. Set up virtual display (Xvfb) ─────────────────────
echo "[7/9] Setting up virtual display..."
cat > /etc/systemd/system/xvfb.service << 'EOF'
[Unit]
Description=Virtual Framebuffer (Xvfb)
After=network.target

[Service]
# 1920x1080 so it covers the largest entry in the fingerprint pool
# (config.yaml fingerprint.screen_resolutions). A screen smaller than the
# chosen viewport means the browser window is clipped by the X server while
# window.screen still reports the spoofed size — an inconsistency that is
# both a rendering hazard and a fingerprinting signal.
ExecStart=/usr/bin/Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable xvfb
systemctl start xvfb
echo "DISPLAY=:99" >> /etc/environment

# ── 8. Systemd service for keepalive ─────────────────────
echo "[8/9] Installing systemd service..."
if [ -f "$APP_DIR/systemd/fiverr-keepalive.service" ]; then
    cp "$APP_DIR/systemd/fiverr-keepalive.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable fiverr-keepalive
    echo "Service installed. Start with: sudo systemctl start fiverr-keepalive"
fi

# ── 9. Firewall (outbound only for security) ─────────────
echo "[9/9] Configuring firewall..."
apt-get install -y -qq ufw
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp   # SSH
ufw --force enable

# ── Done ─────────────────────────────────────────────────
echo ""
echo "=================================================="
echo "  Bootstrap Complete!"
echo "=================================================="
echo ""
echo "Next steps:"
echo "  1. Upload your project:  ./deploy.sh <EC2_PUBLIC_IP>"
echo "  2. SSH in and run:       python scripts/first_run.py"
echo "  3. Start daemon:         sudo systemctl start fiverr-keepalive"
echo "  4. Monitor logs:         journalctl -u fiverr-keepalive -f"
echo ""
