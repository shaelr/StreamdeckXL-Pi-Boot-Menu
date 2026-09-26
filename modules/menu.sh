# shellcheck shell=bash
# The Stream Deck menu app: menu/menu.py in a venv, run as root by menu.service.

menu_label() { echo "Menu app"; }

menu_installed() { [[ -f "$MENU_SERVICE_FILE" && -f "$MENU_INSTALL_DIR/menu.py" ]]; }

menu_detail() {
  local version state
  version="$("$MENU_VENV/bin/pip3" show streamdeck 2>/dev/null | awk '/^Version:/{print $2}')"
  if is_active menu; then state="running"; else state="stopped"; fi
  echo "streamdeck ${version:-?}, $state"
}

menu_install() {
  log "Installing the menu app..."
  apt_install python3 python3-venv network-manager libusb-1.0-0 \
    libhidapi-hidraw0 libhidapi-libusb0 fonts-dejavu-core
  _menu_udev_rule
  systemctl enable --now NetworkManager
  mkdir -p "$MENU_INSTALL_DIR" "$MENU_ICON_DIR" "$MENU_CFG_DIR"
  touch "$MENU_LOG"
  [[ -d "$MENU_VENV" ]] || python3 -m venv "$MENU_VENV"
  _menu_pip
  _menu_deploy
  _menu_write_service
  systemctl enable menu
  _menu_start_if_free
}

menu_update() {
  log "Updating the menu app..."
  _menu_pip
  _menu_deploy
  _menu_write_service
  _menu_start_if_free
}

menu_remove() {
  log "Removing the menu app..."
  systemctl disable --now menu 2>/dev/null || true
  rm -f "$MENU_SERVICE_FILE"
  systemctl daemon-reload
  rm -rf "$MENU_INSTALL_DIR"
  # Companion/Satellite users are in plugdev for device access; keep the rule
  # while either of them is still installed.
  if ! companion_installed && ! satellite_installed; then
    rm -f "$STREAMDECK_UDEV_RULE"
    udevadm control --reload-rules || true
  fi
  if ask_delete_data "The menu app" "$MENU_CFG_DIR" "$MENU_LOG"; then
    rm -rf "$MENU_CFG_DIR" "$MENU_LOG"
  fi
  _menu_offer_boot_target
}

_menu_udev_rule() {
  getent group plugdev >/dev/null || groupadd plugdev
  cat > "$STREAMDECK_UDEV_RULE" <<'EOF'
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0fd9", MODE="0660", GROUP="plugdev"
EOF
  udevadm control --reload-rules || true
  udevadm trigger || true
}

_menu_pip() {
  "$MENU_VENV/bin/pip3" install --upgrade pip
  # Deliberately unpinned. If the touchscreen renders wrong/rotated after an
  # update, check here first: menu.py's update_lcd() was written against
  # streamdeck 0.10.0's PILHelper/rotation behavior.
  "$MENU_VENV/bin/pip3" install --upgrade streamdeck hidapi pillow
}

_menu_deploy() {
  install -m 0755 "$SDPI_HOME/menu/menu.py" "$MENU_INSTALL_DIR/menu.py"
  install -m 0644 "$SDPI_HOME/menu/icons/comp256x256.png" "$MENU_ICON_DIR/comp256x256.png"
  install -m 0644 "$SDPI_HOME/menu/icons/sat256x256.png" "$MENU_ICON_DIR/sat256x256.png"
}

_menu_write_service() {
  cat > "$MENU_SERVICE_FILE" <<'EOF'
[Unit]
Description=StreamDeck Menu IP selector
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=simple
ExecStart=/opt/menu/venv/bin/python3 /opt/menu/menu.py
Restart=on-failure
RestartSec=1
User=root
WorkingDirectory=/opt/menu

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
}

# Only one process can hold the Stream Deck. If Companion or Satellite has it,
# leave the menu stopped; it takes over on the next boot or back-to-menu.
_menu_start_if_free() {
  if is_active companion || is_active satellite; then
    log "Companion or Satellite has the Stream Deck right now; the menu will take over on the next boot or when you go back to the menu."
  else
    systemctl restart menu
  fi
}

# Companion and Satellite are disabled at boot so the menu can choose. With the
# menu gone, nothing would start either of them.
_menu_offer_boot_target() {
  local options=() pick
  companion_installed && options+=("Companion")
  satellite_installed && options+=("Satellite")
  (( ${#options[@]} )) || return 0
  options+=("Neither")
  echo
  echo "Without the menu, nothing starts Companion or Satellite at boot."
  pick="$(choose "Start which one at boot instead?" "${options[@]}")"
  case "$pick" in
    Companion) systemctl enable --now companion ;;
    Satellite) systemctl enable --now satellite ;;
  esac
}
