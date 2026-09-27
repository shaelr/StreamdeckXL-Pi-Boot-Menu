# shellcheck shell=bash
# Bitfocus Companion Satellite, installed with Bitfocus's own pi-image installer.

satellite_label() { echo "Satellite"; }

satellite_installed() { [[ -f /opt/companion-satellite/BUILD || -f /etc/systemd/system/satellite.service ]]; }

satellite_detail() {
  local build
  build="$(cat /opt/companion-satellite/BUILD 2>/dev/null || true)"
  build="${build#v}"
  echo "v${build%%+*}"
}

satellite_install() {
  require_64bit
  log "Installing Bitfocus Satellite (latest stable)..."
  curl "${CURL_OPTS[@]}" https://raw.githubusercontent.com/bitfocus/companion-satellite/main/pi-image/install.sh \
    | SATELLITE_BUILD=stable bash
  # Their installer enables it at boot; the menu decides when it runs instead.
  systemctl disable satellite 2>/dev/null || true
  getent group plugdev >/dev/null || groupadd plugdev
  usermod -aG plugdev satellite
  log "Satellite $(satellite_detail) is installed."
  menu_redraw_if_running
}

# Bitfocus's own updater, with its version picker (stable, beta or a specific build).
satellite_update() {
  local was_active=0
  if is_active satellite; then was_active=1; fi
  satellite_restore_fnm
  log "Running Satellite's updater. Pick the version to install..."
  /usr/local/sbin/satellite-update
  # It always starts Satellite when it finishes; if the menu or Companion had
  # the deck, give it back.
  if (( ! was_active )); then systemctl stop satellite; fi
  log "Satellite is now $(satellite_detail)."
}

# Satellite runs on Node.js from /opt/fnm, which Companion's updater deletes.
# Reinstall fnm and Satellite's Node version the way Satellite's installer and
# updater do, without reinstalling Satellite (so a version picked in
# satellite-update stays).
satellite_restore_fnm() {
  if [[ -x /opt/fnm/fnm && -x /opt/fnm/aliases/default/bin/node ]]; then return 0; fi
  warn "Satellite's Node.js runtime (/opt/fnm) is missing; Companion's updater removes it. Putting it back..."
  curl "${CURL_OPTS[@]}" https://fnm.vercel.app/install | bash -s -- --install-dir /opt/fnm --skip-shell
  (
    export FNM_DIR=/opt/fnm PATH="/opt/fnm:$PATH"
    eval "$(fnm env --shell bash)"
    cd /usr/local/src/companion-satellite || exit
    fnm use --install-if-missing
    fnm default "$(fnm current)"
  )
}

satellite_remove() {
  log "Removing Satellite..."
  systemctl disable --now satellite 2>/dev/null || true
  rm -f /etc/systemd/system/satellite.service
  systemctl daemon-reload
  # /opt/fnm is Satellite's Node.js runtime; Companion doesn't use it.
  rm -rf /opt/companion-satellite /usr/local/src/companion-satellite /opt/fnm
  # Same cleanup companion-pi's updater does for the lines fnm's installer added.
  sed -i '/fnm/d; /FNM_DIR/d' /root/.bashrc 2>/dev/null || true
  rm -f /usr/local/bin/satellite-license /usr/local/bin/satellite-help \
    /usr/local/sbin/satellite-update /usr/local/sbin/satellite-edit-config
  rm -f /etc/udev/rules.d/50-satellite.rules
  udevadm control --reload-rules || true
  remove_motd_link /usr/local/src/companion-satellite
  if ask_delete_data "Satellite" /home/satellite /boot/satellite-config; then
    userdel -r satellite 2>/dev/null || true
    rm -f /boot/satellite-config
  fi
  menu_redraw_if_running
}
