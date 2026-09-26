#!/usr/bin/env bash
# One-liner bootstrap. Run on the Pi:
#   sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/shaelr/StreamdeckXL-Pi-Boot-Menu/main/install.sh)"
# Another branch:
#   sudo BRANCH=test bash -c "$(curl -fsSL https://raw.githubusercontent.com/shaelr/StreamdeckXL-Pi-Boot-Menu/test/install.sh)"
#
# Clones (or updates) the repo into /opt/sdpi, puts the `sdpi` setup manager on
# the PATH and launches it. After that, run `sudo sdpi` any time.
set -euo pipefail

REPO_URL="https://github.com/shaelr/StreamdeckXL-Pi-Boot-Menu.git"
SDPI_HOME="/opt/sdpi"
BRANCH="${BRANCH:-main}"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash -c \"\$(curl -fsSL <url>)\"" >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "Installing git..."
  apt-get update
  apt-get install -y git
fi

if [[ -d "$SDPI_HOME/.git" ]]; then
  echo "Updating $SDPI_HOME to $BRANCH..."
  git -C "$SDPI_HOME" fetch --quiet origin "$BRANCH"
  git -C "$SDPI_HOME" checkout --quiet -B "$BRANCH" "origin/$BRANCH"
  git -C "$SDPI_HOME" reset --hard --quiet "origin/$BRANCH"
else
  echo "Downloading to $SDPI_HOME ($BRANCH)..."
  git clone --quiet -b "$BRANCH" "$REPO_URL" "$SDPI_HOME"
fi

chmod +x "$SDPI_HOME/sdpi"
ln -sf "$SDPI_HOME/sdpi" /usr/local/bin/sdpi

exec "$SDPI_HOME/sdpi"
