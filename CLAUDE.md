# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An installer + app that turns a Raspberry Pi with an Elgato Stream Deck + XL
into a boot-time picker between Bitfocus Companion and Companion Satellite,
with a touchscreen for static IP / DHCP configuration. There is no build
system, package manager, or test suite — this is bash + one Python file,
deployed directly onto a Pi's filesystem as systemd services.

## Commands

There is no build/lint/test tooling. Verification is:

- Shell scripts: `bash -n <script>` before committing (syntax only — no linter is configured).
- `menu/menu.py`: `python3 -m py_compile menu/menu.py` (then remove the generated `__pycache__`).
- Real behavior can only be verified on **actual hardware** — a Raspberry Pi
  (64-bit OS) with a physical Stream Deck + XL attached. Nothing here runs in
  CI or a sandbox; when reasoning about a change, say explicitly if it hasn't
  been confirmed on hardware.
- To exercise the installer: `sudo installer/install.sh` from a clone, or the
  published one-liner (`curl -fsSL https://raw.githubusercontent.com/shaelr/StreamdeckXL-Pi-Boot-Menu/main/install.sh | sudo bash`).
  It's idempotent — safe to re-run on an already-installed Pi to pick up changes.
- No release/tag workflow: the one-liner clones `main` directly (see
  `install.sh`). Every push to `main` is immediately what gets installed —
  don't reintroduce GitHub Releases pinning without being asked; that was
  deliberately removed.

## Architecture

**Three-stage bootstrap:** `install.sh` (root, the published one-liner) clones
the repo to a temp dir and hands off to `installer/install.sh`, which is the
actual installer. It resolves `menu/` and `companion-scripts/` relative to its
own location (not the caller's cwd), so it works whether run via the one-liner
or a manual clone.

`installer/install.sh` does, roughly in order: apt packages, installs
Companion + Satellite via their **own upstream installers** (piped from
GitHub, pinned to the `stable` channel via the `COMPANION_BUILD`/
`SATELLITE_BUILD` env vars they read — not CLI flags, they don't take any),
leaves both services disabled, sets up a Python venv, deploys `menu/menu.py`
as the `menu` systemd service (runs as root), deploys `companion-scripts/*.sh`
to `/opt/companion-scripts/`, and grants Companion shell-command support plus
a scoped sudoers entry (see below). It always re-runs Companion/Satellite's
installers, which re-download and reinstall "latest stable" even if already
current — that's an inefficiency in their own updater's version-comparison
logic, not a bug here.

**The core mechanic — one USB device, two mutually-exclusive owners:** the
Stream Deck + XL can only be claimed by one process at a time (`menu.py`'s
`python-elgato-streamdeck` transport claims it via **libusb directly**, not
`/dev/hidraw` — confirmed by reading the library's actual transport source,
not assumed). `menu.py`'s `handoff_to()` releases its own claim, deauthorizes/
reauthorizes the device's USB port to force a clean kernel-level
re-enumeration, and starts Companion or Satellite. `companion-scripts/back-to-menu.sh`
does the reverse, triggered *from inside Companion* via its "Run shell path"
button action (shell commands are enabled via a systemd drop-in on
`companion.service`, since it's off by default upstream).

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

**`streamdeck` (PyPI) is deliberately unpinned**, not pinned to a tested
version — installed as plain `pip install streamdeck`, always latest. This
was an explicit choice (see git history) despite `menu.py`'s touchscreen
drawing (`update_lcd()`) being written and verified against `0.10.0`'s
specific `PILHelper`/rotation behavior. If the touchscreen ever renders
wrong/rotated after a fresh install, that version-behavior coupling is the
first thing to check, not a StreamDeck+XL hardware issue.

**Notable paths on the target Pi:** `/opt/menu` (venv + `menu.py` + icons),
`/opt/companion-scripts` (the three Companion-triggerable scripts — kept
separate from `/opt/menu` since they're a Companion-integration concern, not
part of the menu app), `/etc/menu/menu.json` (runtime IP config `menu.py`
reads/writes itself, not deployed by the installer), `/var/log/menu.log`
(shared log for both `menu.py` and `back-to-menu.sh`), `/etc/sudoers.d/091-menu-scripts`
(passwordless sudo for the `companion` user, scoped to exactly
`back-to-menu.sh` — `shutdown-pi.sh`/`reboot-pi.sh` need no extra grant since
Bitfocus's own installer already permits `companion` to run
`/sbin/shutdown`/`/sbin/reboot`/`/sbin/poweroff`), `/etc/systemd/system/companion.service.d/menu-overrides.conf`
(the shell-command-support override — a drop-in specifically because
Companion's own updater overwrites the base unit file on every update but
never touches `.d/` override directories).

## Planned work

**Shutdown/reboot keys on the physical menu itself** (in addition to, not
instead of, the existing Companion-triggered `companion-scripts/shutdown-pi.sh`/
`reboot-pi.sh`) — agreed worth doing: there's currently no way to safely
power down/restart while sitting at the menu screen (fresh boot, or just
back from `back-to-menu.sh`) without SSH access. Easier than the Companion
versions too, since `menu.py` already runs as root — no sudoers/shell-command
dance needed, just `subprocess.run(["shutdown", ...])` directly.

Not yet implemented — blocked on the user designing icon assets and testing
how they read on the physical 36-key grid before wiring up behavior.

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
