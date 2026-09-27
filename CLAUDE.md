# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A setup manager (`sdpi`) + app that turns a Raspberry Pi with an Elgato Stream
Deck + XL into a boot-time picker between Bitfocus Companion and Companion
Satellite, with a touchscreen for static IP / DHCP configuration and a web
control page for the same controls. There is no build system, package manager,
or test suite. It's bash plus three Python files (`menu/menu.py`,
`menu/pi_control.py`, `menu/web.py`) and one HTML page, deployed directly onto
a Pi's filesystem as systemd services.

## Commands

There is no build/lint/test tooling in the repo. Verification is:

- Shell scripts: `shellcheck -x -s bash sdpi install.sh lib/common.sh modules/*.sh companion-scripts/*.sh`
  (not installed on this Mac; `pip install shellcheck-py` into a throwaway
  venv works) plus `bash -n <script>`.
- Python: `python3 -m py_compile menu/*.py` (then remove the generated `__pycache__`).
- Local bash is 3.2; the Pi runs bash 5. Code targets bash 5 (e.g. empty
  arrays under `set -u` are fine there, not on 3.2). `sdpi` only runs `main`
  when executed, so it can be `source`d with system commands stubbed out to
  smoke-test menus and module logic on a Mac.
- Real behavior can only be verified on **actual hardware** — a Raspberry Pi
  (64-bit OS) with a physical Stream Deck + XL attached. When reasoning about
  a change, say explicitly if it hasn't been confirmed on hardware.
- On the Pi: `sudo sdpi`. Fresh install / another branch:
  `sudo [BRANCH=test] bash -c "$(curl -fsSL https://raw.githubusercontent.com/shaelr/StreamdeckXL-Pi-Boot-Menu/<branch>/install.sh)"`.
