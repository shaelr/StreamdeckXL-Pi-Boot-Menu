# shellcheck shell=bash
# Timezone keys on the menu's top row. The menu reads the zone list from
# TZ_BUTTONS_FILE; with no file it shows no timezone keys. menu.py runs as
# root, so it sets the zone itself with timedatectl, no sudoers rule needed.
# Setting the zone from the menu before handing off means Companion starts
# fresh in the right zone, so it never needs the restart the manual Companion
# timezone buttons do.

TZ_BUTTONS_FILE="$MENU_CFG_DIR/timezones.json"

timezone_label() { echo "Timezone buttons"; }

timezone_installed() { menu_installed && [[ -f "$TZ_BUTTONS_FILE" ]]; }

timezone_detail() {
  local zones current
  zones="$(grep -c '"zone"' "$TZ_BUTTONS_FILE" 2>/dev/null || true)"
  current="$(timedatectl show --property=Timezone --value 2>/dev/null || true)"
  echo "$zones zones, now ${current:-unknown}"
}

timezone_install() {
  if ! menu_installed; then
    err "Install the menu app first; these keys live on its screen."
    return 1
  fi
  mkdir -p "$MENU_CFG_DIR"
  if [[ -f "$TZ_BUTTONS_FILE" ]]; then
    log "Keeping your existing zone list in $TZ_BUTTONS_FILE."
  else
    log "Writing the zone list to $TZ_BUTTONS_FILE..."
    cat > "$TZ_BUTTONS_FILE" <<'EOF'
[
  {"label": "EASTERN",  "zone": "America/New_York"},
  {"label": "CENTRAL",  "zone": "America/Chicago"},
  {"label": "MOUNTAIN", "zone": "America/Denver"},
  {"label": "PACIFIC",  "zone": "America/Los_Angeles"},
  {"label": "ARIZONA",  "zone": "America/Phoenix"}
]
EOF
  fi
  menu_redraw_if_running
  echo
  echo "The menu shows one key per zone across its top row (up to 9); the active zone is green."
  echo "Edit $TZ_BUTTONS_FILE to change them, then Update > Menu app (or restart the menu)."
}

timezone_remove() {
  log "Removing the timezone keys..."
  rm -f "$TZ_BUTTONS_FILE"
  menu_redraw_if_running
}
