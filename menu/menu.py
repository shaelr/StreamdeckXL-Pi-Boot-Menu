#!/usr/bin/env python3
import sys, time, json, subprocess, re, threading
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.ImageHelpers import PILHelper
from StreamDeck.Devices.StreamDeck import DialEventType

# ================== CONFIG ==================
ETH_DEV = "eth0"
JSON_PATH = "/etc/menu/menu.json"
LOG_FILE = "/var/log/menu.log"
BRIGHTNESS = 80

LEFT_START_SERVICE  = "companion"
RIGHT_START_SERVICE = "satellite"

LEFT_START_LABEL  = "COMPANION"
RIGHT_START_LABEL = "SATELLITE"

LEFT_START_ICON  = "/opt/menu/icons/comp256x256.png"
RIGHT_START_ICON = "/opt/menu/icons/sat256x256.png"

SELF_SERVICE_NAME = "menu"

AUTO_REFRESH_SECS = 1.0
HID_VENDOR        = "0FD9"
NM_CONN_TTL       = 5.0
# ============================================

# StreamDeck Plus XL (9x4) — keys 0-35
KEY_LEFT  = 27
KEY_DHCP  = 31
KEY_RIGHT = 35

# ---------- logging ----------
def log(s: str):
    try:
        Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%F %T ") + s + "\n")
    except:
        pass

# ---------- shell helpers ----------
def run(cmd, timeout=10):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=timeout)

def run_logged(cmd, timeout=10):
    """Like run(), but logs a warning with stderr on a non-zero exit.
    Only worth the log noise for mutating nmcli calls, not the frequent
    read-only polling ones (nm_conn/get_ip_prefix), where a transient
    non-zero exit is normal."""
    r = run(cmd, timeout=timeout)
    if r.returncode != 0:
        log(f"command failed (exit {r.returncode}): {' '.join(cmd)} -- {r.stderr.strip()}")
    return r

# ---------- nmcli connection cache ----------
_nm_conn_cache = None
_nm_conn_time  = 0.0

def nm_conn():
    global _nm_conn_cache, _nm_conn_time
    if time.time() - _nm_conn_time < NM_CONN_TTL:
        return _nm_conn_cache
    try:
        out = run(["nmcli", "-t", "-f", "GENERAL.CONNECTION", "dev", "show", ETH_DEV],
                  timeout=4).stdout.strip()
        if ":" in out:
            out = out.split(":", 1)[1].strip()
        _nm_conn_cache = out if out and out != "--" else None
        _nm_conn_time  = time.time()
        return _nm_conn_cache
    except:
        _nm_conn_cache = None
        return None

def _invalidate_nm_cache():
    global _nm_conn_time
    _nm_conn_time = 0.0

# ---------- network helpers ----------
def get_ip_prefix():
    try:
        out = run(["ip", "-4", "addr", "show", "dev", ETH_DEV], timeout=4).stdout
        m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", out)
        if not m:
            return None, None
        return m.group(1), int(m.group(2))
    except Exception as e:
        log(f"get_ip_prefix error: {e}")
        return None, None

def prefix_to_mask(p: int):
    p = max(0, min(32, int(p)))
    bits = (1 << 32) - (1 << (32 - p)) if p else 0
    return [(bits >> 24) & 255, (bits >> 16) & 255, (bits >> 8) & 255, bits & 255]

def get_ip_mask():
    ip_s, p = get_ip_prefix()
    if not ip_s:
        return [0, 0, 0, 0], [255, 255, 255, 0]
    return list(map(int, ip_s.split("."))), prefix_to_mask(p)

def gw(ip):
    return f"{ip[0]}.{ip[1]}.{ip[2]}.1"

