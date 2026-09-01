#!/bin/bash
# =============================================================
#  deploy.sh - Upload project to EC2 and optionally install
#
#  Usage:
#    ./deploy.sh <EC2_PUBLIC_IP> [--key /path/to/key.pem] [--bootstrap]
#
#  Examples:
#    ./deploy.sh 54.123.45.67 --key ~/.ssh/my-key.pem
#    ./deploy.sh 54.123.45.67 --key ~/.ssh/my-key.pem --bootstrap
# =============================================================
set -euo pipefail

EC2_IP="${1:-}"
KEY_FILE=""
DO_BOOTSTRAP=false
APP_DIR="/opt/fiverr-keepalive"
EC2_USER="ubuntu"

# Parse args
shift 2>/dev/null || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --key)      KEY_FILE="$2"; shift 2 ;;
        --bootstrap) DO_BOOTSTRAP=true;    shift ;;
        *)          echo "Unknown arg: $1"; exit 1 ;;
    esac
done

if [ -z "$EC2_IP" ]; then
    echo "Usage: ./deploy.sh <EC2_IP> [--key /path/to/key.pem] [--bootstrap]"
    exit 1
fi

SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=15"
if [ -n "$KEY_FILE" ]; then
    SSH_OPTS="$SSH_OPTS -i $KEY_FILE"
fi

echo "=================================================="
echo "  Deploying to EC2: $EC2_IP"
echo "=================================================="

# ── 1. Create remote app dir ──────────────────────────────
echo "[1/4] Creating remote directory..."
ssh $SSH_OPTS "$EC2_USER@$EC2_IP" "sudo mkdir -p $APP_DIR && sudo chown $EC2_USER:$EC2_USER $APP_DIR"

# ── 2. Sync project files ─────────────────────────────────
echo "[2/4] Uploading project files..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

rsync -avz --progress \
    $SSH_OPTS \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.env' \
    --exclude 'session/store.db' \
    --exclude 'session/profile/' \
    --exclude 'logs/*.log' \
    "$SCRIPT_DIR/" \
    "$EC2_USER@$EC2_IP:$APP_DIR/"

echo "[3/4] Uploading .env file (if it exists locally)..."
if [ -f "$SCRIPT_DIR/.env" ]; then
    scp $SSH_OPTS "$SCRIPT_DIR/.env" "$EC2_USER@$EC2_IP:$APP_DIR/.env"
    ssh $SSH_OPTS "$EC2_USER@$EC2_IP" "chmod 600 $APP_DIR/.env"
    echo "✓ .env uploaded"
else
    echo "⚠  No .env found locally - you must create it on EC2 manually."
fi

# ── 3. Bootstrap if requested ────────────────────────────
if [ "$DO_BOOTSTRAP" = true ]; then
    echo "[4/4] Running bootstrap script on EC2..."
    ssh $SSH_OPTS "$EC2_USER@$EC2_IP" \
        "sudo bash $APP_DIR/setup.sh && \
         sudo chown -R keepalive:keepalive $APP_DIR && \
         $APP_DIR/venv/bin/pip install -r $APP_DIR/requirements.txt"
else
    echo "[4/4] Skipping bootstrap (--bootstrap not specified)"
    echo "      Run manually: ssh $EC2_USER@$EC2_IP 'sudo bash $APP_DIR/setup.sh'"
fi

echo ""
echo "=================================================="
echo "  Deployment Complete!"
echo "=================================================="
echo ""
echo "Next steps:"
echo "  ssh $EC2_USER@$EC2_IP ${KEY_FILE:+-i $KEY_FILE}"
echo "  cd $APP_DIR"
echo "  python scripts/first_run.py   # one-time login"
echo "  sudo systemctl start fiverr-keepalive"
echo "  journalctl -u fiverr-keepalive -f"
