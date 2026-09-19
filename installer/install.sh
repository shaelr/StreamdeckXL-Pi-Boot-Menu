#!/usr/bin/env bash
set -euo pipefail

# ==========================================================
# menu installer for Raspberry Pi / Debian-based
# - Waits for apt/dpkg locks (prevents lock-frontend errors)
# - Repairs half-configured dpkg state if needed
# - Deploys the menu app into a venv (Stream Deck + XL support comes
#   from the upstream `streamdeck` PyPI package, always installed latest)
# ==========================================================

# Resolve the app source (menu.py, icons) relative to this script's own
# location, not the caller's cwd — so this still works when run as
# `bash installer/install.sh` from elsewhere. They live in ../menu.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MENU_SRC_DIR="$(cd "$SCRIPT_DIR/../menu" && pwd)"

MENU_INSTALL_DIR="/opt/menu"
VENV_DIR="${MENU_INSTALL_DIR}/venv"
ICON_DIR="${MENU_INSTALL_DIR}/icons"
LOG_FILE="/var/log/menu.log"
CFG_DIR="/etc/menu"
SERVICE_FILE="/etc/systemd/system/menu.service"
UDEV_RULE="/etc/udev/rules.d/70-streamdeck.rules"

MENU_PY_SRC="${MENU_SRC_DIR}/menu.py"
ICON_COMP_SRC="${MENU_SRC_DIR}/icons/comp256x256.png"
ICON_SAT_SRC="${MENU_SRC_DIR}/icons/sat256x256.png"

log() { echo "[$(date +'%F %T')] $*"; }

trap 'log "FAILED at line $LINENO: $BASH_COMMAND"' ERR

need_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root: sudo $0"
    exit 1
  fi
}

check_arch() {
  local arch
  arch=$(dpkg --print-architecture)
  case "$arch" in
    arm64|amd64) ;;
    *)
      echo "Unsupported CPU architecture: $arch"
      echo "Bitfocus Companion/Satellite only ship arm64 (or amd64) builds."
      echo "Re-flash with a 64-bit Raspberry Pi OS image and re-run this installer."
      exit 1
      ;;
  esac
}

check_sources() {
  local missing=0
  for f in \
    "$MENU_PY_SRC" \
    "$ICON_COMP_SRC" \
    "$ICON_SAT_SRC"
  do
    if [[ ! -f "$f" ]]; then
      echo "Missing file: $f"
      missing=1
    fi
  done
  if [[ "$missing" -eq 1 ]]; then
    echo
    echo "This looks like an incomplete checkout — the 'menu' directory should"
    echo "sit alongside 'installer' at the repo root. Re-clone and try again."
    exit 1
  fi
}

# ---- APT / DPKG lock handling ----
wait_for_apt() {
  # Locks that commonly block apt/dpkg
  local locks=(
    "/var/lib/dpkg/lock-frontend"
    "/var/lib/dpkg/lock"
    "/var/cache/apt/archives/lock"
  )

  log "Checking apt/dpkg locks..."
  while :; do
    local busy=0
    for l in "${locks[@]}"; do
      if fuser "$l" >/dev/null 2>&1; then
        busy=1
      fi
    done

    if [[ "$busy" -eq 0 ]]; then
      break
    fi

    # Show who is holding locks (helps debugging)
    log "APT/DPKG busy (auto updates running?). Waiting..."
    ps aux | grep -E 'apt|dpkg|unattended|apt\.systemd\.daily' | grep -v grep || true
    sleep 3
  done
  log "Locks are free."
}

repair_dpkg_if_needed() {
  # If dpkg was interrupted, this makes apt usable again
  log "Ensuring dpkg is in a consistent state..."
  dpkg --configure -a || true
  apt-get -f install -y || true
}

apt_install() {
  export DEBIAN_FRONTEND=noninteractive

  wait_for_apt
  repair_dpkg_if_needed
  wait_for_apt

  log "apt update..."
  apt-get update

  wait_for_apt
  log "apt upgrade..."
  apt-get upgrade -y

  wait_for_apt
  log "Installing packages..."
  apt-get install -y \
    curl ca-certificates \
    python3 python3-venv \
    network-manager \
    libusb-1.0-0 \
    libhidapi-hidraw0 \
    libhidapi-libusb0 \
    fonts-dejavu-core
}

