#!/usr/bin/env python3
import sys, time, json, subprocess, re, threading, math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.ImageHelpers import PILHelper
from StreamDeck.Devices.StreamDeck import DialEventType

from pi_control import (
    HID_VENDOR, log, run, get_ip_mask, write_json, mask_to_prefix_if_valid,
    read_static_from_json, nm_ipv4_method, nm_set_dhcp, apply_nm, ip_ok, wait_ip_change, current_zone, set_timezone,
    reset_usb_device, service_installed)

# ================== CONFIG ==================
BRIGHTNESS = 80

LEFT_START_SERVICE  = "companion"
RIGHT_START_SERVICE = "satellite"

LEFT_START_LABEL  = "COMPANION"
RIGHT_START_LABEL = "SATELLITE"

LEFT_START_ICON  = "/opt/menu/icons/comp256x256.png"
RIGHT_START_ICON = "/opt/menu/icons/sat256x256.png"

# sdpi writes these when the user removes a key from the menu; no file = shown.
RESTART_DISABLED  = "/etc/menu/restart-button-disabled"
SHUTDOWN_DISABLED = "/etc/menu/shutdown-button-disabled"
RESTART_ICON   = "/opt/menu/icons/res256x256.png"
SHUTDOWN_ICON  = "/opt/menu/icons/pwr256x256.png"

SELF_SERVICE_NAME = "menu"

# Written by sdpi's "Timezone buttons" feature; no file = no timezone keys.
TZ_CONFIG = "/etc/menu/timezones.json"

AUTO_REFRESH_SECS = 1.0
CONFIRM_TIMEOUT_SECS = 10   # restart/shutdown confirm screen cancels itself after this
# ============================================

# StreamDeck Plus XL (9x4) — keys 0-35
KEY_COLS  = 9
KEY_LEFT  = 27
KEY_DHCP  = 31
KEY_RIGHT = 35
# Third row, far ends, so one can't be hit while reaching for the other.
KEY_RESTART  = 18
KEY_SHUTDOWN = 26
# Never the key that asked, so a double press can't confirm by accident.
KEY_CONFIRM  = 21
KEY_CANCEL   = 23

GREEN = (0, 120, 0)   # DHCP key; restart matches it
RED   = (150, 0, 0)

# Logging, network, timezone and USB-reset helpers live in pi_control.py,
# shared with the web control page (web.py).

