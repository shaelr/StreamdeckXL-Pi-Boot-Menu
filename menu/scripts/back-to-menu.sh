#!/usr/bin/env bash
# Meant to be pointed at by a Companion "Run shell path" button action, as:
#   sudo /opt/menu/scripts/back-to-menu.sh
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

# Wait for a fresh hidraw node to reappear (matches menu.py's _wait_for_hidraw).
found=0
for _ in $(seq 1 40); do
  for hr in /sys/class/hidraw/hidraw*; do
    [[ -e "$hr/device" ]] || continue
    real="$(readlink -f "$hr/device" 2>/dev/null)" || continue
    p="$real"
    while [[ -n "$p" && "$p" != "/" ]]; do
      if [[ -r "$p/idVendor" ]] && [[ "$(tr '[:upper:]' '[:lower:]' < "$p/idVendor")" == "$HID_VENDOR" ]]; then
        found=1
        break 2
      fi
      p="$(dirname "$p")"
    done
  done
  [[ "$found" -eq 1 ]] && break
  sleep 0.2
done
[[ "$found" -eq 1 ]] || log "WARNING: hidraw node did not reappear within timeout"

# Give udev a moment to apply permission rules.
sleep 0.5

log "Starting menu..."
systemctl start menu