cleanup_apt() {
  log "Cleaning up apt caches..."
  apt-get autoremove -y || true
  apt-get clean || true
}

CURL_OPTS=(-fsSL --retry 3 --retry-delay 5)

install_bitfocus() {
  # Both upstream installers pick the latest release for the given channel
  # from the Bitfocus API; they take no CLI flags, only these env vars.
  log "Installing Bitfocus Companion (latest stable)..."
  curl "${CURL_OPTS[@]}" https://raw.githubusercontent.com/bitfocus/companion-pi/main/install.sh | COMPANION_BUILD=stable bash

  log "Installing Bitfocus Satellite (latest stable)..."
  curl "${CURL_OPTS[@]}" https://raw.githubusercontent.com/bitfocus/companion-satellite/main/pi-image/install.sh | SATELLITE_BUILD=stable bash

  log "Installed Companion build: $(cat /opt/companion/BUILD 2>/dev/null || echo unknown)"
  log "Installed Satellite build: $(cat /opt/companion-satellite/BUILD 2>/dev/null || echo unknown)"
}

disable_services() {
  log "Disabling companion/satellite services..."
  systemctl disable --now companion 2>/dev/null || true
  systemctl disable --now satellite 2>/dev/null || true
}

ensure_plugdev() {
  log "Ensuring plugdev group + adding users..."
  getent group plugdev >/dev/null || groupadd plugdev

  # add if users exist (install scripts may vary)
  getent passwd companion >/dev/null && usermod -aG plugdev companion || true
  getent passwd satellite >/dev/null && usermod -aG plugdev satellite || true
}

write_udev_rule() {
  log "Writing StreamDeck udev rule..."
  cat > "$UDEV_RULE" <<'EOF'
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0fd9", MODE="0660", GROUP="plugdev"
EOF
  udevadm control --reload-rules || true
  udevadm trigger || true
}

enable_networkmanager() {
  log "Enabling NetworkManager..."
  systemctl enable --now NetworkManager
}

setup_dirs_and_log() {
  log "Creating directories..."
  mkdir -p "$MENU_INSTALL_DIR" "$ICON_DIR" "$CFG_DIR"
  touch "$LOG_FILE"
}

setup_venv_and_deps() {
  log "Setting up Python venv + deps..."
  if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"
  fi
  "$VENV_DIR/bin/pip3" install --upgrade pip
  # Always latest, deliberately unpinned. If the touchscreen ever renders
  # wrong/rotated after a fresh install, check here first — menu.py's
  # update_lcd() was written against 0.10.0's PILHelper/rotation behavior.
  "$VENV_DIR/bin/pip3" install streamdeck hidapi pillow

  local installed_version
  installed_version=$("$VENV_DIR/bin/pip3" show streamdeck 2>/dev/null | awk '/^Version:/{print $2}')
  log "Installed streamdeck package: ${installed_version:-unknown}"
}

deploy_files() {
  log "Deploying menu + icons..."
  install -m 0755 "$MENU_PY_SRC" "$MENU_INSTALL_DIR/menu.py"
  install -m 0644 "$ICON_COMP_SRC" "$ICON_DIR/comp256x256.png"
  install -m 0644 "$ICON_SAT_SRC" "$ICON_DIR/sat256x256.png"
}

write_systemd_service() {
  log "Writing systemd service: $SERVICE_FILE"
  cat > "$SERVICE_FILE" <<'EOF'
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
  systemctl enable menu
}

main() {
  need_root
  check_arch
  check_sources

  apt_install
  install_bitfocus
  disable_services
  ensure_plugdev
  write_udev_rule
  enable_networkmanager
  setup_dirs_and_log
  setup_venv_and_deps
  deploy_files
  write_systemd_service
  cleanup_apt

  log "DONE."
  log "Follow logs: journalctl -u menu -f"
  log "If StreamDeck permissions don't apply yet: replug USB or reboot."

  if [[ -f /var/run/reboot-required ]]; then
    log "NOTE: A reboot is required (kernel/library update) before changes fully take effect."
  fi
}

main "$@"