# ---------- timezone buttons ----------
def load_timezone_buttons():
    """[(key, label, zone), ...] from TZ_CONFIG, centred on the top row."""
    try:
        entries = json.loads(Path(TZ_CONFIG).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception as e:
        log(f"timezone config error: {e}")
        return []
    if not isinstance(entries, list):
        log("timezone config error: expected a list")
        return []
    valid = []
    for e in entries:
        label = str(e.get("label", "")).strip() if isinstance(e, dict) else ""
        zone  = str(e.get("zone", "")).strip() if isinstance(e, dict) else ""
        if label and zone and (Path("/usr/share/zoneinfo") / zone).is_file():
            valid.append((label, zone))
        else:
            log(f"skipping timezone entry {e!r}")
    valid = valid[:KEY_COLS]
    start = (KEY_COLS - len(valid)) // 2
    return [(start + i, label, zone) for i, (label, zone) in enumerate(valid)]

def tz_key_faces():
    """[(key, abbr, clock, label, zone), ...] for the timezone keys. abbr is the
    zone's official abbreviation right now (EDT/EST follow DST); clock is 24h
    to keep it narrow. label is None unless the abbreviation is only numeric
    (e.g. "+04") or another key currently shares it (Mountain and Arizona are
    both MST in winter)."""
    faces = []
    for key, label, zone in tz_buttons:
        try:
            now = datetime.now(ZoneInfo(zone))
            abbr, clock = now.strftime("%Z"), now.strftime("%H:%M")
        except Exception as e:
            log(f"clock for {zone} failed: {e}")
            abbr, clock = "", None
        faces.append((key, label, abbr, clock, zone))
    abbrs = [f[2] for f in faces]
    return [(key, abbr, clock,
             label if not abbr[:1].isalpha() or abbrs.count(abbr) > 1 else None, zone)
            for key, label, abbr, clock, zone in faces]

def tz_year_abbrs():
    """Every abbreviation the timezone keys can show (winter and summer), so the
    title font is sized once and doesn't change size at a DST switch."""
    year = datetime.now().year
    abbrs = set()
    for _key, _label, zone in tz_buttons:
        try:
            for month in (1, 7):
                abbrs.add(datetime(year, month, 1, tzinfo=ZoneInfo(zone)).strftime("%Z"))
        except Exception:
            pass
    return sorted(abbrs)


# ---------- UI helpers ----------
def load_font(sz):
    p = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    return ImageFont.truetype(p, sz) if Path(p).exists() else ImageFont.load_default()

FONT_LCD = load_font(28)

# Every text that can appear on a key. All keys share KEY_FONT, sized at startup
# so the longest of these (plus the timezone labels) fits; add new key/flash
# text here or it may not fit.
KEY_TEXTS = [LEFT_START_LABEL, RIGHT_START_LABEL, "DHCP", "Manual",
             "APPLIED", "TIMEOUT", "BAD IP", "BAD JSON", "NO NM",
             "RESTART", "SHUTDOWN", "CONFIRM", "CANCEL", "FAILED"]
KEY_FONT = load_font(16)
# The exceptions to the shared size (user's choice): the timezone keys' zone
# abbreviation (sized at startup like KEY_FONT) and 24h clock are drawn larger.
TZ_TITLE_FONT = load_font(28)
TZ_CLOCK_FONT = load_font(22)   # "23:59" fits with room to spare

def pick_key_font(labels, max_w, largest=24):
    """Largest font (largest px down) at which every label fits in max_w pixels."""
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for size in range(largest, 9, -1):
        font = load_font(size)
        if all(probe.textbbox((0, 0), t, font=font)[2] - probe.textbbox((0, 0), t, font=font)[0] <= max_w
               for t in labels):
            return font
    return load_font(10)

def img_text(deck, text, bg, sub=None):
    w, h = deck.key_image_format()["size"]
    im = Image.new("RGB", (w, h), bg)
    d  = ImageDraw.Draw(im)
    if text:
        bb = d.textbbox((0, 0), text, font=KEY_FONT)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        y = (h - th) // 2 - (6 if sub else 0)
        d.text(((w - tw) // 2, y), text, font=KEY_FONT, fill=(255, 255, 255))
    if sub:
        bb2 = d.textbbox((0, 0), sub, font=KEY_FONT)
        sw, sh = bb2[2] - bb2[0], bb2[3] - bb2[1]
        d.text(((w - sw) // 2, h - sh - 6), sub, font=KEY_FONT, fill=(255, 255, 255))
    return PILHelper.to_native_key_format(deck, im)

def img_icon(deck, label, bg, path):
    w, h = deck.key_image_format()["size"]
    im = Image.new("RGB", (w, h), bg)
    d  = ImageDraw.Draw(im)
    bb = d.textbbox((0, 0), label, font=KEY_FONT)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    try:
        ic = Image.open(path).convert("RGBA")
        ic.thumbnail((int(w * 0.90), int(h * 0.55)))
    except Exception as e:
        log(f"icon load failed '{path}': {e}")
        ic = None
    # Icon and label centred together, so the label isn't on the bottom edge.
    gap = 8
    y = (h - ((ic.height + gap) if ic else 0) - th) // 2
    if ic:
        im.paste(ic, ((w - ic.width) // 2, y), ic)
        y += ic.height + gap
    d.text(((w - tw) // 2 - bb[0], y - bb[1]), label, font=KEY_FONT, fill=(255, 255, 255))
    return PILHelper.to_native_key_format(deck, im)

def img_tz(deck, title, clock, label, bg):
    """Timezone key: title, clock and optional label, centred as a block."""
    w, h = deck.key_image_format()["size"]
    im = Image.new("RGB", (w, h), bg)
    d  = ImageDraw.Draw(im)
    rows = [(t, f) for t, f in ((title, TZ_TITLE_FONT), (clock, TZ_CLOCK_FONT), (label, KEY_FONT)) if t]
    # More space under the title than between the lines below it.
    gaps = [20 if f is TZ_TITLE_FONT else 4 for _t, f in rows[:-1]] + [0]
    boxes = [d.textbbox((0, 0), t, font=f) for t, f in rows]
    y = (h - sum(b[3] - b[1] + g for b, g in zip(boxes, gaps))) // 2
    for (t, f), b, g in zip(rows, boxes, gaps):
        d.text(((w - (b[2] - b[0])) // 2 - b[0], y - b[1]), t, font=f, fill=(255, 255, 255))
        y += b[3] - b[1] + g
    return PILHelper.to_native_key_format(deck, im)

def blank_key(deck):
    return img_text(deck, "", (0, 0, 0))

def flash_ok(color_rgb, text):
    def _flash():
        if confirm_action:   # e.g. an IP apply finishing behind the confirm screen
            return
        with deck_lock:
            deck.set_key_image(KEY_DHCP, img_text(deck, text, color_rgb))
        time.sleep(0.6)
        redraw()
    threading.Thread(target=_flash, daemon=True).start()

def update_lcd():
    try:
        w, h   = deck.touchscreen_image_format()["size"]
        ZONE_W = 200
        im = PILHelper.create_touchscreen_image(deck)
        d  = ImageDraw.Draw(im)

        def draw_zone(zone_idx, bot_label, bot_color):
            x = zone_idx * ZONE_W
            d.rectangle([x, 0, x + ZONE_W - 2, h - 1], outline=(40, 40, 40))
            if bot_label:
                bb = d.textbbox((0, 0), bot_label, font=FONT_LCD)
                tw, th = bb[2] - bb[0], bb[3] - bb[1]
                d.text((x + (ZONE_W - tw) // 2, (h - th) // 2),
                       bot_label, font=FONT_LCD, fill=bot_color)

        if lcd_message:
            bb = d.textbbox((0, 0), lcd_message, font=FONT_LCD)
            d.text(((w - (bb[2] - bb[0])) // 2, (h - (bb[3] - bb[1])) // 2),
                   lcd_message, font=FONT_LCD, fill=(255, 255, 255))
        elif editing:
            octets = edit_ip if dial_page == "ip" else edit_mask
            for i in range(4):
                draw_zone(i, bot_label=str(octets[i]), bot_color=(255, 255, 0))
            if dial_page == "ip":
                draw_zone(4, bot_label="->MASK", bot_color=(220, 150, 0))
                draw_zone(5, bot_label="CANCEL", bot_color=(220, 80, 80))
            else:
                draw_zone(4, bot_label="<-IP",   bot_color=(220, 150, 0))
                draw_zone(5, bot_label="APPLY",  bot_color=(0, 220, 0))
        else:
            for i in range(4):
                draw_zone(i, bot_label=str(cur_ip[i]), bot_color=(200, 200, 255))
            draw_zone(4, bot_label=f"{cur_mask[0]}.{cur_mask[1]}", bot_color=(150, 255, 150))
            draw_zone(5, bot_label=f"{cur_mask[2]}.{cur_mask[3]}", bot_color=(150, 255, 150))

        native = PILHelper.to_native_touchscreen_format(deck, im)
        with deck_lock:
            deck.set_touchscreen_image(native, 0, 0, w, h)

    except Exception as e:
        log(f"update_lcd error: {e}")

# ---------- State ----------
deck      = None
deck_lock = threading.Lock()

editing   = False
dial_page = "ip"
cur_ip,  cur_mask  = get_ip_mask()
edit_ip, edit_mask = cur_ip[:], cur_mask[:]
dhcp_on = False
tz_buttons  = []
active_zone = ""
confirm_action = None   # "restart"/"shutdown" while the confirm screen is up
confirm_deadline = None # when the confirm screen cancels itself; None once confirmed
lcd_message    = None   # full-width touchscreen text, replacing the IP zones

def refresh_current():
    global cur_ip, cur_mask
    cur_ip, cur_mask = get_ip_mask()

def refresh_dhcp_state():
    global dhcp_on
    dhcp_on = (nm_ipv4_method() == "auto")

def enter_edit():
    global editing, edit_ip, edit_mask
    if not editing:
        editing   = True
        edit_ip   = cur_ip[:]
        edit_mask = cur_mask[:]
    return True

def _reset_edit_state():
    global editing, dial_page
    editing   = False
    dial_page = "ip"
    refresh_current()
    edit_ip[:]   = cur_ip[:]
    edit_mask[:] = cur_mask[:]
    redraw()

# Serializes the blocking nmcli work (apply static IP / toggle DHCP) onto a
# background thread so it doesn't stall the StreamDeck's USB read thread —
# dial/key callbacks run synchronously on that thread, so without this a
# single IP change could freeze all input for up to ~30s. Non-blocking
# acquire: a press that arrives while one is already running is dropped
# rather than queued, since starting overlapping nmcli calls isn't safe.
_network_op_lock = threading.Lock()

# ---------- static keys (draw once at startup) ----------
def draw_static_keys():
    with deck_lock:
        blank = blank_key(deck)
        for k in range(36):
            deck.set_key_image(k, blank)
        # A key only appears if its service is installed (sdpi installs/removes each separately).
        for key, label, icon, svc in ((KEY_LEFT, LEFT_START_LABEL, LEFT_START_ICON, LEFT_START_SERVICE),
                                      (KEY_RIGHT, RIGHT_START_LABEL, RIGHT_START_ICON, RIGHT_START_SERVICE)):
            if service_installed(svc):
                deck.set_key_image(key, img_icon(deck, label, (0, 60, 140), icon))
        if power_key_enabled("restart"):
            deck.set_key_image(KEY_RESTART,  img_icon(deck, "RESTART",  GREEN, RESTART_ICON))
        if power_key_enabled("shutdown"):
            deck.set_key_image(KEY_SHUTDOWN, img_icon(deck, "SHUTDOWN", RED,   SHUTDOWN_ICON))
    draw_tz_keys()

def draw_tz_keys():
    if confirm_action:
        return
    faces = tz_key_faces()
    with deck_lock:
        for key, abbr, clock, label, zone in faces:
            bg = GREEN if zone == active_zone else (0, 0, 0)
            deck.set_key_image(key, img_tz(deck, abbr, clock, label, bg))

# ---------- redraw (DHCP button + LCD) ----------
def redraw():
    if confirm_action:
        return
    with deck_lock:
        if dhcp_on:
            deck.set_key_image(KEY_DHCP, img_text(deck, "DHCP",   GREEN))
        else:
            deck.set_key_image(KEY_DHCP, img_text(deck, "Manual", (0, 80, 150)))
    update_lcd()

# ---------- restart / shutdown ----------
POWER_ACTIONS = {
    "restart":  ("RESTART THE PI?",   "RESTARTING...",    ["systemctl", "reboot"],   GREEN),
    "shutdown": ("SHUT DOWN THE PI?", "SHUTTING DOWN...", ["systemctl", "poweroff"], RED),
}

def power_key_enabled(action):
    return not Path(RESTART_DISABLED if action == "restart" else SHUTDOWN_DISABLED).exists()

def show_only(images):
    """Blank every key except the given {key: image}."""
    blank = blank_key(deck)
    with deck_lock:
        for k in range(36):
            deck.set_key_image(k, images.get(k, blank))

def start_power_confirm(action):
    global confirm_action, confirm_deadline, lcd_message
    prompt, _busy, _cmd, color = POWER_ACTIONS[action]
    confirm_action = action
    confirm_deadline = time.time() + CONFIRM_TIMEOUT_SECS
    lcd_message = f"{prompt} {CONFIRM_TIMEOUT_SECS}"
    show_only({KEY_CONFIRM: img_text(deck, "CONFIRM", color),
               KEY_CANCEL:  img_text(deck, "CANCEL", (0, 60, 140))})
    update_lcd()

def cancel_power_confirm():
    global confirm_action, lcd_message
    confirm_action = None
    lcd_message = None
    draw_static_keys()
    redraw()

def tick_power_confirm():
    """Once a second while the confirm screen is up: count down on the
    touchscreen, and cancel (never confirm) when time runs out."""
    global lcd_message
    action, deadline = confirm_action, confirm_deadline
    if not action or deadline is None:
        return
    left = math.ceil(deadline - time.time())
    if left <= 0:
        log(f"{action} confirm timed out")
        cancel_power_confirm()
        return
    lcd_message = f"{POWER_ACTIONS[action][0]} {left}"
    update_lcd()

def do_power_action(action):
    global lcd_message, confirm_deadline
    _prompt, busy, cmd, _color = POWER_ACTIONS[action]
    confirm_deadline = None   # stop the countdown; it's happening now
    log(f"{action} confirmed from the menu")
    lcd_message = busy
    show_only({})
    update_lcd()
    if action == "shutdown":
        # USB power usually stays on after the Pi halts; going dark shows it's off.
        time.sleep(1.5)
        with deck_lock:
            deck.set_brightness(0)
    try:
        r = run(cmd, timeout=10)
        err = None if r.returncode == 0 else r.stderr.strip()
    except Exception as e:
        err = str(e)
    if err is not None:
        log(f"{action} failed: {err}")
        with deck_lock:
            deck.set_brightness(BRIGHTNESS)
        cancel_power_confirm()
        flash_ok(RED, "FAILED")

active_key      = None
active_key_lock = threading.Lock()

# ---------- Dial helpers ----------
def _apply_worker(ip, mask, pref):
    global dhcp_on
    try:
        write_json(ip, mask)
        apply_nm(ip, pref)
        if wait_ip_change(ip):
            flash_ok((0, 120, 0), "APPLIED")
        else:
            flash_ok((150, 0, 0), "TIMEOUT")
        dhcp_on = False
        _reset_edit_state()
    except Exception as e:
        log(f"apply worker error: {e}")
    finally:
        _network_op_lock.release()

def _do_apply():
    if not editing:
        return
    pref = mask_to_prefix_if_valid(edit_mask)
    if (not ip_ok(edit_ip)) or (pref is None):
        flash_ok((150, 0, 0), "BAD IP")
        return
    if not _network_op_lock.acquire(blocking=False):
        return
    threading.Thread(target=_apply_worker, args=(edit_ip[:], edit_mask[:], pref), daemon=True).start()

def _do_cancel():
    if not editing:
        return
    _reset_edit_state()

# ---------- Dial callback ----------
def on_dial(_, dial, event, value):
    global editing, dial_page

    if confirm_action:
        return

    if event == DialEventType.TURN:
        if dhcp_on or dial > 3 or _network_op_lock.locked():
            return
        enter_edit()
        if dial_page == "ip":
            edit_ip[dial]   = (edit_ip[dial]   + value) % 256
        else:
            edit_mask[dial] = (edit_mask[dial] + value) % 256
        update_lcd()

    elif event == DialEventType.PUSH:
        if not value or dhcp_on:
            return

        if dial == 4:
            if dial_page == "ip":
                enter_edit()
                dial_page = "mask"
            else:
                dial_page = "ip"
            update_lcd()

        elif dial == 5:
            if dial_page == "ip":
                _do_cancel()
            else:
                _do_apply()


# ---------- Handoff ----------


def _wait_for_hidraw(timeout=8):
    """Wait until at least one /dev/hidraw* node owned by Elgato appears."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        hidraw_dir = Path("/sys/class/hidraw")
        if hidraw_dir.exists():
            for hr in hidraw_dir.iterdir():
                try:
                    device_link = hr / "device"
                    if not device_link.exists():
                        continue
                    # Walk up to find the USB vendor
                    real = device_link.resolve()
                    for parent in [real] + list(real.parents):
                        vf = parent / "idVendor"
                        if vf.exists() and vf.read_text().strip().lower() == HID_VENDOR.lower():
                            log(f"hidraw node ready: {hr.name}")
                            return True
                except OSError:
                    pass
        time.sleep(0.3)
    return False


def handoff_to(service_name: str):
    global active_key
    with active_key_lock:
        active_key = None

    # ---- Step 1: Fully release the Python HID handle ----
    # Stop the reader thread FIRST so it isn't holding the fd open,
    # then close the transport.  The library's close() alone does NOT
    # stop the reader thread — only _setup_reader(None) does.
    try:
        deck.reset()
    except Exception as e:
        log(f"deck reset error: {e}")
    try:
        deck._setup_reader(None)          # kill reader thread
    except Exception as e:
        log(f"reader stop error: {e}")
    try:
        deck.close()
    except Exception as e:
        log(f"deck close error: {e}")

    # Small settle time for libhidapi / kernel to fully release
    time.sleep(0.3)

    # ---- Step 2: USB port-level deauthorize/reauthorize ----
    # This is the most reliable way to make the kernel fully tear down
    # and re-enumerate the device, creating fresh /dev/hidraw* nodes
    # that are not stale from our previous session.
    reset_usb_device()

    # ---- Step 3: Wait for the fresh hidraw node to appear ----
    if not _wait_for_hidraw(timeout=8):
        log("WARNING: hidraw node did not reappear within timeout")

    # Give udev a moment to apply permission rules
    time.sleep(0.5)

    # ---- Step 4: Start the target service, THEN stop ourselves ----
    # Use --no-block so systemctl returns immediately, then stop
    # our own unit.  Ordering: start first, stop second, so the
    # target service is already activating before we disappear.
    log(f"handoff to: {service_name}")
    try:
        subprocess.run(
            ["systemctl", "start", "--no-block", service_name],
            timeout=5, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log(f"systemctl start error: {e}")

    # Stop ourselves via a detached process so we don't kill this
    # code path mid-execution.
    subprocess.Popen(
        ["systemctl", "stop", "--no-block", SELF_SERVICE_NAME],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    time.sleep(1)
    sys.exit(0)
# ---------- Keys ----------
def _dhcp_to_manual_worker(ip, mask, pref):
    global dhcp_on
    try:
        write_json(ip, mask)
        apply_nm(ip, pref)
        wait_ip_change(ip)
        dhcp_on = False
        _reset_edit_state()
        enter_edit()
        update_lcd()
    except Exception as e:
        log(f"dhcp-to-manual worker error: {e}")
    finally:
        _network_op_lock.release()

def _manual_to_dhcp_worker():
    global dhcp_on
    try:
        ok = nm_set_dhcp(True)
        dhcp_on = True
        _reset_edit_state()
        if not ok:
            flash_ok((150, 0, 0), "NO NM")
    except Exception as e:
        log(f"manual-to-dhcp worker error: {e}")
    finally:
        _network_op_lock.release()

def on_key(_, key, pressed):
    global active_key, active_zone

    if not pressed:
        with active_key_lock:
            if active_key == key:
                active_key = None
        return

    with active_key_lock:
        if active_key is not None and active_key != key:
            return
        active_key = key

    if confirm_action:
        if key == KEY_CONFIRM:
            do_power_action(confirm_action)
        else:
            cancel_power_confirm()
        return

    if key in (KEY_RESTART, KEY_SHUTDOWN):
        action = "restart" if key == KEY_RESTART else "shutdown"
        if power_key_enabled(action):
            start_power_confirm(action)
        return

    if key == KEY_DHCP:
        refresh_dhcp_state()
        if dhcp_on:
            # Switch DHCP -> Manual
            ip, mask = read_static_from_json()
            pref = mask_to_prefix_if_valid(mask)
            if (not ip_ok(ip)) or (pref is None):
                flash_ok((150, 0, 0), "BAD JSON")
                refresh_current()
                redraw()
                return
            if not _network_op_lock.acquire(blocking=False):
                return
            threading.Thread(target=_dhcp_to_manual_worker, args=(ip, mask, pref), daemon=True).start()
        else:
            # Switch Manual -> DHCP
            if not _network_op_lock.acquire(blocking=False):
                return
            threading.Thread(target=_manual_to_dhcp_worker, daemon=True).start()
        return

    for tz_key, _label, zone in tz_buttons:
        if key == tz_key:
            if set_timezone(zone):
                active_zone = zone
                draw_tz_keys()
            return

    if key in (KEY_LEFT, KEY_RIGHT):
        svc = LEFT_START_SERVICE if key == KEY_LEFT else RIGHT_START_SERVICE
        # Handing off stops the menu; with nothing to start, the deck would go dead.
        if not service_installed(svc):
            log(f"{svc} is not installed; ignoring key")
            return
        handoff_to(svc)

# ---------- main ----------
while deck is None:
    try:
        ds = DeviceManager().enumerate()
        if ds:
            deck = ds[0]
    except Exception as e:
        log(f"enumerate error: {e}")
    time.sleep(1)

deck.open()
deck.reset()
deck.set_brightness(BRIGHTNESS)

refresh_current()
refresh_dhcp_state()
edit_ip, edit_mask = cur_ip[:], cur_mask[:]

deck.set_key_callback(on_key)
deck.set_dial_callback(on_dial)

def on_touch(*args):
    pass

deck.set_touchscreen_callback(on_touch)

tz_buttons  = load_timezone_buttons()
active_zone = current_zone()
KEY_FONT    = pick_key_font(KEY_TEXTS + [label for _, label, _ in tz_buttons],
                            deck.key_image_format()["size"][0] - 8)
TZ_TITLE_FONT = pick_key_font(tz_year_abbrs() or ["EST"],
                              deck.key_image_format()["size"][0] - 8, largest=28)
draw_static_keys()
redraw()
drawn_zone, drawn_minute = active_zone, int(time.time() // 60)

# ---------- auto-refresh loop ----------
t_next = time.time() + AUTO_REFRESH_SECS
while True:
    time.sleep(0.05)
    if time.time() < t_next:
        continue
    t_next = time.time() + AUTO_REFRESH_SECS

    if confirm_action:
        tick_power_confirm()
        continue

    # Catches a zone changed some other way (SSH, sdpi) so the lit key stays
    # right, and ticks the clocks over each minute.
    if tz_buttons:
        zone = current_zone()
        minute = int(time.time() // 60)
        if zone != active_zone:
            active_zone = zone
            time.tzset()
        if zone != drawn_zone or minute != drawn_minute:
            drawn_zone, drawn_minute = zone, minute
            draw_tz_keys()

    if editing:
        continue

    new_ip, new_mask = get_ip_mask()
    if new_ip != cur_ip or new_mask != cur_mask:
        cur_ip[:]    = new_ip[:]
        cur_mask[:]  = new_mask[:]
        edit_ip[:]   = cur_ip[:]
        edit_mask[:] = cur_mask[:]
        update_lcd()

    prev_dhcp = dhcp_on
    refresh_dhcp_state()
    if dhcp_on != prev_dhcp:
        redraw()
