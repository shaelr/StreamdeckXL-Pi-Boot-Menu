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

satellite_update() {
  local was_active=0
  is_active satellite && was_active=1
  satellite_install
  if (( was_active )); then
    log "Restarting Satellite to load the new version..."
    systemctl restart satellite
  fi
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
