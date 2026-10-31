#!/usr/bin/env bash
# ==========================================
#  TICC-DASH Installer
#  - Installs into /opt/ticc-dash
#  - Uses current user
#  - Creates venv and installs Flask + Gunicorn
#  - Downloads ticc-dash.py + logo from GitHub
#  - Sets up and enables a systemd service
# ==========================================

set -euo pipefail

APP_NAME="TICC-DASH"
APP_DIR="/opt/ticc-dash"
VENV_DIR="$APP_DIR/venv"
SERVICE_NAME="ticc-dash.service"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME"
SUDOERS_FILE="/etc/sudoers.d/ticc-dash"

# Sources in repo
REPO_RAW_PY="https://raw.githubusercontent.com/anoniemerd/ticc-dash/main/ticc-dash.py"
REPO_RAW_LOGO="https://raw.githubusercontent.com/anoniemerd/ticc-dash/main/static/img/ticc-dash-logo.png"

USER_NAME="$(whoami)"

log()  { printf "\n\033[1;34m%s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m%s\033[0m\n" "$*"; }
warn() { printf "\n\033[1;33m%s\033[0m\n" "$*"; }
run()  { echo "  $*"; eval "$*"; }

trap 'echo "❌ Installation aborted."; exit 1' ERR

echo "=========================================="
echo "  🚀 Installing $APP_NAME"
echo "=========================================="

# 1) Dependencies
log "📦 Installing dependencies..."
run "sudo apt update -y"
run "sudo apt install -y python3 python3-venv python3-pip chrony nginx curl"
ok "✅ Dependencies installed."

# 2) Sudo for chronyc
log "🔐 Configuring sudo permission for chronyc..."
# The dashboard polls chronyc every second; without the Defaults line each poll
# writes a sudo log line plus a PAM session open/close pair to the journal.
if sudo test -f "$SUDOERS_FILE" && sudo grep -q "^$USER_NAME .*/usr/bin/chronyc" "$SUDOERS_FILE" \
   && sudo grep -q "!log_allowed" "$SUDOERS_FILE"; then
  ok "✅ Sudo rule already exists."
else
  SUDOERS_TMP="$(mktemp)"
  printf '%s\n' 'Defaults!/usr/bin/chronyc !log_allowed, !pam_session' \
    "$USER_NAME ALL=(ALL) NOPASSWD: /usr/bin/chronyc" > "$SUDOERS_TMP"
  run "sudo visudo -cf '$SUDOERS_TMP'"
  run "sudo install -o root -g root -m 440 '$SUDOERS_TMP' '$SUDOERS_FILE'"
  rm -f "$SUDOERS_TMP"
  ok "✅ Sudo rule added."
fi

# 3) Project dir
log "📁 Creating project directory at $APP_DIR..."
run "sudo mkdir -p '$APP_DIR/static/img'"
run "sudo chown -R '$USER_NAME':'$USER_NAME' '$APP_DIR'"
ok "✅ Project directory ready."

# 4) venv + Python deps
log "🐍 Setting up Python virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
  run "python3 -m venv '$VENV_DIR'"
fi
run "source '$VENV_DIR/bin/activate' && pip install --upgrade pip && pip install flask gunicorn && deactivate"
ok "✅ Virtual environment ready."

# 5) App + Logo
log "⬇️  Downloading application file..."
if [ -f "$APP_DIR/ticc-dash.py" ]; then
  warn "ℹ️  ticc-dash.py already exists, skipping download."
else
  run "curl -fsSL '$REPO_RAW_PY' -o '$APP_DIR/ticc-dash.py'"
  ok "✅ ticc-dash.py downloaded."
fi

log "🎨 Downloading logo asset..."
if [ -f "$APP_DIR/static/img/ticc-dash-logo.png" ]; then
  warn "ℹ️  Logo already exists, skipping download."
else
  run "curl -fsSL '$REPO_RAW_LOGO' -o '$APP_DIR/static/img/ticc-dash-logo.png'"
  ok "✅ Logo downloaded to $APP_DIR/static/img/ticc-dash-logo.png"
fi

# 6) systemd service
log "⚙️  Creating systemd service..."
run "sudo bash -c 'cat > \"$SERVICE_FILE\" <<EOF
[Unit]
Description=$APP_NAME - Time Information of Chrony Clients Dashboard
After=network.target

[Service]
User=$USER_NAME
WorkingDirectory=$APP_DIR
ExecStart=$VENV_DIR/bin/gunicorn --bind 0.0.0.0:5000 ticc-dash:app
Restart=always
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF'"

run "sudo systemctl daemon-reload"
run "sudo systemctl enable '$SERVICE_NAME'"
run "sudo systemctl restart '$SERVICE_NAME'"
ok "✅ Service started successfully."

# 7) Summary
echo
echo "=========================================="
ok "🎉 Installation complete!"
echo
echo "📍 Directory:     $APP_DIR"
echo "📄 App file:      $APP_DIR/ticc-dash.py"
echo "🖼️  Logo:          $APP_DIR/static/img/ticc-dash-logo.png"
echo "🧠 App object:    ticc-dash:app"
echo "🧩 Service:       $SERVICE_NAME"
echo
IP_ADDR="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "🌐 Access via:    http://$IP_ADDR:5000/"
echo
echo "🔎 Manage service:"
echo "  • sudo systemctl status $SERVICE_NAME"
echo "  • sudo systemctl restart $SERVICE_NAME"
echo "  • sudo journalctl -u $SERVICE_NAME -f"
echo "=========================================="
