#!/usr/bin/env bash
set -euo pipefail

# ==========================================================
# One-liner bootstrap:
#   curl -fsSL https://raw.githubusercontent.com/shaelr/StreamdeckXL-Pi-Boot-Menu/main/install.sh | sudo bash
#
# Clones the latest main branch commit to a temp dir and runs the real
# installer (installer/install.sh) from there, so it can find the app
# source in ../menu alongside it. Always installs whatever's newest on
# main — no release tags to keep updated.
# ==========================================================

REPO_URL="https://github.com/shaelr/StreamdeckXL-Pi-Boot-Menu.git"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: curl -fsSL <url> | sudo bash"
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "Installing git..."
  apt-get update
  apt-get install -y git
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Cloning ${REPO_URL}..."
git clone --depth 1 "$REPO_URL" "$TMP_DIR/repo"

exec bash "$TMP_DIR/repo/installer/install.sh"
