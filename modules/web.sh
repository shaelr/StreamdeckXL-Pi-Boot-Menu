# shellcheck shell=bash
# Web control page: menu/web.py as menu-web.service, always on, port 80. Its
# files are deployed with the menu app (/opt/menu); this feature is just the
# service. On by default: installed with the menu app, and by updates unless
# the user removed it (WEB_DISABLED_FILE records that).

WEB_SERVICE_FILE="/etc/systemd/system/menu-web.service"
WEB_DISABLED_FILE="$MENU_CFG_DIR/web-disabled"

web_label() { echo "Web control page"; }

web_installed() { [[ -f "$WEB_SERVICE_FILE" ]]; }

web_detail() {
  local ip state
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  if is_active menu-web; then state="running"; else state="stopped"; fi
  echo "http://${ip:-<pi-ip>}, $state"
}

web_install() {
  if ! menu_installed; then
    err "Install the menu app first; the web page ships with it."
    return 1
  fi
  log "Installing the web control page..."
  rm -f "$WEB_DISABLED_FILE"
  cat > "$WEB_SERVICE_FILE" <<'EOF'
[Unit]
Description=StreamDeck Menu web control page
After=network.target

[Service]
Type=simple
ExecStart=/opt/menu/venv/bin/python3 /opt/menu/web.py
Restart=always
RestartSec=3
User=root
WorkingDirectory=/opt/menu

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable menu-web
  systemctl restart menu-web
  log "Web control page: $(web_detail)"
}

# The code is deployed by the menu app's update; this just reloads it.
web_update() {
  log "Restarting the web control page..."
  systemctl restart menu-web
}

web_remove() {
  log "Removing the web control page..."
  systemctl disable --now menu-web 2>/dev/null || true
  rm -f "$WEB_SERVICE_FILE"
  systemctl daemon-reload
  mkdir -p "$MENU_CFG_DIR"
  touch "$WEB_DISABLED_FILE"
}

# Brings the page to installs that predate it, without undoing a removal.
web_install_if_wanted() {
  if menu_installed && ! web_installed && [[ ! -f "$WEB_DISABLED_FILE" ]]; then
    web_install
  fi
}
