# shellcheck shell=bash
# The RESTART key on the menu. On by default, so the flag works the other way
# round: removing it writes RESTART_KEY_DISABLED_FILE, which menu.py checks.
# Only the menu key: Companion's reboot-pi.sh (companion_scripts) is separate.

RESTART_KEY_DISABLED_FILE="$MENU_CFG_DIR/restart-button-disabled"

restart_key_label() { echo "Restart button"; }

restart_key_installed() { menu_installed && [[ ! -f "$RESTART_KEY_DISABLED_FILE" ]]; }

restart_key_detail() { echo "restart the Pi from the menu"; }

restart_key_install() {
  if ! menu_installed; then
    err "Install the menu app first; this key lives on its screen."
    return 1
  fi
  log "Adding the RESTART key to the menu..."
  rm -f "$RESTART_KEY_DISABLED_FILE"
  menu_redraw_if_running
}

restart_key_remove() {
  log "Removing the RESTART key from the menu (Companion's scripts are not affected)..."
  mkdir -p "$MENU_CFG_DIR"
  touch "$RESTART_KEY_DISABLED_FILE"
  menu_redraw_if_running
}
