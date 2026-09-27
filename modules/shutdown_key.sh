# shellcheck shell=bash
# The SHUTDOWN key on the menu. On by default, so the flag works the other way
# round: removing it writes SHUTDOWN_KEY_DISABLED_FILE, which menu.py checks.
# Only the menu key: Companion's shutdown-pi.sh (companion_scripts) is separate.

SHUTDOWN_KEY_DISABLED_FILE="$MENU_CFG_DIR/shutdown-button-disabled"

shutdown_key_label() { echo "Shutdown button"; }

shutdown_key_installed() { menu_installed && [[ ! -f "$SHUTDOWN_KEY_DISABLED_FILE" ]]; }

shutdown_key_detail() { echo "shut down the Pi from the menu"; }

shutdown_key_install() {
  if ! menu_installed; then
    err "Install the menu app first; this key lives on its screen."
    return 1
  fi
  log "Adding the SHUTDOWN key to the menu..."
  rm -f "$SHUTDOWN_KEY_DISABLED_FILE"
  menu_redraw_if_running
}

shutdown_key_remove() {
  log "Removing the SHUTDOWN key from the menu (Companion's scripts are not affected)..."
  mkdir -p "$MENU_CFG_DIR"
  touch "$SHUTDOWN_KEY_DISABLED_FILE"
  menu_redraw_if_running
}
