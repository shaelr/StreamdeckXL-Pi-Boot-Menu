# shellcheck shell=bash
# Shared helpers for sdpi and its modules. Sourced, not executed.
# The modules use these variables, which shellcheck can't see from here:
# shellcheck disable=SC2034

# Deployed locations on the Pi. Companion buttons and the sudoers rule point at
# /opt/companion-scripts by path, so these must not move.
MENU_INSTALL_DIR="/opt/menu"
MENU_VENV="$MENU_INSTALL_DIR/venv"
MENU_ICON_DIR="$MENU_INSTALL_DIR/icons"
MENU_CFG_DIR="/etc/menu"
MENU_LOG="/var/log/menu.log"
MENU_SERVICE_FILE="/etc/systemd/system/menu.service"
STREAMDECK_UDEV_RULE="/etc/udev/rules.d/70-streamdeck.rules"
COMPANION_SCRIPTS_DIR="/opt/companion-scripts"
COMPANION_SCRIPTS_SUDOERS="/etc/sudoers.d/091-menu-scripts"
COMPANION_OVERRIDE_DIR="/etc/systemd/system/companion.service.d"
REBOOT_FLAG="/var/run/reboot-required"

CURL_OPTS=(-fsSL --retry 3 --retry-delay 5)

if [[ -t 1 ]]; then
  C_RED=$'\e[31m' C_GRN=$'\e[32m' C_YLW=$'\e[33m' C_DIM=$'\e[2m' C_BLD=$'\e[1m' C_RST=$'\e[0m'
else
  C_RED="" C_GRN="" C_YLW="" C_DIM="" C_BLD="" C_RST=""
fi

log()  { echo "${C_BLD}==>${C_RST} $*"; }
warn() { echo "${C_YLW}WARNING:${C_RST} $*" >&2; }
err()  { echo "${C_RED}ERROR:${C_RST} $*" >&2; }

line() { echo " ${C_DIM}------------------------------------------------------------------${C_RST}"; }

pause() { read -r -p " Press Enter to continue..." _ || true; }

# confirm "Question" [y|n]  — returns 0 for yes. The second arg is the default.
confirm() {
  local prompt="$1" default="${2:-n}" hint answer
  if [[ "$default" == y ]]; then hint="[Y/n]"; else hint="[y/N]"; fi
  while true; do
    read -r -p "$prompt $hint " answer || return 1
    answer="${answer:-$default}"
    case "$answer" in
      y|Y|yes|Yes|YES) return 0 ;;
      n|N|no|No|NO) return 1 ;;
    esac
  done
}

# choose "Prompt" option... — prints the picked option. The list goes to stderr
# so callers can capture the answer with $(choose ...).
choose() {
  local prompt="$1" i=1 opt answer
  shift
  for opt in "$@"; do
    echo "  $i) $opt" >&2
    i=$((i + 1))
  done
  while true; do
    read -r -p "$prompt " answer || return 1
    if [[ "$answer" =~ ^[0-9]+$ ]] && (( answer >= 1 && answer <= $# )); then
      echo "${!answer}"
      return 0
    fi
  done
}

# Ask before deleting a component's saved configuration. Returns 0 to delete.
ask_delete_data() {
  local what="$1" p shown=0
  shift
  for p in "$@"; do
    if [[ -e "$p" ]]; then
      (( shown )) || echo "$what's saved configuration:"
      echo "  $p"
      shown=1
    fi
  done
  (( shown )) || return 1
  confirm "Delete it too? No keeps it, so reinstalling brings your setup back." n
}

is_active() { systemctl is-active --quiet "$1"; }

require_64bit() {
  local arch
  arch="$(dpkg --print-architecture)"
  case "$arch" in
    arm64|amd64) ;;
    *) err "Unsupported CPU architecture: $arch. Bitfocus only ships arm64/amd64 builds; re-flash with 64-bit Raspberry Pi OS."
       return 1 ;;
  esac
}

pi_model() { tr -d '\0' 2>/dev/null < /proc/device-tree/model || true; }

boot_config_file() {
  if [[ -f /boot/firmware/config.txt ]]; then echo /boot/firmware/config.txt; else echo /boot/config.txt; fi
}

# Some components change systemd units or udev rules the menu depends on
# (e.g. which handoff keys are available). Restart it so it redraws.
menu_redraw_if_running() {
  if is_active menu; then systemctl restart menu; fi
}

remove_motd_link() {
  local target
  if [[ -L /etc/motd ]]; then
    target="$(readlink /etc/motd)"
    if [[ "$target" == "$1"/* ]]; then rm -f /etc/motd; fi
  fi
}

# ---- apt ----

wait_for_apt() {
  local locks=(/var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/cache/apt/archives/lock) l busy
  while true; do
    busy=0
    for l in "${locks[@]}"; do
      if fuser "$l" >/dev/null 2>&1; then busy=1; fi
    done
    (( busy )) || return 0
    log "apt/dpkg is busy (automatic updates?). Waiting..."
    sleep 3
  done
}

_SDPI_APT_UPDATED=0
apt_update() {
  (( _SDPI_APT_UPDATED )) && return 0
  wait_for_apt
  log "Updating package lists..."
  apt-get update
  _SDPI_APT_UPDATED=1
}

apt_install() {
  export DEBIAN_FRONTEND=noninteractive
  apt_update
  wait_for_apt
  apt-get install -y "$@"
}

system_upgrade() {
  export DEBIAN_FRONTEND=noninteractive
  wait_for_apt
  log "Making sure dpkg is in a consistent state..."
  dpkg --configure -a || true
  apt-get -f install -y || true
  apt_update
  wait_for_apt
  log "Upgrading system packages..."
  apt-get upgrade -y
}

apt_cleanup() {
  log "Cleaning up apt caches..."
  apt-get autoremove -y || true
  apt-get clean || true
}
