# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A setup manager (`sdpi`) + app that turns a Raspberry Pi with an Elgato Stream
Deck + XL into a boot-time picker between Bitfocus Companion and Companion
Satellite, with a touchscreen for static IP / DHCP configuration. There is no
build system, package manager, or test suite — this is bash + one Python file,
deployed directly onto a Pi's filesystem as systemd services.

## Commands

There is no build/lint/test tooling in the repo. Verification is:

- Shell scripts: `shellcheck -x -s bash sdpi install.sh lib/common.sh modules/*.sh companion-scripts/*.sh`
  (not installed on this Mac; `pip install shellcheck-py` into a throwaway
  venv works) plus `bash -n <script>`.
- `menu/menu.py`: `python3 -m py_compile menu/menu.py` (then remove the generated `__pycache__`).
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
  Put in-progress work on the `test` branch and merge once it's verified.

## Architecture

**Bootstrap:** `install.sh` (the published one-liner) clones or updates the
repo at `/opt/sdpi` (branch from `$BRANCH`, default `main`), symlinks
`/usr/local/bin/sdpi` to it, and execs `sdpi`. The checkout stays on the Pi:
sdpi updates itself with `git fetch` + `reset --hard origin/<branch>`, then
re-execs with a `--continue-update-*` flag so the rest of an update runs with
the new code. The one-liner uses `bash -c "$(curl ...)"` so stdin stays the
keyboard; `sdpi` also reattaches to `/dev/tty` if launched via `curl | bash`.

**`sdpi`** is a numbered text menu (Install / Update / Remove / Advanced) over
`MODULES=(menu timezone companion satellite companion_scripts rtc)`. Each
`modules/<id>.sh` defines `<id>_label`, `<id>_installed`, `<id>_detail`,
`<id>_install`, `<id>_remove`, and optionally `<id>_update`. Status comes from
the Pi's actual state (unit files, BUILD files, the config.txt overlay line),
never a separate record, so hand-installed or old-installer setups show up
correctly. `lib/common.sh` holds paths and shared helpers (`confirm`,
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
**Gotcha:** companion-pi's `update.sh` deletes `/opt/fnm` ("fnm is no longer
used"), but `satellite.service` runs Node from `/opt/fnm`. So any Companion
install/update re-runs Satellite's installer afterwards if Satellite is
installed (`_companion_restore_satellite_runtime`); `SDPI_SKIP_SATELLITE_FIX=1`
skips that when Satellite is reinstalled right after anyway (Install/Update
Everything). The old single installer only survived this by always installing
Companion before Satellite.

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
it centres up to 9 text keys on the top row, lights the active zone (read from
the `/etc/localtime` symlink, rechecked every refresh tick so SSH/sdpi changes
show up), and on press runs `timedatectl set-timezone` directly (it's root) and
calls `time.tzset()` so its own log timestamps follow. Setting the zone from the
menu *before* handing off means Companion starts fresh in it; that's why this
avoids the Companion restart the README's manual Companion timezone buttons
need. Not yet verified on hardware.

**Key text is one size everywhere** (user's rule): every key uses `KEY_FONT`,
which `menu.py` sizes at startup to the largest font where every entry in
`KEY_TEXTS` plus the timezone labels fits the key width. With DejaVu Sans that's
16px, set by COMPANION. New key or flash text must be added to `KEY_TEXTS`.
The touchscreen strip uses its own `FONT_LCD` (28px).

**Notable paths on the target Pi:** `/opt/sdpi` (the git checkout sdpi runs
from), `/opt/menu` (venv + `menu.py` + icons), `/opt/companion-scripts` (the
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
need a reboot; sdpi shows a banner and offers to reboot on quit).

## Planned work

**Shutdown/reboot keys on the physical menu itself** (in addition to, not
instead of, the existing Companion-triggered `companion-scripts/shutdown-pi.sh`/
`reboot-pi.sh`) — agreed worth doing: there's currently no way to safely
power down/restart while sitting at the menu screen (fresh boot, or just
back from `back-to-menu.sh`) without SSH access. Easier than the Companion
versions too, since `menu.py` already runs as root — no sudoers/shell-command
dance needed, just `subprocess.run(["shutdown", ...])` directly.

Blocked on the user designing icon assets and testing how they read on the
physical 36-key grid before wiring up behavior. The top row is now taken by
the timezone keys (when that feature is installed).

Confirmation design, when it happens: don't build a timeout-based "press
once to arm, confirm within N seconds" flow — that pattern doesn't actually
exist anywhere in this codebase (a prior session incorrectly assumed the
IP-edit dial flow worked that way; it doesn't, see `on_dial()`/`_do_apply()`/
`_do_cancel()`). The real IP-edit safety net is untimed: `editing` mode has
no expiry at all, and the destructive action (`_do_apply()`, dial 5) only
fires when a *separate* key (dial 4) has first been pressed to navigate to a
different page — not a second press of the same control, and no clock
running either way. Mirror that shape here: a dedicated confirm screen with
its own CONFIRM/CANCEL keys, sitting untimed, not a countdown.
