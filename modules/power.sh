# shellcheck shell=bash
# RESTART and SHUTDOWN keys on the menu. On by default, so the flag works the
# other way round: removing them writes POWER_DISABLED_FILE, which menu.py
# checks. Existing installs get the keys on update with nothing to install.

POWER_DISABLED_FILE="$MENU_CFG_DIR/power-buttons-disabled"

power_label() { echo "Power buttons"; }

power_installed() { menu_installed && [[ ! -f "$POWER_DISABLED_FILE" ]]; }

power_detail() { echo "restart and shutdown on the menu"; }

power_install() {
  if ! menu_installed; then
    err "Install the menu app first; these keys live on its screen."
    return 1
  fi
  log "Adding the restart and shutdown keys to the menu..."
  rm -f "$POWER_DISABLED_FILE"
  menu_redraw_if_running
}

power_remove() {
  log "Removing the restart and shutdown keys from the menu..."
  mkdir -p "$MENU_CFG_DIR"
  touch "$POWER_DISABLED_FILE"
  menu_redraw_if_running
}
