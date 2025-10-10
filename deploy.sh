#!/usr/bin/env bash
set -euo pipefail

# deploy.sh
# Usage:
# 1) In an empty directory run: ./deploy.sh
# 2) Optionally set environment variables to populate .env automatically:
#    TELEGRAM_BOT_TOKEN, DO_API_KEY, DO_AGENT_ID, AGENT_ENDPOINT, AGENT_ACCESS_KEY
#
# The script will:
# - Clone https://github.com/sitemech/biobot_do.git into the current directory
# - Copy .env.example to .env and substitute values from environment if provided
# - Create a Python venv at .venv and install requirements
# - Optionally create a systemd service if run as root

REPO_URL="https://github.com/sitemech/biobot_do.git"
APP_DIR="."

echo "Starting deployment script"

script_name="$(basename "$0")"
# Consider the directory empty if the only file present is this script itself.
other_count=$(ls -A | grep -v -x "$script_name" | wc -l || true)
if [ "$other_count" -ne 0 ]; then
  echo "Warning: current directory is not empty. This script expects an empty directory or a fresh workspace."
  read -r -p "Continue anyway? (y/N) " yn
  case "$yn" in
    [Yy]*) ;;
    *) echo "Aborting."; exit 1;;
  esac
fi

echo "Preparing repository in $APP_DIR"
# Decide target directory: if current dir is a git repo, use it; otherwise
# if directory non-empty we'll clone into ./app to avoid git clone failure.
TARGET_DIR="."
if [ -d .git ]; then
  TARGET_DIR="."
  echo "Existing git repository detected. Updating from origin..."
  # Try to update to latest main; fail safely if remote/branch is different
  git fetch origin || true
  if git rev-parse --verify origin/main >/dev/null 2>&1; then
    git pull --ff-only origin main || git pull origin main || true
  else
    echo "Warning: origin/main not found. Skipping automatic pull."
  fi
else
  if [ "$other_count" -ne 0 ]; then
    TARGET_DIR="app"
    echo "Directory is not empty and not a git repo. Will clone into ./$TARGET_DIR"
    mkdir -p "$TARGET_DIR"
    git clone "$REPO_URL" "$TARGET_DIR"
  else
    echo "Cloning repository $REPO_URL into $APP_DIR"
    git clone "$REPO_URL" .
  fi
fi

# Work inside the target directory for further steps
if [ "$TARGET_DIR" != "." ]; then
  cd "$TARGET_DIR"
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Please install Python 3.11+ and rerun the script." >&2
  exit 1
fi

echo "Creating Python virtual environment in .venv"
python3 -m venv .venv

echo "Activating virtual environment and upgrading pip"
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel

if [ -f requirements.txt ]; then
  echo "Installing Python dependencies from requirements.txt"
  pip install -r requirements.txt
else
  echo "requirements.txt not found, skipping pip install"
fi

if [ -f .env.example ]; then
  echo "Populating .env from .env.example"
  python3 - "$@" <<'PY'
import os
src = '.env.example'
dst = '.env'
with open(src, 'r', encoding='utf-8') as f:
    lines = f.read().splitlines()

keys = ['TELEGRAM_BOT_TOKEN','DO_API_KEY','DO_AGENT_ID','DO_API_BASE_URL','AGENT_ENDPOINT','AGENT_ACCESS_KEY','DO_API_TIMEOUT']
env = {k: os.environ.get(k) for k in keys}

out = []
for L in lines:
    if not L.strip() or L.strip().startswith('#') or '=' not in L:
        out.append(L)
        continue
    k = L.split('=',1)[0]
    if k in env and env[k]:
        out.append(f"{k}={env[k]}")
    else:
        out.append(L)

with open(dst, 'w', encoding='utf-8') as f:
    f.write('\n'.join(out) + '\n')
print('Wrote .env (edit it to fill any missing secrets):', dst)
PY
else
  echo ".env.example not found; create .env manually with required variables." >&2
fi

echo "Adjusting file permissions"
chmod 600 .env || true

echo "Deployment files are ready."

ABS_DIR=$(pwd)
PYTHON_BIN="$ABS_DIR/.venv/bin/python"

echo
echo "Next steps / service setup"
if [ "$EUID" -eq 0 ]; then
  echo "Creating systemd service since script runs as root."
  SERVICE_FILE="/etc/systemd/system/telegram-ai-bot.service"
  cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=Telegram bot for DigitalOcean AI Agent
After=network.target

[Service]
Type=simple
WorkingDirectory=$ABS_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$PYTHON_BIN $ABS_DIR/main.py $ABS_DIR/.env
Restart=on-failure
User=$(whoami)
Group=$(id -gn)

[Install]
WantedBy=multi-user.target
SERVICE

  systemctl daemon-reload
  systemctl enable --now telegram-ai-bot.service
  echo "systemd service installed and started: telegram-ai-bot.service"
  echo "Watch logs: journalctl -u telegram-ai-bot -f"
else
  echo "To run as a background service on a systemd host, re-run this script as root or create a unit file like below and enable it manually (replace /path/to with $(pwd)):"
  echo
  cat <<EOF
[Unit]
Description=Telegram bot for DigitalOcean AI Agent
After=network.target

[Service]
Type=simple
WorkingDirectory=$ABS_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$PYTHON_BIN $ABS_DIR/main.py $ABS_DIR/.env
Restart=on-failure
User=youruser
Group=yourgroup

[Install]
WantedBy=multi-user.target
EOF
  echo
  echo "You can start the bot now in foreground:"
  echo "  $PYTHON_BIN $ABS_DIR/main.py $ABS_DIR/.env"
fi

echo "Done."
