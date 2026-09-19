# StreamdeckXL Pi Boot Menu

A boot-time picker for a Raspberry Pi running an Elgato Stream Deck + XL: press
a key to hand the physical device over to either [Bitfocus
Companion](https://bitfocus.io/companion) or [Companion
Satellite](https://bitfocus.io/companion-satellite), with a dial-driven screen
for setting a static IP or switching back to DHCP.

## What it does

- Installs the latest **stable** release of Companion and Satellite (via
  Bitfocus's own installers), but leaves both services disabled — the menu
  decides which one runs.
- Installs the [python-elgato-streamdeck](https://github.com/abcminiuser/python-elgato-streamdeck)
  library into a dedicated venv, pinned to an exact version (`streamdeck==0.10.0`,
  the first PyPI release with native Stream Deck + XL support) — `menu.py`'s
  touchscreen drawing depends on that version's specific API/rotation
  behavior, so upgrading it requires re-verifying that code, not just
  bumping a floor.
- Installs the menu app itself as a systemd service (`menu.service`) that
  starts on boot, shows COMPANION/SATELLITE keys plus a network-config
  touchscreen, and hands off the USB device cleanly to whichever service you
  pick.

## Requirements

- Raspberry Pi (or any Debian-based board) running a **64-bit** OS —
  Companion/Satellite only ship `arm64`/`amd64` builds.
- An Elgato Stream Deck + XL connected via USB.
- Root access.

## Install

Run this on the Pi:

```bash
curl -fsSL https://raw.githubusercontent.com/shaelr/StreamdeckXL-Pi-Boot-Menu/main/install.sh | sudo bash
```

This looks up the latest [GitHub Release](../../releases) of this repo,
clones that tag to a temp directory, and runs
[`installer/install.sh`](installer/install.sh) from it, which does a full
`apt update`/`upgrade`, installs Companion + Satellite, deploys the menu app,
and enables everything to start on boot. `main` can move ahead independently —
only tagged releases get installed by the one-liner.

Alternatively, clone a specific release yourself and run the installer
directly:

```bash
git clone --branch <tag> https://github.com/shaelr/StreamdeckXL-Pi-Boot-Menu.git
cd StreamdeckXL-Pi-Boot-Menu/installer
sudo ./install.sh
```

## Layout

```
install.sh          # one-liner bootstrap: resolves latest release, clones it, hands off to installer/install.sh
installer/
  install.sh         # the real installer
menu/
  menu.py            # the chooser app, deployed to /opt/menu/menu.py
  icons/             # deployed to /opt/menu/icons/
```

## After installing

- Logs: `journalctl -u menu -f`
- If a reboot is flagged as required (kernel/library update during install),
  reboot once before expecting StreamDeck permissions to fully apply.
- Left key hands off to Companion, right key to Satellite; the touchscreen
  dials edit IP/mask, with buttons to switch between DHCP and a manual static
  address.
