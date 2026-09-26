# shellcheck shell=bash
# Scripts for Companion "Run shell command" buttons: shut down, reboot, and
# hand the Stream Deck back to the menu.

companion_scripts_label() { echo "Companion scripts"; }

companion_scripts_installed() { [[ -f "$COMPANION_SCRIPTS_DIR/back-to-menu.sh" ]]; }

companion_scripts_detail() { echo "$COMPANION_SCRIPTS_DIR"; }

companion_scripts_install() {
  if ! companion_installed; then
    err "Install Companion first; these scripts are run from Companion buttons."
    return 1
  fi
  log "Installing the Companion scripts to $COMPANION_SCRIPTS_DIR..."
  mkdir -p "$COMPANION_SCRIPTS_DIR"
  local s
  for s in shutdown-pi.sh reboot-pi.sh back-to-menu.sh; do
    install -m 0755 "$SDPI_HOME/companion-scripts/$s" "$COMPANION_SCRIPTS_DIR/$s"
  done
  _companion_scripts_sudoers
  echo
  echo "In Companion, point \"Run shell command\" buttons at:"
  echo "  $COMPANION_SCRIPTS_DIR/shutdown-pi.sh"
  echo "  $COMPANION_SCRIPTS_DIR/reboot-pi.sh"
  echo "  sudo $COMPANION_SCRIPTS_DIR/back-to-menu.sh   (note the leading sudo)"
}

companion_scripts_update() { companion_scripts_install; }

companion_scripts_remove() {
  log "Removing the Companion scripts..."
  rm -rf "$COMPANION_SCRIPTS_DIR"
  rm -f "$COMPANION_SCRIPTS_SUDOERS"
}

# shutdown-pi.sh/reboot-pi.sh need no grant: Bitfocus's own
# /etc/sudoers.d/090-companion_sudo already lets the companion user run
# /sbin/shutdown and /sbin/reboot. back-to-menu.sh needs root for itself.
_companion_scripts_sudoers() {
  local tmp
  tmp="$(mktemp)"
  echo "companion ALL=NOPASSWD: $COMPANION_SCRIPTS_DIR/back-to-menu.sh" > "$tmp"
  if visudo -cf "$tmp" >/dev/null; then
    install -m 0440 -o root -g root "$tmp" "$COMPANION_SCRIPTS_SUDOERS"
    rm -f "$tmp"
  else
    rm -f "$tmp"
    err "The generated sudoers rule failed validation; not installing it."
    return 1
  fi
}
