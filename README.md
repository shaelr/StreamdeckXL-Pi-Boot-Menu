# StreamdeckXL Pi Boot Menu

A boot-time picker for a Raspberry Pi running an Elgato Stream Deck + XL: press
a key to hand the physical device over to either [Bitfocus
Companion](https://bitfocus.io/companion) or [Companion
Satellite](https://bitfocus.io/companion-satellite), with a dial-driven screen
for setting a static IP or switching back to DHCP.

## Requirements

- Raspberry Pi (or any Debian-based board) running a **64-bit** OS —
  Companion/Satellite only ship `arm64`/`amd64` builds.
- An Elgato Stream Deck + XL connected via USB.
- Root access.

## Install

Run this on the Pi:

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/shaelr/StreamdeckXL-Pi-Boot-Menu/main/install.sh)"
```

That clones this repo to `/opt/sdpi`, adds the `sdpi` command, and opens it.
Pick **Install → Everything** for a first-time setup. After that, run it any
time to install, update or remove individual features:

```bash
sudo sdpi
```

```
 StreamdeckXL Pi Boot Menu setup        main @ a1b2c3d
 ------------------------------------------------------------------
  Menu app             Installed (streamdeck 0.10.0, running)
  Companion            Installed (v5.0.6)
  Satellite            Installed (v3.4.0)
  Companion scripts    Installed (/opt/companion-scripts)
  RTC module           Not installed
 ------------------------------------------------------------------
  1) Install    2) Update    3) Remove    4) Advanced    Q) Quit
```

(The one-liner uses `bash -c "$(curl ...)"` rather than `curl ... | bash` so
the script's input stays attached to your keyboard for sdpi's prompts. The old
piped form still works; sdpi reattaches to the terminal itself.)

## Features

| Feature | What it installs |
| --- | --- |
| **Menu app** | `menu/menu.py` in a venv at `/opt/menu`, run as `menu.service`. Shows COMPANION/SATELLITE keys plus a touchscreen for static IP / DHCP, and hands the Stream Deck to whichever you pick. A key turns grey if that one isn't installed. Uses the [python-elgato-streamdeck](https://github.com/abcminiuser/python-elgato-streamdeck) library, deliberately unpinned: if the touchscreen ever renders wrong after an update, `menu.py`'s `update_lcd()` (written against `streamdeck` 0.10.0) is the first place to look. |
| **Companion** | The latest **stable** Bitfocus Companion via Bitfocus's own installer, disabled at boot so the menu decides when it runs. Enables Companion's "Run shell command" action (off by default upstream). |
| **Satellite** | The latest **stable** Companion Satellite via Bitfocus's own installer, also disabled at boot. |
| **Companion scripts** | Shutdown, reboot and back-to-menu scripts in `/opt/companion-scripts` for Companion buttons (see below). Needs Companion. |
| **RTC module** | Sets up a battery-backed real-time clock (see below). |

**Update** has two quick options: **Everything** (this project, Companion,
Satellite and system packages), or **This project only** (pulls the latest code
and redeploys the menu app and Companion scripts, skipping the slow Companion,
Satellite and apt steps).

**Remove** asks whether to keep each feature's saved configuration (e.g.
Companion's buttons and pages) so a reinstall can bring it back, or delete it.

### Testing a branch

Install from another branch:

```bash
sudo BRANCH=test bash -c "$(curl -fsSL https://raw.githubusercontent.com/shaelr/StreamdeckXL-Pi-Boot-Menu/test/install.sh)"
```

Or switch an existing install with **Advanced → Switch branch** in sdpi.

## Layout

```
install.sh          # one-liner bootstrap: clones the repo to /opt/sdpi and opens sdpi
sdpi                # the setup manager (install / update / remove)
lib/common.sh       # shared helpers for sdpi and its modules
modules/            # one file per feature: install, update, remove and status
menu/
  menu.py           # the menu app, deployed to /opt/menu/menu.py
  icons/            # deployed to /opt/menu/icons/
companion-scripts/  # deployed to /opt/companion-scripts/, for Companion buttons to call
```

## After installing

- Logs: `journalctl -u menu -f`, or **Advanced → View menu app log** in sdpi.
- If sdpi says a reboot is needed (kernel update, RTC setup), reboot before
  expecting everything to apply. sdpi offers to reboot when you quit.
- Left key hands off to Companion, right key to Satellite; the touchscreen
  dials edit IP/mask, with buttons to switch between DHCP and a manual static
  address.

### Companion "Run shell command" buttons

Shell commands are enabled in Companion, so a button using its internal
**internal: System: Run shell command (local)** action can point at:

| Script | Does |
| --- | --- |
| `/opt/companion-scripts/shutdown-pi.sh` | Shuts the Pi down |
| `/opt/companion-scripts/reboot-pi.sh` | Reboots the Pi |
| `sudo /opt/companion-scripts/back-to-menu.sh` | Stops Companion, releases the Stream Deck, and starts the menu |

The first two just call `sudo /sbin/shutdown`/`/sbin/reboot`, already
permitted passwordless for the `companion` user by Bitfocus's own installer.
`back-to-menu.sh` needs root itself (to reset the USB device and switch
services), so its button must include the leading `sudo` — the Companion
scripts feature grants exactly that one command passwordless via a dedicated
sudoers drop-in, nothing broader. It stops whichever surface service is running,
then deauthorizes/reauthorizes the Stream Deck's USB port so a fresh device
node appears free of any stale state from Companion's session — the same
release dance `menu.py` does when handing off in the other direction —
before starting `menu.service`.

### Timezone-switching Companion buttons (manual setup)

Not set up by sdpi — this is a personal setup note for adding
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

One **Run shell command** button per zone, each command chained with `&&` so
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

### RTC (real-time clock) battery module

Keeps correct time with no network (e.g. between shows, before the Pi reaches
a network with NTP). Set it up from **sdpi → Install → RTC module**; **Install
→ Everything** also asks whether you have one.

**Raspberry Pi 5:** has an onboard RTC; connecting a battery to its RTC header
is enough. sdpi detects a Pi 5 and skips the setup.

**Any other Pi** needs an add-on I2C RTC module. sdpi:

1. Enables I2C and installs `i2c-tools` and `util-linux-extra` (which provides
   `hwclock` on trixie).
2. Scans the bus with `i2cdetect -y 1` to confirm the module is wired up, and
   offers only the chips that match the address it finds: `0x68` is a DS3231,
   DS1307 or PCF8523; `0x51` is a PCF8563 or PCF85063. If nothing answers, it
   points you at the wiring (SDA to GPIO2/pin 3, SCL to GPIO3/pin 5).
3. Adds `dtoverlay=i2c-rtc,<chip>` to `/boot/firmware/config.txt` inside a
   marked block (backup saved as `config.txt.sdpi-bak`). Any hand-added
   `i2c-rtc` line is replaced so there's only ever one.
4. Installs `/etc/udev/rules.d/85-sdpi-rtc.rules`, which runs
   `hwclock --hctosys` when the RTC appears at boot. This is needed because the
   Pi kernel builds RTC drivers as modules, so its own boot-time RTC read runs
   before the driver exists, and trixie no longer ships a hook to do it later.
5. Removes `fake-hwclock`, which fakes an RTC by saving the time at shutdown
   and would conflict with the real one.

Reboot afterwards. Once the Pi is online, the kernel copies network time into
the RTC every 11 minutes on its own; **Update → RTC module** writes it
immediately. Check it with `sudo hwclock -r`. **Remove** takes the overlay and
udev rule back out and reinstalls `fake-hwclock`.
