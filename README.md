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
  library into a dedicated venv, always the latest release from PyPI (native
  Stream Deck + XL support landed in `0.10.0`). This is deliberately
  unpinned, so `menu.py`'s touchscreen drawing — written against `0.10.0`'s
  specific PILHelper/rotation behavior — is the first place to check if the
  touchscreen ever renders wrong after a fresh install following a future
  `streamdeck` release.
- Installs the menu app itself as a systemd service (`menu.service`) that
  starts on boot, shows COMPANION/SATELLITE keys plus a network-config
  touchscreen, and hands off the USB device cleanly to whichever service you
  pick.
- Enables Companion's built-in "Run shell command" action (off by default
  upstream) via a systemd drop-in, and deploys three scripts to
  `/opt/companion-scripts/` for Companion buttons to call: shut down the
  Pi, reboot it, and hand the Stream Deck back to the menu (see below).

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

This clones the latest commit on `main` to a temp directory and runs
[`installer/install.sh`](installer/install.sh) from it, which does a full
`apt update`/`upgrade`, installs Companion + Satellite, deploys the menu app,
and enables everything to start on boot. It always installs whatever's
newest on `main` — there are no release tags to keep up to date.

Alternatively, clone the repo yourself and run the installer directly:

```bash
git clone https://github.com/shaelr/StreamdeckXL-Pi-Boot-Menu.git
cd StreamdeckXL-Pi-Boot-Menu/installer
sudo ./install.sh
```

## Layout

```
install.sh          # one-liner bootstrap: clones main, hands off to installer/install.sh
installer/
  install.sh         # the real installer
menu/
  menu.py            # the menu app, deployed to /opt/menu/menu.py
  icons/             # deployed to /opt/menu/icons/
companion-scripts/   # deployed to /opt/companion-scripts/, for Companion buttons to call
```

## After installing

- Logs: `journalctl -u menu -f`
- If a reboot is flagged as required (kernel/library update during install),
  reboot once before expecting StreamDeck permissions to fully apply.
- Left key hands off to Companion, right key to Satellite; the touchscreen
  dials edit IP/mask, with buttons to switch between DHCP and a manual static
  address.

### Companion "Run shell path" buttons

Shell commands are enabled in Companion, so a button using its internal
**Run shell path** action can point at:

| Script | Does |
| --- | --- |
| `/opt/companion-scripts/shutdown-pi.sh` | Shuts the Pi down |
| `/opt/companion-scripts/reboot-pi.sh` | Reboots the Pi |
| `sudo /opt/companion-scripts/back-to-menu.sh` | Stops Companion, releases the Stream Deck, and starts the menu |

The first two just call `sudo /sbin/shutdown`/`/sbin/reboot`, already
permitted passwordless for the `companion` user by Bitfocus's own installer.
`back-to-menu.sh` needs root itself (to reset the USB device and switch
services), so its button must include the leading `sudo` — the installer
grants exactly that one command passwordless via a dedicated sudoers
drop-in, nothing broader. It stops whichever surface service is running,
then deauthorizes/reauthorizes the Stream Deck's USB port so a fresh device
node appears free of any stale state from Companion's session — the same
release dance `menu.py` does when handing off in the other direction —
before starting `menu.service`.

### Timezone-switching Companion buttons (manual setup)

Not deployed by the installer — this is a personal setup note for adding
one-press timezone switching (e.g. touring between US/Canada markets)
directly in Companion, since `menu.py` has no timezone UI of its own.

Grant the `companion` user passwordless sudo for changing the system
timezone and for restarting Companion:

```bash
sudo tee /etc/sudoers.d/092-timezone > /dev/null <<'EOF'
companion ALL=NOPASSWD: /usr/bin/timedatectl set-timezone *
companion ALL=NOPASSWD: /usr/bin/systemctl restart companion
EOF
sudo visudo -cf /etc/sudoers.d/092-timezone && sudo chmod 440 /etc/sudoers.d/092-timezone
```

Companion restarting on every zone change isn't optional cleanup — it's
required. Companion's own internal time/date variables only resolve the OS
timezone once per process (a Node.js/V8 runtime limitation, not a Companion
bug), so without a restart they silently keep showing the *previous* zone
even after `timedatectl` has already changed. Since this is a "press once
when you land in a new city" action rather than something done mid-show, a
few seconds of Companion restarting is an acceptable trade for the internal
variables actually being correct afterward.

One **Run shell path** button per zone, each command chained with `&&` so
the restart only fires after the zone actually changes successfully:

| Button | Command |
| --- | --- |
| EASTERN | `sudo /usr/bin/timedatectl set-timezone America/New_York && sudo systemctl restart companion` |
| CENTRAL | `sudo /usr/bin/timedatectl set-timezone America/Chicago && sudo systemctl restart companion` |
| MOUNTAIN | `sudo /usr/bin/timedatectl set-timezone America/Denver && sudo systemctl restart companion` |
| PACIFIC | `sudo /usr/bin/timedatectl set-timezone America/Los_Angeles && sudo systemctl restart companion` |
| ARIZONA | `sudo /usr/bin/timedatectl set-timezone America/Phoenix && sudo systemctl restart companion` |

(`America/Toronto`/`Winnipeg`/`Edmonton`/`Vancouver`/`Whitehorse` are the
Canadian equivalents of Eastern/Central/Mountain/Pacific/Arizona
respectively — identical clock behavior, just a different IANA label, so
swap in whichever name you'd rather see if you prefer the Canadian city.)

A readout button showing the active zone, using Companion's own
`$(internal:timezone)` variable — accurate here specifically *because*
every button above restarts Companion, so it's never stale — via a text
expression:

```
includes($(internal:timezone), 'New_York') ? 'EASTERN' :
includes($(internal:timezone), 'Chicago') ? 'CENTRAL' :
includes($(internal:timezone), 'Denver') ? 'MOUNTAIN' :
includes($(internal:timezone), 'Los_Angeles') ? 'PACIFIC' :
includes($(internal:timezone), 'Phoenix') ? 'ARIZONA' :
$(internal:timezone)
```

### RTC (real-time clock) battery module (manual setup)

Not deployed by the installer — a personal reference for enabling a
battery-backed RTC so the Pi keeps correct time offline (e.g. between shows,
before it reaches a network with NTP).

**Raspberry Pi 5:** has an onboard RTC already; connecting a battery to its
RTC battery header is enough, no software setup needed.

**Any other Pi:** needs an add-on I2C RTC module. Steps below assume a
DS3231 (the common cheap-module chip) — swap `ds3231` for whatever chip your
specific board actually uses if different.

```bash
# Enable I2C
sudo raspi-config nonint do_i2c 0

# Add the overlay for your RTC chip
echo "dtoverlay=i2c-rtc,ds3231" | sudo tee -a /boot/firmware/config.txt

sudo reboot
```

After rebooting, remove `fake-hwclock` (it fakes an RTC by saving/restoring
system time across boots — only useful on boards *without* a real one, and
it can conflict with a genuine RTC if left in place):

```bash
sudo apt-get remove -y fake-hwclock
```

Confirm the RTC is actually being read:

```bash
sudo hwclock -r
```

Then set the correct time once (from network, or manually) and write it to
the RTC so the battery carries it forward from here:

```bash
sudo hwclock -w
```
