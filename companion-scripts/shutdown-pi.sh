#!/usr/bin/env bash
# Meant to be pointed at by a Companion "Run shell path" button action.
# The companion system user already has passwordless sudo for /sbin/shutdown
# (granted by Bitfocus's own installer, /etc/sudoers.d/090-companion_sudo).
set -euo pipefail
exec sudo /sbin/shutdown -h now
