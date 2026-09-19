#!/usr/bin/env bash
# Meant to be pointed at by a Companion "Run shell path" button action, as:
#   sudo /opt/companion-scripts/back-to-menu.sh
# The companion system user has passwordless sudo for exactly this script
# (/etc/sudoers.d/091-menu-scripts), since it needs root to stop the
# service, reset the USB device, and start menu.service.
#
# Stops whichever surface service (companion/satellite) is running, then
# releases the Stream Deck back to the kernel's generic HID driver the same
# way menu.py's own handoff_to() does when going the other direction:
# deauthorize/reauthorize the USB port so a fresh /dev/hidraw* node appears,
# free of any stale state/permissions from the previous owner's session.
# Without this, the menu can fail to reopen the device after a handoff.
set -uo pipefail

HID_VENDOR="0fd9"
LOG_FILE="/var/log/menu.log"

log() {
  echo "[$(date +'%F %T')] back-to-menu: $*" >> "$LOG_FILE" 2>/dev/null || true
}

if [[ "${EUID}" -ne 0 ]]; then
  echo "Must be run as root, e.g.: sudo $0" >&2
  exit 1
fi

# Companion spawns this script as a child of its own process, which means
# it inherits companion.service's cgroup — plain backgrounding (&, nohup,
# setsid) does NOT escape a cgroup, only an explicit move does. Without
# detaching first, `systemctl stop companion` below kills companion AND
# this script together (systemd's default KillMode=control-group kills
# everything in the unit's cgroup on stop), so the script never reaches
# the USB reset or `systemctl start menu`, and Companion never gets a
# clean shutdown to release the device. Re-exec into an independent
# transient unit before doing anything else so this survives that kill.
if [[ -z "${MENU_HANDOFF_DETACHED:-}" ]]; then
  exec systemd-run --unit="menu-handoff-$$" --collect \
    --setenv=MENU_HANDOFF_DETACHED=1 \
    "$0" "$@"
fi

for svc in companion satellite; do
  if systemctl is-active --quiet "$svc"; then
    log "Stopping $svc..."
    systemctl stop "$svc"
  fi
done

# Give the kernel a moment to finish closing the fds the stopped process held.
sleep 0.3

# USB port-level deauthorize/reauthorize: the most reliable way to make the
# kernel fully tear down and re-enumerate the device.
usb_path=""
for f in /sys/bus/usb/devices/*/idVendor; do
  [[ -r "$f" ]] || continue
  if [[ "$(tr '[:upper:]' '[:lower:]' < "$f")" == "$HID_VENDOR" ]]; then
    usb_path="$(dirname "$f")"
    break
  fi
done

if [[ -n "$usb_path" ]]; then
  log "USB deauthorize: $usb_path"
  echo 0 > "$usb_path/authorized" 2>/dev/null || log "deauthorize write failed"
  sleep 0.8
  log "USB reauthorize"
  echo 1 > "$usb_path/authorized" 2>/dev/null || log "reauthorize write failed"
else
  log "USB device path not found, skipping reset"
fi

# No wait-for-hidraw loop here: menu.py accesses the device via libusb
# directly (confirmed by reading python-elgato-streamdeck's transport
# source), not through /dev/hidraw, so hidraw reappearing was never
# actually what menu.py depends on -- it only exists in the brief window
# where nothing has claimed the device via libusb yet. menu.py already
# retries DeviceManager().enumerate() once a second on its own startup
# loop until the device shows up, so there's nothing useful to wait for
# here. An earlier version of this script polled for a hidraw node
# anyway; on real hardware that polling loop itself was observed taking
# up to ~30s (heavy on external `readlink`/`dirname` forks) even though
# it wasn't checking anything menu.py needed, so it's been removed.

# Give udev a moment to apply permission rules after the USB reset.
sleep 0.5

log "Starting menu..."
systemctl start menu
