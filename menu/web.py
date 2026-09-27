#!/usr/bin/env python3
"""Web control page: http://<pi-ip>/ from any phone or laptop on the network.
Switches which service has the Stream Deck, sets the timezone and IP, and
restarts or shuts down the Pi. Runs as root, always on (menu-web.service), so
it works whichever of menu/Companion/Satellite has the deck. No password, by
the user's choice: Companion's own web UI is just as open on this network."""
import json, socket, threading, time
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from zoneinfo import ZoneInfo

import pi_control as pc

PORT = 80
PAGE = Path(__file__).with_name("web") / "index.html"


def log(s):
    pc.log(f"web: {s}")


def later(fn, *args, delay=0.5):
    """Run fn after the HTTP reply has gone out; network changes and power
    actions cut the connection the reply would travel on."""
    def go():
        time.sleep(delay)
        try:
            fn(*args)
        except Exception as e:
            log(f"{getattr(fn, '__name__', fn)} failed: {e}")
    threading.Thread(target=go, daemon=True).start()


def status():
    ip, mask = pc.get_ip_mask()
    zone = pc.current_zone()
    try:
        now = datetime.now(ZoneInfo(zone)) if zone else datetime.now().astimezone()
        clock = now.strftime("%Z %H:%M")
    except Exception:
        clock = ""
    method = pc.nm_ipv4_method()
    return {
        "owner": pc.deck_owner(),
        "installed": {s: pc.service_installed(s) for s in pc.DECK_SERVICES},
        "ip": ".".join(map(str, ip)) if pc.ip_ok(ip) else None,
        "mask": ".".join(map(str, mask)),
        "dhcp": None if method is None else method == "auto",
        "zone": zone,
        "clock": clock,
        "hostname": socket.gethostname().split(".")[0],
    }


def parse_quad(s):
    parts = str(s).strip().split(".")
    if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return None
    return [int(p) for p in parts]


def set_network(body):
    if not pc.nm_conn():
        return 409, {"error": f"NetworkManager has no active connection on {pc.ETH_DEV}."}
    if body.get("mode") == "dhcp":
        log("network: switching to DHCP")
        later(pc.nm_set_dhcp, True)
        return 202, {"ok": True}
    ip, mask = parse_quad(body.get("ip", "")), parse_quad(body.get("mask", ""))
    if not ip or not pc.ip_ok(ip):
        return 400, {"error": "That IP address isn't valid."}
    prefix = pc.mask_to_prefix_if_valid(mask) if mask else None
    if prefix is None:
        return 400, {"error": "That subnet mask isn't valid."}
    log(f"network: manual {'.'.join(map(str, ip))}/{prefix}")
    # Same steps as the deck's apply (menu.py _apply_worker).
    def apply():
        pc.write_json(ip, mask)
        pc.apply_nm(ip, prefix)
    later(apply)
    return 202, {"ok": True}


def set_zone(body):
    zone = str(body.get("zone", ""))
    if zone not in pc.list_timezones():
        return 400, {"error": "Unknown timezone."}
    if not pc.set_timezone(zone):
        return 500, {"error": "Couldn't set the timezone; see the menu log."}
    log(f"timezone: {zone}")
    return 200, {"ok": True}


def switch(body):
    target = str(body.get("target", ""))
    if target not in pc.DECK_SERVICES or not pc.service_installed(target):
        return 400, {"error": f"{target or 'That'} isn't installed."}
    log(f"deck: switching to {target}")
    pc.switch_deck(target)
    return 200, {"ok": True}


def power(body):
    action = body.get("action")
    cmd = {"restart": ["systemctl", "reboot"], "shutdown": ["systemctl", "poweroff"]}.get(action)
    if not cmd:
        return 400, {"error": "Unknown action."}
    log(f"power: {action}")
    later(pc.run, cmd, delay=1.0)
    return 202, {"ok": True}


POST_ROUTES = {"/api/network": set_network, "/api/timezone": set_zone,
               "/api/deck": switch, "/api/power": power}


class Handler(BaseHTTPRequestHandler):
    server_version = "sdpi-web"

    def send(self, code, body, ctype="application/json", extra=()):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        try:
            if path == "/":
                self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/status":
                self.send(200, status())
            elif path == "/api/timezones":
                self.send(200, pc.list_timezones())
            elif path == "/api/ping":
                # Polled from the old address after an IP change to find the new one.
                self.send(200, {"ok": True}, extra=[("Access-Control-Allow-Origin", "*")])
            else:
                self.send(404, {"error": "Not found."})
        except Exception as e:
            log(f"GET {path} error: {e}")
            self.send(500, {"error": str(e)})

    def do_POST(self):
        route = POST_ROUTES.get(self.path)
        if not route:
            return self.send(404, {"error": "Not found."})
        # Requiring JSON means another website open in the same browser can't
        # post here: a cross-site JSON request needs a CORS preflight, which
        # this server never approves.
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self.send(415, {"error": "Expected JSON."})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("expected an object")
        except Exception:
            return self.send(400, {"error": "Bad request."})
        try:
            code, reply = route(body)
        except Exception as e:
            log(f"POST {self.path} error: {e}")
            code, reply = 500, {"error": str(e)}
        self.send(code, reply)

    def log_message(self, *args):
        pass   # the page polls status every few seconds; don't flood the journal


if __name__ == "__main__":
    log(f"listening on port {PORT}")
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
