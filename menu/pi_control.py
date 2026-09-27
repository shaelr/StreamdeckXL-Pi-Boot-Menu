#!/usr/bin/env python3
"""Pi-side controls shared by the deck menu (menu.py) and the web control page
(web.py): network (NetworkManager), timezone, and switching which service has
the Stream Deck. Nothing here touches the deck itself, so web.py can use it
without the StreamDeck/Pillow libraries."""
import time, json, subprocess, re
from pathlib import Path

# ================== CONFIG ==================
ETH_DEV = "eth0"
JSON_PATH = "/etc/menu/menu.json"
LOG_FILE = "/var/log/menu.log"
HID_VENDOR        = "0FD9"
NM_CONN_TTL       = 5.0
# ============================================

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

# ---------- timezone ----------
def current_zone():
    try:
        return str(Path("/etc/localtime").resolve()).split("zoneinfo/", 1)[1]
    except Exception:
        return ""

def set_timezone(zone):
    try:
        r = run(["timedatectl", "set-timezone", zone], timeout=5)
    except Exception as e:
        log(f"set-timezone {zone} error: {e}")
        return False
    if r.returncode != 0:
        log(f"set-timezone {zone} failed: {r.stderr.strip()}")
        return False
    # Python caches the zone at startup; re-read it so this log follows the change.
    time.tzset()
    log(f"timezone set to {zone}")
    return True

# ---------- Stream Deck owner (menu / companion / satellite) ----------
def find_usb_device_path():
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

def reset_usb_device():
    """Deauthorize/reauthorize the deck's USB port: the most reliable way to make
    the kernel fully tear down and re-enumerate it, creating fresh /dev/hidraw*
    nodes that are not stale from the previous owner's session."""
    usb_path = find_usb_device_path()
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

def service_installed(name):
    return Path(f"/etc/systemd/system/{name}.service").exists()

def is_active(name):
    return run(["systemctl", "is-active", "--quiet", name], timeout=5).returncode == 0

# Only one of these can hold the Stream Deck at a time.
DECK_SERVICES = ("menu", "companion", "satellite")

def deck_owner():
    return next((s for s in DECK_SERVICES if is_active(s)), None)

def switch_deck(target):
    """Hand the deck to target from outside all three services (the web page):
    stop whichever has it, reset the USB port, start target. The same steps as
    menu.py's handoff_to() and back-to-menu.sh, which run from inside an owner."""
    if target not in DECK_SERVICES or not service_installed(target):
        raise ValueError(f"{target} is not installed")
    if is_active(target):
        return
    for s in DECK_SERVICES:
        if is_active(s):
            log(f"switch: stopping {s}")
            run_logged(["systemctl", "stop", s], timeout=60)
    # Give the kernel a moment to finish closing the fds the stopped process held.
    time.sleep(0.3)
    reset_usb_device()
    # Give udev a moment to apply permission rules after the USB reset.
    time.sleep(0.5)
    log(f"switch: starting {target}")
    run_logged(["systemctl", "start", "--no-block", target], timeout=10)

def list_timezones():
    try:
        return run(["timedatectl", "list-timezones"], timeout=10).stdout.split()
    except Exception as e:
        log(f"list-timezones error: {e}")
        return []
