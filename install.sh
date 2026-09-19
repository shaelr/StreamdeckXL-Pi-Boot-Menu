#!/usr/bin/env bash
set -euo pipefail

# ==========================================================
# One-liner bootstrap:
#   curl -fsSL https://raw.githubusercontent.com/shaelr/StreamdeckXL-Pi-Boot-Menu/main/install.sh | sudo bash
#
# Looks up the latest GitHub Release (not just main HEAD), clones that
# tag to a temp dir, and runs the real installer (Installer/Installer.sh)
# from there, so it has all the sibling files (menu.py, icons, custom
# StreamDeck modules) it needs alongside it.
# ==========================================================

REPO="shaelr/StreamdeckXL-Pi-Boot-Menu"
REPO_URL="https://github.com/${REPO}.git"
LATEST_RELEASE_API="https://api.github.com/repos/${REPO}/releases/latest"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: curl -fsSL <url> | sudo bash"
  exit 1
fi

MISSING_PKGS=()
command -v curl >/dev/null 2>&1 || MISSING_PKGS+=(curl ca-certificates)
command -v git  >/dev/null 2>&1 || MISSING_PKGS+=(git)
if [[ "${#MISSING_PKGS[@]}" -gt 0 ]]; then
  echo "Installing ${MISSING_PKGS[*]}..."
  apt-get update
  apt-get install -y "${MISSING_PKGS[@]}"
fi

echo "Looking up latest stable release..."
TAG="$(curl -fsSL "$LATEST_RELEASE_API" | grep -m1 '"tag_name"' | sed -E 's/.*"tag_name":[[:space:]]*"([^"]+)".*/\1/')"

if [[ -z "$TAG" ]]; then
  echo "Could not determine latest release tag from ${LATEST_RELEASE_API}" >&2
  exit 1
fi
echo "Latest stable release: ${TAG}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Cloning ${REPO_URL} @ ${TAG}..."
git clone --depth 1 --branch "$TAG" "$REPO_URL" "$TMP_DIR/repo"

exec bash "$TMP_DIR/repo/Installer/Installer.sh"