def write_json(ip, mask):
    data = {"ip": ".".join(map(str, ip)), "mask": ".".join(map(str, mask)),
            "gw": gw(ip), "dns": gw(ip)}
    Path(JSON_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(JSON_PATH).write_text(json.dumps(data, indent=2), encoding="utf-8")
    log("WRITE_JSON called")

def mask_to_prefix_if_valid(mask):
    if len(mask) != 4:
        return None
    for o in mask:
        if o < 0 or o > 255:
            return None
    bits = (mask[0] << 24) | (mask[1] << 16) | (mask[2] << 8) | mask[3]
    inv  = (~bits) & 0xFFFFFFFF
    if inv & (inv + 1) != 0:
        return None
    return bits.bit_count()

def read_static_from_json():
    default_ip, default_mask = [192, 168, 0, 10], [255, 255, 255, 0]
    try:
        p = Path(JSON_PATH)
        if not p.exists():
            return default_ip, default_mask
        txt = p.read_text(encoding="utf-8").strip()
        if not txt:
            return default_ip, default_mask
        data   = json.loads(txt)
        ip_s   = (data.get("ip")   or "").strip()
        mask_s = (data.get("mask") or "").strip()
        ip   = list(map(int, ip_s.split(".")))   if ip_s   else None
        mask = list(map(int, mask_s.split("."))) if mask_s else None
        if not ip   or len(ip)   != 4 or any(o < 0 or o > 255 for o in ip):
            return default_ip, default_mask
        if not mask or len(mask) != 4 or any(o < 0 or o > 255 for o in mask):
            return default_ip, default_mask
        if mask_to_prefix_if_valid(mask) is None or ip[0] == 0:
            return default_ip, default_mask
        return ip, mask
    except Exception as e:
        log(f"read_static_from_json error: {e}")
        return default_ip, default_mask

def nm_ipv4_method():
    c = nm_conn()
    if not c:
        return None
    out = run(["nmcli", "-t", "-f", "ipv4.method", "con", "show", c],
              timeout=4).stdout.strip()
    if ":" in out:
        out = out.split(":", 1)[1].strip()
    return out or None

def nm_set_dhcp(enable: bool):
    c = nm_conn()
    if not c:
        log("No active NM connection; cannot set DHCP.")
        return False
    if enable:
        run_logged(["nmcli", "con", "mod", c,
             "ipv4.method", "auto", "ipv4.addresses", "",
             "ipv4.gateway", "", "ipv4.dns", "",
             "ipv4.ignore-auto-dns", "no"], timeout=10)
    else:
        run_logged(["nmcli", "con", "mod", c, "ipv4.method", "manual"], timeout=10)
    run_logged(["nmcli", "dev", "disconnect", ETH_DEV], timeout=8)
    run_logged(["nmcli", "dev", "connect",    ETH_DEV], timeout=10)
    _invalidate_nm_cache()
    return True

def apply_nm(ip, prefix):
    c = nm_conn()
    if not c:
        log("No active NM connection; skipping apply (JSON still saved).")
        return
    addr = f"{ip[0]}.{ip[1]}.{ip[2]}.{ip[3]}/{prefix}"
    run_logged(["nmcli", "con", "mod", c,
         "ipv4.method", "manual", "ipv4.addresses", addr,
         "ipv4.gateway", gw(ip), "ipv4.dns", gw(ip),
         "ipv4.ignore-auto-dns", "yes"], timeout=10)
    run_logged(["nmcli", "dev", "disconnect", ETH_DEV], timeout=8)
    run_logged(["nmcli", "dev", "connect",    ETH_DEV], timeout=10)
    _invalidate_nm_cache()

def ip_ok(ip):
    return ip[0] != 0

def wait_ip_change(target_ip):
    want = ".".join(map(str, target_ip))
    for _ in range(25):
        got, _ = get_ip_prefix()
        if got == want:
            return True
        time.sleep(0.2)
    return False

# ---------- UI helpers ----------
def load_font(sz):
    p = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    return ImageFont.truetype(p, sz) if Path(p).exists() else ImageFont.load_default()

FONT_BIG = load_font(24)
FONT_SML = load_font(16)
FONT_LCD = load_font(28)

def img_text(deck, text, bg, sub=None):
    w, h = deck.key_image_format()["size"]
    im = Image.new("RGB", (w, h), bg)
    d  = ImageDraw.Draw(im)
    if text:
        bb = d.textbbox((0, 0), text, font=FONT_BIG)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        y = (h - th) // 2 - (6 if sub else 0)
        d.text(((w - tw) // 2, y), text, font=FONT_BIG, fill=(255, 255, 255))
    if sub:
        bb2 = d.textbbox((0, 0), sub, font=FONT_SML)
        sw, sh = bb2[2] - bb2[0], bb2[3] - bb2[1]
        d.text(((w - sw) // 2, h - sh - 6), sub, font=FONT_SML, fill=(255, 255, 255))
    return PILHelper.to_native_key_format(deck, im)

def img_icon(deck, label, bg, path):
    w, h = deck.key_image_format()["size"]
    im = Image.new("RGB", (w, h), bg)
    try:
        ic = Image.open(path).convert("RGBA")
        ic.thumbnail((int(w * 0.90), int(h * 0.70)))
        im.paste(ic, ((w - ic.width) // 2, 6), ic)
    except Exception as e:
        log(f"icon load failed '{path}': {e}")
    d  = ImageDraw.Draw(im)
    bb = d.textbbox((0, 0), label, font=FONT_SML)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    d.text(((w - tw) // 2, h - th - 6), label, font=FONT_SML, fill=(255, 255, 255))
    return PILHelper.to_native_key_format(deck, im)

def blank_key(deck):
    return img_text(deck, "", (0, 0, 0))

def flash_ok(color_rgb, text):
    def _flash():
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

        if editing:
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
        deck.set_key_image(KEY_LEFT,  img_icon(deck, LEFT_START_LABEL,  (0, 60, 140), LEFT_START_ICON))
        deck.set_key_image(KEY_RIGHT, img_icon(deck, RIGHT_START_LABEL, (0, 60, 140), RIGHT_START_ICON))

# ---------- redraw (DHCP button + LCD) ----------
def redraw():
    with deck_lock:
        if dhcp_on:
            deck.set_key_image(KEY_DHCP, img_text(deck, "DHCP",   (0, 120, 0)))
        else:
            deck.set_key_image(KEY_DHCP, img_text(deck, "Manual", (0, 80, 150)))
    update_lcd()

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
def _find_usb_device_path():
    """Return the resolved sysfs path for the Elgato USB device, or None."""
    for p in Path("/sys/bus/usb/devices").iterdir():
        id_vendor_file = p / "idVendor"
        if id_vendor_file.exists():
            try:
                if id_vendor_file.read_text().strip().lower() == HID_VENDOR.lower():
                    return str(p.resolve())
            except OSError:
                pass
    return None


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
    usb_path = _find_usb_device_path()
    if usb_path:
        try:
            authorized = Path(usb_path) / "authorized"
            log(f"USB deauthorize: {usb_path}")
            authorized.write_text("0")
            time.sleep(0.8)
            log("USB reauthorize")
            authorized.write_text("1")
        except Exception as e:
            log(f"USB reset error: {e}")
    else:
        log("USB device path not found for reset, skipping")

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
    global active_key

    if not pressed:
        with active_key_lock:
            if active_key == key:
                active_key = None
        return

    with active_key_lock:
        if active_key is not None and active_key != key:
            return
        active_key = key

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

    if key == KEY_LEFT:
        handoff_to(LEFT_START_SERVICE);  return
    if key == KEY_RIGHT:
        handoff_to(RIGHT_START_SERVICE); return

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

draw_static_keys()
redraw()

# ---------- auto-refresh loop ----------
t_next = time.time() + AUTO_REFRESH_SECS
while True:
    time.sleep(0.05)
    if editing:
        continue
    if time.time() < t_next:
        continue
    t_next = time.time() + AUTO_REFRESH_SECS

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
