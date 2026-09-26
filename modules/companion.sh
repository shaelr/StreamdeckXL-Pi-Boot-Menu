# shellcheck shell=bash
# Bitfocus Companion, installed with Bitfocus's own companion-pi installer.

companion_label() { echo "Companion"; }

companion_installed() { [[ -f /opt/companion/BUILD || -f /etc/systemd/system/companion.service ]]; }

companion_detail() {
  local build
  build="$(cat /opt/companion/BUILD 2>/dev/null || true)"
  build="${build#v}"
  echo "v${build%%+*}"
}

companion_install() {
  require_64bit
  log "Installing Bitfocus Companion (latest stable)..."
  # Their installer takes no CLI flags; it reads the channel from this env var.
  curl "${CURL_OPTS[@]}" https://raw.githubusercontent.com/bitfocus/companion-pi/main/install.sh \
    | COMPANION_BUILD=stable bash
  # Their installer enables it at boot; the menu decides when it runs instead.
  systemctl disable companion 2>/dev/null || true
  getent group plugdev >/dev/null || groupadd plugdev
  usermod -aG plugdev companion
  _companion_enable_shell_commands
  _companion_restore_satellite_runtime
  log "Companion $(companion_detail) is installed."
  menu_redraw_if_running
}

companion_update() {
  local was_active=0
  is_active companion && was_active=1
  companion_install
  if (( was_active )); then
    log "Restarting Companion to load the new version..."
    systemctl restart companion
  fi
}

companion_remove() {
  if companion_scripts_installed; then
    log "The Companion scripts only work with Companion, so removing them too."
    companion_scripts_remove
  fi
  log "Removing Companion..."
  systemctl disable --now companion 2>/dev/null || true
  rm -f /etc/systemd/system/companion.service
  rm -rf "$COMPANION_OVERRIDE_DIR"
  systemctl daemon-reload
  rm -rf /opt/companion /usr/local/src/companionpi
  rm -f /usr/local/bin/companion-license /usr/local/bin/companion-help \
    /usr/local/sbin/companion-update /usr/local/sbin/companion-config \
    /usr/local/sbin/companion-reset /usr/local/sbin/companion-sync-udev-rules
  rm -f /etc/sudoers.d/090-companion_sudo
  rm -f /etc/udev/rules.d/50-companion.rules
  udevadm control --reload-rules || true
  remove_motd_link /usr/local/src/companionpi
  # Only removed when empty: anything in here is a module the user added.
  rmdir /opt/companion-module-dev 2>/dev/null || true
  if ask_delete_data "Companion" /home/companion /etc/companion /opt/companion-module-dev; then
    userdel -r companion 2>/dev/null || true
    rm -rf /etc/companion /opt/companion-module-dev
  fi
  if [[ -f /etc/sudoers.d/092-timezone ]]; then
    log "Left /etc/sudoers.d/092-timezone (timezone buttons) in place; delete it by hand if you no longer need it."
  fi
  menu_redraw_if_running
}

# Companion's "Run shell command" action is off by default upstream. A systemd
# drop-in (not their config.yaml) because companion-pi's updater rewrites
# companion.service on every update but never touches .d/ overrides.
_companion_enable_shell_commands() {
  mkdir -p "$COMPANION_OVERRIDE_DIR"
  cat > "$COMPANION_OVERRIDE_DIR/menu-overrides.conf" <<'EOF'
[Service]
Environment=COMPANION_ENABLE_SHELL_COMMAND_SUPPORT=true
EOF
  systemctl daemon-reload
}

# companion-pi's update.sh deletes /opt/fnm ("fnm is no longer used"), but
# Satellite's service runs its Node.js from /opt/fnm. Re-running Satellite's
# installer puts it back (and skips the download if Satellite is already current).
# Set SDPI_SKIP_SATELLITE_FIX when Satellite is about to be reinstalled anyway.
_companion_restore_satellite_runtime() {
  if satellite_installed && [[ -z "${SDPI_SKIP_SATELLITE_FIX:-}" ]]; then
    warn "Companion's installer removes /opt/fnm, which Satellite needs. Re-running Satellite's installer to restore it..."
    satellite_update
  fi
}