- No release/tag workflow: the one-liner and sdpi's self-update track a
  branch (`main` by default). Every push to `main` is immediately what gets
  installed — don't reintroduce GitHub Releases pinning without being asked.
  Put in-progress work on a `test` branch (created fresh from `main` when
  needed; it's deleted after each merge) and merge once it's verified. A Pi
  left on a deleted branch gets a clear error from Update and needs
  **Advanced → Switch branch**.

## Architecture

**Bootstrap:** `install.sh` (the published one-liner) clones or updates the
repo at `/opt/sdpi` (branch from `$BRANCH`, default `main`), symlinks
`/usr/local/bin/sdpi` to it, and execs `sdpi`. The checkout stays on the Pi:
sdpi updates itself with `git fetch` + `reset --hard origin/<branch>`, then
re-execs with a `--continue-update-*` flag so the rest of an update runs with
the new code. The one-liner uses `bash -c "$(curl ...)"` so stdin stays the
keyboard; `sdpi` also reattaches to `/dev/tty` if launched via `curl | bash`.

**`sdpi`** is a numbered text menu (Install / Update / Remove / Advanced) over
`MODULES=(menu web restart_key shutdown_key timezone companion satellite companion_scripts rtc)`. Each
`modules/<id>.sh` defines `<id>_label`, `<id>_installed`, `<id>_detail`,
`<id>_install`, `<id>_remove`, and optionally `<id>_update`. Status comes from
the Pi's actual state (unit files, BUILD files, the config.txt overlay line),
never a separate record, so hand-installed or old-installer setups show up
correctly. The one exception is the on-by-default features (web page,
restart/shutdown keys), which record an opt-out marker in `/etc/menu/` when
removed. `lib/common.sh` holds paths and shared helpers (`confirm`,
`choose`, `ask_delete_data`, apt locking). Modules deploy files from the
checkout into fixed paths (`/opt/menu`, `/opt/companion-scripts`) — Companion
buttons and the sudoers rule reference those by path, so don't move them.

**`run()` in `sdpi` executes each action in a `( set -eo pipefail; ... )`
subshell** so a failure stops that action and returns to the menu. Bash
silently disables errexit for anything executed inside an `if`/`&&`/`||`
condition — including functions and subshells — so never call `run` or a
module action in a condition (`if confirm ...; then run ...; fi` is fine, the
body isn't a condition). In module code, prefer `if ...; then ...; fi` over
`a && b` as a function's last line, which returns 1 and trips errexit.

**Companion/Satellite** are installed with Bitfocus's own installers (piped
from GitHub, `COMPANION_BUILD=stable` / `SATELLITE_BUILD=stable` env vars), then
disabled at boot so the menu decides which runs. Remove reverses what their
installers create (they ship no uninstaller) and asks whether to keep the
saved config (`/home/<user>`, `/etc/companion`, `/boot/satellite-config`).
**Updating** them runs Bitfocus's own `companion-update` / `satellite-update`,
so the user picks the version (user's request). Those wrappers always
`systemctl start` their app at the end, so sdpi stops it again if it wasn't
running before; otherwise it would fight the menu for the deck.
**Gotcha:** companion-pi's `update.sh` (run by its installer and by
`companion-update`) deletes `/opt/fnm` ("fnm is no longer used"), but
`satellite.service` *and* Satellite's `update.sh` run Node from `/opt/fnm`.
`satellite_restore_fnm` reinstalls just fnm and Satellite's Node version, the
same way Satellite's installer and updater do, without reinstalling
Satellite, so a picked version stays. It runs after any Companion
install/update (`_companion_restore_satellite_runtime`) and before
`satellite-update`. It's a no-op if fnm is intact.

**The core mechanic — one USB device, two mutually-exclusive owners:** the
Stream Deck + XL can only be claimed by one process at a time (`menu.py`'s
`python-elgato-streamdeck` transport claims it via **libusb directly**, not
`/dev/hidraw` — confirmed by reading the library's actual transport source,
not assumed). `menu.py`'s `handoff_to()` releases its own claim, deauthorizes/
reauthorizes the device's USB port to force a clean kernel-level
re-enumeration, and starts Companion or Satellite (it hides and ignores
the key for one that isn't installed, since handing off to nothing would leave
the deck dead). `companion-scripts/back-to-menu.sh` does the reverse, triggered
*from inside Companion* via its "Run shell command" button action, and refuses
if `menu.service` isn't installed. Modules that add/remove Companion or
Satellite restart the menu (if running) so it redraws those keys. The menu
module only starts the menu when neither Companion nor Satellite is active.

**Two hard-won gotchas in `back-to-menu.sh`, worth understanding before
touching it again:**
1. Because Companion spawns it as a child process, it inherits
   `companion.service`'s cgroup. Calling `systemctl stop companion` from
   inside it is a self-referential trap — systemd's default
   `KillMode=control-group` signals *every* process in the unit's cgroup on
   stop, killing the script alongside Companion before it can finish. The
   fix is the `systemd-run --unit=... --collect` self-re-exec at the top of
   the script, which detaches it into an independent transient unit first.
   Don't remove that without understanding why it's there.
2. There's no "wait for hidraw to reappear" step. An earlier version had one
   (mirroring `menu.py`'s own `_wait_for_hidraw()`, used going the other
   direction), but on real hardware it was observed stalling up to ~30s and
   wasn't even checking something `menu.py` depends on (libusb access
   doesn't need hidraw). `menu.py`'s own startup loop already retries
   `DeviceManager().enumerate()` every second, so nothing else needs to wait.

**RTC module:** on Pi 5 the RTC is built in and the module does nothing.
Otherwise it enables I2C, scans the bus (0x68 → DS3231/DS1307/PCF8523, 0x51 →
PCF8563/PCF85063; `UU` means a driver already owns it), writes
`dtoverlay=i2c-rtc,<chip>` into config.txt between `# BEGIN/END sdpi rtc`
markers (replacing any hand-added i2c-rtc line), removes `fake-hwclock`, and
installs `/etc/udev/rules.d/85-sdpi-rtc.rules` to run `hwclock --hctosys` when
rtc0 appears. That rule is load-bearing: the Pi kernel builds RTC drivers as
modules (`=m`), so the kernel's RTC_HCTOSYS boot read runs before the driver
exists, and trixie dropped the old util-linux hwclock-set udev hook (moved to
the sysvinit `initscripts` package, absent on systemd installs). The kernel's
RTC_SYSTOHC (default on) writes NTP time back to the RTC every 11 minutes.
The RTC path is not yet verified on hardware.

**`streamdeck` (PyPI) is deliberately unpinned**, not pinned to a tested
version — installed with `pip install --upgrade streamdeck`, always latest.
This was an explicit choice (see git history) despite `menu.py`'s touchscreen
drawing (`update_lcd()`) being written and verified against `0.10.0`'s
specific `PILHelper`/rotation behavior. If the touchscreen ever renders
wrong/rotated after an update, that version-behavior coupling is the first
thing to check, not a StreamDeck+XL hardware issue.

**Timezone buttons:** the `timezone` module just writes/removes
`/etc/menu/timezones.json` (a `[{label, zone}]` list, the user's five by
default) and restarts the menu; `menu.py` does the rest. With the file present
it centres up to 9 keys on the top row. Each shows the zone's tz-database
abbreviation, drawn large, with its current 24h time under it (24h to keep it narrow enough for a bigger font) (`tz_key_faces()`,
`img_tz()`; EDT/EST follow DST). The config `label` is added as a small third
line only when the abbreviation is numeric or shared; for example, Mountain and
Arizona are both MST in winter. This was the user's request. The active zone
is green and the rest black; the active zone is read from the `/etc/localtime`
symlink and rechecked every refresh tick, so SSH/sdpi changes show up. The
clocks redraw each minute, even during IP editing, and on press runs `timedatectl set-timezone` directly (it's root) and
calls `time.tzset()` so its own log timestamps follow. Setting the zone from the
menu *before* handing off means Companion starts fresh in it; that's why this
avoids the Companion restart the README's manual Companion timezone buttons
need. Not yet verified on hardware.

**Web control page** (`web` module, user's design, 2026-09-27): `menu/web.py`
serves `menu/web/index.html` plus a small JSON API on port 80, using only
Python's built-in `http.server`, as `menu-web.service` (root, always on,
`Restart=always`). It's deliberately separate from `menu.service`: it has to
work while Companion or Satellite has the deck. That's its reason to exist:
getting back from a Satellite host that has no back-to-menu, restart or
shutdown buttons. The network, timezone, logging and USB-reset code moved
from `menu.py` into `menu/pi_control.py` unchanged, and both apps import it.
`pi_control.py` must never import StreamDeck or Pillow. The two apps never
talk to each other; each reads the Pi's real state, and the menu's 1s
refresh loop picks up web changes. Deck switching from the web
(`pi_control.switch_deck`) stops whichever owner is active, resets the USB
port, then starts the target. That's the same sequence as `handoff_to()` and
`back-to-menu.sh`, which stay as they are because they run *inside* an owner.
Design decisions:
- **No password** (the user's choice). Companion's own UI is just as open.
  POSTs must be `application/json`, so a cross-site page can't trigger them
  without a CORS preflight, which the server never approves.
- **Same network options as the deck:** DHCP or manual IP + mask only;
  gateway/DNS are `.1`.
- **Timezone:** a dropdown of every zone from `timedatectl list-timezones`,
  independent of the deck's `timezones.json`.
- **Replies first:** network changes and power actions are applied ~0.5-1s
  *after* replying, because they cut the connection.
- **Following an IP change** depends on how the page was opened (the user's
  rule). If it was opened by name (`http://<hostname>.local`), there's no "Now
  at" screen: the page shows "Applying… reconnecting" and reloads once the same
  name answers. If it was opened by IP, it always shows "Now at
  http://<new ip>" as a failsafe and follows it once `/api/ping` answers there
  (`no-cors`). For DHCP it learns the new IP by fetching
  `http://<hostname>.local/api/ping`, which is CORS-enabled and returns
  `{"ip"}`, so it relies on Pi OS's avahi and on the client resolving `.local`.
  After ~30s it points to the deck's touchscreen instead.
- **On by default:** `menu_install` calls `web_install`; `update_project`
  calls `web_install_if_wanted`. `web_remove` leaves
  `/etc/menu/web-disabled` so updates don't re-add a page the user removed.
  `menu_remove` removes the web page first, since its code lives in
  `/opt/menu`.

Not yet verified on hardware.

**Restart/shutdown keys:** RESTART on key 18 (green, same as DHCP) and
SHUTDOWN on key 26 (red), with the user's `res256x256.png`/`pwr256x256.png`
icons. The two keys are on the third row at opposite ends, so reaching for one
won't hit the other. (Top row = timezone keys; bottom row = COMPANION 27,
DHCP 31, SATELLITE 35.) They're on by default and each is its own sdpi feature
(`restart_key`, `shutdown_key`; the user wanted them removable individually).
The modules invert the usual flag: removing one writes
`/etc/menu/restart-button-disabled` or `/etc/menu/shutdown-button-disabled`,
which `menu.py` checks, so existing installs pick up the keys on update with
nothing to install. Removing a key only takes it off the menu. Companion's
`reboot-pi.sh`/`shutdown-pi.sh` belong to `companion_scripts` and stay put
(the user uses them from Companion). Pressing either key blanks the deck except CONFIRM (21) and
CANCEL (23), and puts the question on the touchscreen (`lcd_message`). Any key
other than CONFIRM cancels. CONFIRM is a separate key, never the one that
asked, so a double press can't confirm. The screen **cancels itself after
`CONFIRM_TIMEOUT_SECS` (10)**, with a countdown after the question on the
touchscreen. The user asked for this (2026-09-27) after it first shipped
untimed. The timeout only ever cancels; it never confirms. The refresh loop
drives it (`tick_power_confirm()`), and `do_power_action()` sets
`confirm_deadline = None` so the countdown can't cancel an action already
underway. While `confirm_action` is set, `redraw()`, `draw_tz_keys()`,
`flash_ok()`, `on_dial()` and the rest of the refresh loop stand down, so
background redraws can't paint over the confirm screen. Shutdown blanks the
keys and drops the brightness to 0 before `systemctl poweroff`, because USB
power usually stays on after the Pi halts. If the command fails, the menu comes
back and flashes FAILED. Runs as root, so no sudoers is needed. This sits
alongside the Companion-triggered `shutdown-pi.sh`/`reboot-pi.sh`; it doesn't
replace them. Not yet verified on hardware.

**Key text is one size everywhere** (user's rule): every key uses `KEY_FONT`,
which `menu.py` sizes at startup to the largest font where every entry in
`KEY_TEXTS` plus the timezone labels fits the key width. With DejaVu Sans that's
16px, set by COMPANION. New key or flash text must be added to `KEY_TEXTS`.
The touchscreen strip uses its own `FONT_LCD` (28px). **Agreed exceptions, on
the timezone keys only:** the clock uses `TZ_CLOCK_FONT` (22px), and the zone
abbreviation uses `TZ_TITLE_FONT`. It's sized at
startup (28px max, same as the touchscreen font) over every abbreviation the zones use in winter and summer
(`tz_year_abbrs()`), so it doesn't jump at a DST switch. A clash label under
the clock stays at `KEY_FONT`.

**Notable paths on the target Pi:** `/opt/sdpi` (the git checkout sdpi runs
from), `/opt/menu` (venv, `menu.py`, `pi_control.py`, `web.py`, `web/`,
icons), `/etc/systemd/system/menu-web.service` (the web control page), `/opt/companion-scripts` (the
three Companion-triggerable scripts — kept separate from `/opt/menu` since
they're a Companion-integration concern), `/etc/menu/menu.json` (runtime IP
config `menu.py` reads/writes itself), `/var/log/menu.log` (shared log for
`menu.py` and `back-to-menu.sh`), `/etc/sudoers.d/091-menu-scripts`
(passwordless sudo for the `companion` user, scoped to exactly
`back-to-menu.sh` — `shutdown-pi.sh`/`reboot-pi.sh` need no extra grant since
Bitfocus's own installer already permits `companion` to run
`/sbin/shutdown`/`/sbin/reboot`/`/sbin/poweroff`),
`/etc/systemd/system/companion.service.d/menu-overrides.conf` (the
shell-command-support override — a drop-in specifically because Companion's
own updater overwrites the base unit file on every update but never touches
`.d/` override directories), `/var/run/reboot-required` (set by modules that
need a reboot). `run()` offers a reboot right after any successful action
that newly set it (Enter = yes). Install → Everything suppresses that per
step (`SDPI_NO_REBOOT_PROMPT`) and offers once at the end. The banner, and the
offer on quit, remain for anyone who said no.
