"""DNSalsa app for Home Assistant.

Pairs with a DNSalsa account by a short code. With a tunnel (Remote) it runs cloudflared with the tunnel token
DNSalsa hands over. Without one it keeps a dynamic-DNS hostname on the current home address: it looks up its
IPv4 and IPv6 address and calls the update API itself. Shows its state on the DNSalsa page in the Home Assistant
sidebar (ingress).

Only the Python standard library: no packages to install or update.
"""
import html
import http.client
import ipaddress
import json
import os
import random
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = os.environ.get('DNSALSA_DATA', '/data')
STATE_FILE = os.path.join(DATA, 'state.json')
PREFS_FILE = os.path.join(DATA, 'prefs.json')   # survives "Pair again", unlike state.json
OPTIONS_FILE = os.path.join(DATA, 'options.json')
HA_URL = os.environ.get('DNSALSA_HA_URL', 'http://homeassistant:8123')   # fallback when the Supervisor doesn't say
INGRESS_PORT = int(os.environ.get('DNSALSA_PORT', '8099'))
CLOUDFLARED = os.environ.get('DNSALSA_CLOUDFLARED', '/usr/local/bin/cloudflared')
METRICS = '127.0.0.1:20241'
SUPERVISOR = os.environ.get('DNSALSA_SUPERVISOR', 'http://supervisor')   # tests point this at a fake
INGRESS_PEERS = {'172.30.32.2', '127.0.0.1'}  # Home Assistant's ingress proxy (and local tests)
USER_AGENT = 'dnsalsa-ha/1.3'
MISSES_BEFORE_REMOVE = 3   # lookups in a row without an address before its record goes


def log(msg):
    print(f'[dnsalsa] {msg}', flush=True)


def options():
    try:
        with open(OPTIONS_FILE) as f:
            o = json.load(f)
    except (OSError, ValueError):
        o = {}
    # DNSALSA_SERVER: for tests and local development only; in Home Assistant the option decides.
    return {'server': (os.environ.get('DNSALSA_SERVER') or o.get('server') or 'https://dnsalsa.nl').rstrip('/')}


class State:
    """Everything the UI shows, guarded by one lock."""

    def __init__(self):
        self.lock = threading.Lock()
        self.saved = self._load()      # {mode, token, hostname, url, service} once paired; mode 'tunnel' or 'ddns'
        self.code = None               # pairing code shown to the user
        self.code_expires = 0
        self.error = None
        self.proxy_ok = None           # reverse proxy setting in Home Assistant
        self.ha = None                 # {'url', 'port', 'ssl'} from the Supervisor; None: unknown, use HA_URL
        self.ha_reachable = None       # does Home Assistant answer at that address
        self.public_ok = None          # does the public address reach Home Assistant through Cloudflare
        self.public_status = None
        self.route_error = None
        self.connected = False
        self.wake = threading.Event()  # nudges the background loops
        self.ddns = Ddns()             # dynamic DNS without a tunnel

    def _load(self):
        try:
            with open(STATE_FILE) as f:
                s = json.load(f)
            return s if s.get('token') else None
        except (OSError, ValueError):
            return None

    def save(self, data):
        tmp = STATE_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, STATE_FILE)
        with self.lock:
            self.saved = data
            self.code = None

    def forget(self):
        try:
            os.remove(STATE_FILE)
        except OSError:
            pass
        with self.lock:
            self.saved = None
            self.connected = False
            self.error = None
            self.ddns = Ddns()


def mode(saved):
    """'tunnel' (Remote, cloudflared) or 'ddns' (only the address records). Pairings from before 1.3.0 are tunnels."""
    return (saved or {}).get('mode') or 'tunnel'


class Ddns:
    """What the dynamic-DNS loop last saw; read by the page."""

    def __init__(self):
        self.records = {'ipv4': None, 'ipv6': None}   # as DNSalsa has them now
        self.found = {'ipv4': None, 'ipv6': None}     # what the last lookup found
        self.misses = {'ipv4': 0, 'ipv6': 0}          # lookups in a row without an address
        self.last_ok = None                           # time of the last successful update call
        self.error = None
        self.ipv6_remote = False                      # free account: DNSalsa left IPv6 out
        self.wake = threading.Event()                 # settings changed: update now


STATE = State()


def prefs():
    try:
        with open(PREFS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def set_pref(key, value):
    p = {**prefs(), key: value}
    tmp = PREFS_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(p, f)
    os.replace(tmp, PREFS_FILE)


# Dynamic DNS settings, set on the page. Kept in prefs.json, so they survive "Pair again".
INTERVALS = [(30, '30 seconds'), (60, '1 minute'), (300, '5 minutes'), (900, '15 minutes'), (3600, '1 hour')]
DEFAULT_INTERVAL = 300


def ddns_settings():
    p = prefs()
    interval = p.get('interval')
    return {'ipv4': p.get('ipv4', True) is not False, 'ipv6': p.get('ipv6', True) is not False,
            'interval': interval if interval in dict(INTERVALS) else DEFAULT_INTERVAL}


def http_json(method, url, body=None, headers=None, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        'Content-Type': 'application/json', 'User-Agent': USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b'{}')
        except ValueError:
            return e.code, {}


def supervisor_token():
    token = os.environ.get('SUPERVISOR_TOKEN')
    if token:
        return token
    # s6-overlay keeps the container's variables here instead of passing them on to the command
    try:
        with open('/run/s6/container_environment/SUPERVISOR_TOKEN') as f:
            return f.read().strip() or None
    except OSError:
        return None


def core_info():
    """Home Assistant's version, architecture, port and SSL setting from the Supervisor, or None."""
    token = supervisor_token()
    if not token:
        return None
    try:
        status, core = http_json('GET', f'{SUPERVISOR}/core/info', headers={'Authorization': f'Bearer {token}'}, timeout=5)
        return core.get('data') if status == 200 else None
    except Exception:  # noqa: BLE001  Supervisor busy: try again later
        return None


def apply_defaults():
    """Turn on Show in sidebar and Watchdog once. Someone who switches either off later keeps that choice."""
    if prefs().get('defaults_applied'):
        return True
    token = supervisor_token()
    if not token:
        return False
    try:
        status, _ = http_json('POST', f'{SUPERVISOR}/addons/self/options', {'ingress_panel': True, 'watchdog': True},
                              headers={'Authorization': f'Bearer {token}'}, timeout=10)
    except Exception:  # noqa: BLE001  Supervisor busy: the next try or the next start
        return False
    if status != 200:
        log(f'Could not turn on Show in sidebar and Watchdog: HTTP {status}')
        return False
    set_pref('defaults_applied', True)
    log('Turned on Show in sidebar and Watchdog')
    return True


def defaults_loop():
    for _ in range(5):
        if apply_defaults():
            return
        time.sleep(30)


def refresh_ha(info=None):
    """Where Home Assistant listens. New Home Assistant OS installs (2026.8 and later) use port 80, older ones 8123;
    with its own certificate it speaks https."""
    info = core_info() if info is None else info
    port = info.get('port') if info else None
    if isinstance(port, int) and 0 < port < 65536:
        use_ssl = info.get('ssl') is True
        ha = {'url': f"{'https' if use_ssl else 'http'}://homeassistant:{port}", 'port': port, 'ssl': use_ssl}
        with STATE.lock:
            STATE.ha = ha
        return ha
    return None


def ha_url():
    with STATE.lock:
        return STATE.ha['url'] if STATE.ha else HA_URL


def device_name(info=None):
    """Home Assistant version and architecture, shown in the dashboard when the user enters the code."""
    info = core_info() if info is None else info
    if not info:
        return 'Home Assistant'
    return f"Home Assistant {info.get('version', '')} ({info.get('arch', '')})".replace(' ()', '')


# ── Pairing ──
def pairing_loop():
    while True:
        if STATE.saved:
            STATE.wake.wait(30)
            STATE.wake.clear()
            continue
        server = options()['server']
        try:
            info = core_info()
            ha = refresh_ha(info) if info else None
            body = {'device': device_name(info), 'ddns': True}   # this version can also do dynamic DNS without a tunnel
            if ha:
                body['ha'] = {'port': ha['port'], 'ssl': ha['ssl']}
            status, r = http_json('POST', f'{server}/api/pair/start', body)
            if status != 200:
                raise RuntimeError(r.get('message') or f'HTTP {status}')
            with STATE.lock:
                STATE.code, STATE.code_expires, STATE.error = r['code'], r['expiresAt'] / 1000, None
            log(f"Pairing code: {r['code']}  In your DNSalsa dashboard: hostname > Setup > Home Assistant, "
                "or Dynamic DNS > Home Assistant app.")
            secret = r['secret']
            while time.time() < STATE.code_expires and not STATE.saved:
                time.sleep(3)
                status, p = http_json('POST', f'{server}/api/pair/poll', {'secret': secret})
                if status == 200 and p.get('status') == 'paired':
                    STATE.save({'mode': p.get('mode') or 'tunnel', 'token': p['token'], 'hostname': p['hostname'], 'url': p['url'],
                                'service': p.get('service')})
                    if p.get('mode') == 'ddns':
                        log(f"Paired. Keeping {p['hostname']} on this home's address")
                    else:
                        log(f"Paired. Home Assistant will be reachable at {p['url']}")
                    STATE.wake.set()
                    STATE.ddns.wake.set()
                    break
                if status == 410:
                    break
                if status != 200:
                    raise RuntimeError(p.get('message') or f'HTTP {status}')
        except Exception as e:  # noqa: BLE001  network trouble: show it and try again
            with STATE.lock:
                STATE.error = f'Could not reach DNSalsa: {e}'
            log(STATE.error)
            time.sleep(30)


# ── cloudflared ──
def tunnel_loop():
    proc = None
    quick_exits = 0
    while True:
        saved = STATE.saved if mode(STATE.saved) == 'tunnel' else None
        if not saved:
            if proc and proc.poll() is None:
                proc.terminate()
                proc.wait(10)
            proc = None
            STATE.wake.wait(5)
            STATE.wake.clear()
            continue
        if proc is None or proc.poll() is not None:
            if proc is not None:
                quick_exits = quick_exits + 1 if time.time() - started < 30 else 0
                if quick_exits >= 5:
                    with STATE.lock:
                        STATE.error = ('Cloudflare keeps refusing the tunnel. If you removed this hostname or its tunnel '
                                       'in the DNSalsa dashboard, press "Pair again".')
                    time.sleep(60)
            env = {**os.environ, 'TUNNEL_TOKEN': saved['token']}   # not on the command line, so it stays out of ps
            proc = subprocess.Popen([CLOUDFLARED, 'tunnel', '--no-autoupdate', '--metrics', METRICS, 'run'], env=env)
            started = time.time()
            log('cloudflared started')
        try:
            with urllib.request.urlopen(f'http://{METRICS}/ready', timeout=3) as r:
                ok = r.status == 200
        except Exception:  # noqa: BLE001
            ok = False
        with STATE.lock:
            if ok and not STATE.connected:
                log('Tunnel connected')
            STATE.connected = ok
            if ok:
                STATE.error = None
                quick_exits = 0
        STATE.wake.wait(10 if ok else 3)   # check often until connected, then calmly
        STATE.wake.clear()


# ── Dynamic DNS without a tunnel ──
def _connection(scheme, family):
    """An http.client connection class that only connects over one address family (like curl -4 / -6)."""
    base_cls = http.client.HTTPSConnection if scheme == 'https' else http.client.HTTPConnection

    class FamilyConnection(base_cls):
        def connect(self):
            err = OSError('no address')
            for af, kind, proto, _, addr in socket.getaddrinfo(self.host, self.port, family, socket.SOCK_STREAM):
                sock = socket.socket(af, kind, proto)
                try:
                    sock.settimeout(self.timeout)
                    sock.connect(addr)
                except OSError as e:
                    sock.close()
                    err = e
                    continue
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host) if scheme == 'https' else sock
                return
            raise err
    return FamilyConnection


def lookup_ip(base, family):
    """Our public address of one family, as DNSalsa's /api/ip sees it, or None (no such address, or offline)."""
    u = urllib.parse.urlsplit(base)
    conn = _connection(u.scheme, family)(u.hostname, u.port, timeout=8)
    try:
        conn.request('GET', '/api/ip', headers={'User-Agent': USER_AGENT})
        r = conn.getresponse()
        text = r.read(100).decode('ascii', 'replace').strip() if r.status == 200 else ''
    except (OSError, http.client.HTTPException):
        return None
    finally:
        conn.close()
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    # DNSalsa refuses private addresses, and one bad address would block the other family's update too.
    if ip.version != (4 if family == socket.AF_INET else 6) or not (ip.is_global or os.environ.get('DNSALSA_ALLOW_PRIVATE')):
        return None
    return str(ip)


def ddns_update():
    """One round: look up both addresses, send what we found in one call. Returns seconds until the next round."""
    saved, d = STATE.saved, STATE.ddns
    settings = ddns_settings()
    interval = settings['interval'] + random.uniform(1, 5)   # a bit more than the interval: DNSalsa allows one call per 30 s
    base = options()['server']
    body = {'type': 'both'}
    for key, family in (('ipv4', socket.AF_INET), ('ipv6', socket.AF_INET6)):
        if not settings[key]:
            body[key] = 'none'        # turned off on this page: that record goes
            d.found[key], d.misses[key] = None, 0
            continue
        ip = lookup_ip(base, family)
        d.found[key] = ip
        if ip:
            d.misses[key] = 0
            body[key] = ip
        else:
            d.misses[key] += 1
            if d.misses[key] >= MISSES_BEFORE_REMOVE:
                body[key] = 'none'    # gone for a while (no IPv6 any more, say): don't leave a dead record
    if len(body) == 1:
        d.error = 'Could not find your address. Is the internet connection down? Trying again soon.'
        return min(interval, 60)
    try:
        status, r = http_json('POST', f'{base}/api/update', body, headers={'Authorization': f"Bearer {saved['token']}"})
    except Exception as e:  # noqa: BLE001  network trouble
        d.error = f'Could not reach DNSalsa: {e}'
        return min(interval, 60)
    if status == 200:
        changed = r.get('changed')
        d.records = {'ipv4': r.get('ipv4'), 'ipv6': r.get('ipv6')}
        d.ipv6_remote = r.get('ipv6Skipped') == 'remote_only'
        d.last_ok, d.error = time.time(), None
        if changed:
            log(f"Updated {saved['hostname']}: IPv4 {d.records['ipv4'] or 'none'}, IPv6 {d.records['ipv6'] or 'none'}")
        return interval
    if status == 429:
        return 31 + random.uniform(0, 3)
    if status == 401:
        d.error = 'DNSalsa no longer accepts this app\'s token: you made a new one in the dashboard, or removed the hostname. Press "Pair again".'
    elif status == 409:
        d.error = r.get('message') or 'This hostname runs through a tunnel now. Press "Pair again".'
    else:
        d.error = r.get('message') or f'DNSalsa answered HTTP {status}.'
    log(d.error)
    return interval


def ddns_loop():
    while True:
        wait = 5
        if mode(STATE.saved) == 'ddns':
            wait = ddns_update()
        d = STATE.ddns
        d.wake.wait(wait)
        d.wake.clear()


# ── Home Assistant reverse proxy setting ──
# Home Assistant's own certificate is for your domain, never for "homeassistant": don't verify it on this hop.
INSECURE = ssl.create_default_context()
INSECURE.check_hostname = False
INSECURE.verify_mode = ssl.CERT_NONE


def check_proxy():
    """Home Assistant answers 400 to a forwarded request when 'Trust X-Forwarded-For' is off or we aren't trusted.
    /manifest.json needs no login, so this check never shows up as a failed login in Home Assistant.
    203.0.113.7 is a documentation address (TEST-NET-3) that no real visitor has.
    No answer at all: Home Assistant is restarting, or listens somewhere else; the proxy setting is unknown then."""
    url = ha_url()
    req = urllib.request.Request(f'{url}/manifest.json', headers={'X-Forwarded-For': '203.0.113.7'})
    try:
        with urllib.request.urlopen(req, timeout=5, context=INSECURE if url.startswith('https:') else None) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:  # noqa: BLE001
        code = None
    with STATE.lock:
        STATE.ha_reachable = code is not None
        STATE.proxy_ok = None if code is None else code != 400
    return STATE.proxy_ok


ROUTE_RETRY = 600   # seconds between attempts to fix the tunnel's route


def sync_route(last_try=[0.0]):  # noqa: B006  remembers the last attempt between calls
    """Point the tunnel at the address Home Assistant really uses, if DNSalsa has another one on file."""
    saved, ha = STATE.saved, STATE.ha
    if not saved or not ha or saved.get('service') == ha['url'] or time.time() - last_try[0] < ROUTE_RETRY:
        return
    last_try[0] = time.time()
    try:
        status, r = http_json('POST', f"{options()['server']}/api/pair/service",
                              {'hostname': saved['hostname'], 'token': saved['token'], 'port': ha['port'], 'ssl': ha['ssl']})
    except Exception as e:  # noqa: BLE001
        log(f'Could not update the route: {e}')
        return
    if status == 200:
        STATE.save({**saved, 'service': r.get('service', ha['url'])})
        with STATE.lock:
            STATE.route_error = None
        if r.get('changed'):
            log(f"Tunnel now points at Home Assistant on {ha['url']}")
    else:
        with STATE.lock:
            STATE.route_error = r.get('message') or f'HTTP {status}'
        log(f'Could not update the route: {STATE.route_error}')


def check_public():
    """Fetch /manifest.json through Cloudflare, the way a visitor would. 502 or 530 means Cloudflare can't get through."""
    saved = STATE.saved
    if not saved or not STATE.connected:
        with STATE.lock:
            STATE.public_ok, STATE.public_status = None, None
        return None
    req = urllib.request.Request(f"{saved['url']}/manifest.json", headers={'User-Agent': 'dnsalsa-ha/1.1'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:  # noqa: BLE001  our own internet is down: unknown
        code = None
    with STATE.lock:
        STATE.public_status = code
        STATE.public_ok = None if code is None else code < 500
    return STATE.public_ok


def check_all():
    refresh_ha()
    check_proxy()
    sync_route()
    check_public()


def health_loop():
    while True:
        if mode(STATE.saved) == 'ddns':   # no tunnel: nothing of Home Assistant's to check
            time.sleep(15)
            continue
        check_all()
        with STATE.lock:
            ok = STATE.public_ok is not False and STATE.ha_reachable is not False
        time.sleep(60 if ok else 15)


# ── Page in the Home Assistant sidebar ──
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')


# Served next to the page with a year-long cache, so the browser downloads them once instead of with every page (60 KB inline).
FONTS = {}
for _name in ('space-grotesk-latin-wght-normal.woff2', 'space-mono-latin-700-normal.woff2'):
    try:
        with open(os.path.join(FONT_DIR, _name), 'rb') as _f:
            FONTS[_name] = _f.read()
    except OSError:
        pass


FLAME = ('<svg class="flame" viewBox="0 0 52 70" aria-hidden="true"><defs><linearGradient id="fl" x1="0" y1="70" x2="52" y2="0" '
         'gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#b3122a"/><stop offset=".5" stop-color="#d9401c"/>'
         '<stop offset="1" stop-color="#f0782a"/></linearGradient></defs>'
         '<path d="M26 68 C10 68 4 54 4 42 C4 28 14 22 18 10 C20 4 22 0 26 0 C26 8 30 12 34 16 C38 20 48 28 48 42 C48 56 42 68 26 68Z" fill="url(#fl)"/>'
         '<path d="M26 60 C18 60 14 52 14 44 C14 36 20 32 22 26 C24 34 30 36 34 44 C34 52 32 60 26 60Z" fill="#fff" fill-opacity=".14"/></svg>')

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DNSalsa</title><style>
@font-face{font-family:'Space Grotesk';src:url(fonts/space-grotesk-latin-wght-normal.woff2) format('woff2');font-weight:300 700;font-display:block}
@font-face{font-family:'Space Mono';src:url(fonts/space-mono-latin-700-normal.woff2) format('woff2');font-weight:700;font-display:block}
:root{color-scheme:dark;--red:oklch(48% .23 22);--orange:oklch(63% .22 42);--heat:oklch(72% .2 55);
--bg:oklch(11% .015 22);--card:oklch(15% .018 22);--card2:oklch(18% .02 22);--line:oklch(24% .015 22);
--muted:oklch(66% .012 55);--fg:oklch(96% .008 55);--ok:oklch(74% .16 150);
--flame:linear-gradient(120deg,var(--red),var(--orange) 55%,var(--heat));
--sans:'Space Grotesk',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;--mono:'Space Mono',ui-monospace,Menlo,Consolas,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);font:15px/1.6 var(--sans);-webkit-font-smoothing:antialiased;min-height:100vh;
background-image:radial-gradient(60rem 30rem at 50% -12rem,oklch(48% .23 22 / .22),transparent 70%)}
main{max-width:620px;margin:0 auto;padding:28px 16px 40px;display:grid;gap:14px}
header{display:flex;align-items:center;gap:12px;margin-bottom:10px}
.flame{width:26px;height:35px;flex:none;filter:drop-shadow(0 0 12px oklch(63% .22 42 / .45))}
.brand{font-weight:600;font-size:22px;letter-spacing:-.01em}.brand span{color:var(--orange)}
.pill{margin-left:auto;display:inline-flex;align-items:center;gap:7px;font-size:13px;font-weight:500;padding:5px 11px;border-radius:99px;
border:1px solid var(--line);background:var(--card);color:var(--muted);white-space:nowrap}
.pill i{width:8px;height:8px;border-radius:50%;background:currentColor}
.pill.ok{color:var(--ok);border-color:oklch(74% .16 150 / .35)}.pill.ok i{box-shadow:0 0 10px currentColor}
.pill.wait{color:var(--heat);border-color:oklch(72% .2 55 / .35)}.pill.wait i{animation:pulse 1.4s ease-in-out infinite}
@keyframes pulse{50%{opacity:.3}}
@media(prefers-reduced-motion:reduce){.pill.wait i{animation:none}}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;display:grid;gap:12px}
.card.hero{background:linear-gradient(var(--card2),var(--card)) padding-box,var(--flame) border-box;border:1px solid transparent}
.card.warn{border-left:3px solid var(--heat)}
.label{font:700 11px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
h2{font-size:17px;font-weight:600;letter-spacing:-.01em}h2.warn{color:var(--heat)}
.code{font:700 clamp(34px,10vw,48px)/1.05 var(--mono);letter-spacing:.06em;background:var(--flame);-webkit-background-clip:text;background-clip:text;color:transparent}
.addr{font-size:clamp(18px,5vw,22px);font-weight:600;word-break:break-all}
a{color:var(--orange);text-underline-offset:3px}a:hover{color:var(--heat)}
.muted{color:var(--muted)}.small{font-size:13px}.ok{color:var(--ok)}
ol{padding-left:22px;display:grid;gap:6px}li::marker{color:var(--orange);font-weight:600}
button,a.btn{font:500 14px var(--sans);padding:9px 16px;border-radius:9px;border:1px solid var(--line);background:var(--card2);color:var(--fg);
cursor:pointer;text-decoration:none;display:inline-block}
button:hover,a.btn:hover{border-color:var(--muted);color:var(--fg)}
.primary{background:var(--flame)!important;border-color:transparent!important;color:#fff!important;font-weight:600!important}
.primary:hover{filter:brightness(1.1)}
button:disabled{opacity:.5;cursor:wait}
code{font:700 14px var(--mono);background:var(--bg);border:1px solid var(--line);border-radius:7px;padding:4px 8px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.copy{margin-top:6px}.copy button{padding:5px 11px;font-size:13px}
.check{display:flex;gap:10px;align-items:center}.check svg{flex:none;color:var(--ok)}
.recs{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;align-items:baseline}.recs dt{font:700 11px/1.9 var(--mono);letter-spacing:.14em;color:var(--muted)}
.recs dd{font:700 14px var(--mono);word-break:break-all}.recs dd.off{color:var(--muted);font-weight:400;font-family:var(--sans)}
.set{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 0;border-top:1px solid var(--line)}.set:first-of-type{border-top:0}
.set small{display:block;color:var(--muted);font-size:13px}
.switch{appearance:none;-webkit-appearance:none;flex:none;width:44px;height:26px;border-radius:99px;background:var(--line);position:relative;cursor:pointer;transition:background .15s}
.switch::after{content:'';position:absolute;top:3px;left:3px;width:20px;height:20px;border-radius:50%;background:var(--fg);transition:transform .15s}
.switch:checked{background:var(--orange)}.switch:checked::after{transform:translateX(18px)}
.switch:focus-visible,select:focus-visible{outline:2px solid var(--heat);outline-offset:2px}
select{font:500 14px var(--sans);padding:7px 10px;border-radius:9px;border:1px solid var(--line);background:var(--card2);color:var(--fg)}
@media(prefers-reduced-motion:reduce){.switch,.switch::after{transition:none}}
footer{margin-top:6px;text-align:center;font-size:12px;color:var(--muted)}footer a{color:var(--muted)}
</style></head><body><main__REFRESH__>
<header>__FLAME__<div class="brand">DNS<span>alsa</span></div>__PILL__</header>
__BODY__
<footer><a href="https://dnsalsa.nl" target="_blank" rel="noopener">dnsalsa.nl</a> · <a href="mailto:info@dnsalsa.nl">info@dnsalsa.nl</a></footer>
</main><script>
// Refresh by swapping <main> in place. A full reload blanks the Home Assistant frame (white flash) and parses the fonts again.
let tm;
function schedule(){clearTimeout(tm);if(document.querySelector('main[data-refresh]'))tm=setTimeout(soft,4000)}
async function soft(){
try{const r=await fetch(location.href,{cache:'no-store'});if(!r.ok)throw 0;
const m=new DOMParser().parseFromString(await r.text(),'text/html').querySelector('main');if(!m)throw 0;
document.querySelector('main').replaceWith(m);bind(m);repaint()}catch(e){}
schedule()}
function bind(root){
root.querySelectorAll('form[data-post]').forEach(f=>f.addEventListener('submit',async e=>{e.preventDefault();
const b=f.querySelector('button');if(b)b.disabled=true;
try{await fetch(f.dataset.post,{method:'POST',body:new URLSearchParams(new FormData(f))})}catch(e){}soft()}));
// Settings save as soon as you flip a switch or pick an interval.
root.querySelectorAll('form[data-auto]').forEach(f=>f.addEventListener('change',()=>f.requestSubmit()));
// Copy also works on plain http:// (no secure context), where navigator.clipboard is missing.
root.querySelectorAll('[data-copy]').forEach(b=>b.addEventListener('click',async()=>{
const t=document.getElementById(b.dataset.copy).textContent;let ok=false;
try{await navigator.clipboard.writeText(t);ok=true}catch(e){}
if(!ok){const a=document.createElement('textarea');a.value=t;document.body.append(a);a.select();
try{ok=document.execCommand('copy')}catch(e){}a.remove()}
b.textContent=ok?'Copied':'Select and copy';setTimeout(()=>b.textContent='Copy',2000)}))}
bind(document);schedule();
// iOS WebKit in the Home Assistant app sometimes doesn't repaint the panel after the fonts arrive, and the page stays blank
// until something else forces a layout. Force one ourselves: when the fonts are ready, on returning to the panel, and
// after each in-place refresh.
function repaint(){const m=document.querySelector('main');if(!m)return;m.style.transform='translateZ(0)';void m.offsetHeight;
requestAnimationFrame(()=>{m.style.transform=''})}
if(document.fonts&&document.fonts.ready)document.fonts.ready.then(repaint);
window.addEventListener('pageshow',repaint);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)repaint()});
</script></body></html>"""
PAGE = PAGE.replace('__FLAME__', FLAME)

CHECK = ('<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" '
         'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>')


def ago(t):
    secs = int(time.time() - t)
    if secs < 60:
        return 'just now'
    mins = secs // 60
    return f"{mins} minute{'s' if mins != 1 else ''} ago" if mins < 60 else f"{mins // 60} h ago"


def render_ddns(saved):
    """The page without a tunnel: the address records, the settings, and what's still needed for remote access."""
    d, settings = STATE.ddns, ddns_settings()
    if d.error:
        pill = '<span class="pill wait"><i></i>Problem</span>'
    elif d.last_ok:
        pill = '<span class="pill ok"><i></i>Updating</span>'
    else:
        pill = '<span class="pill wait"><i></i>Starting</span>'
    parts = []
    if d.error:
        parts.append(f'<div class="card warn"><p>{html.escape(d.error)}</p></div>')

    def rec(key, label):
        if not settings[key]:
            return f'<dt>{label}</dt><dd class="off">Off</dd>'
        if d.records[key]:
            return f'<dt>{label}</dt><dd>{html.escape(d.records[key])}</dd>'
        why = 'IPv6 comes with Remote' if key == 'ipv6' and d.ipv6_remote else ('none found' if d.last_ok else 'checking…')
        return f'<dt>{label}</dt><dd class="off">{why}</dd>'
    when = f'Last update {ago(d.last_ok)}.' if d.last_ok else 'Looking up your address…'
    host = html.escape(saved['hostname'])
    parts.append(f'''<div class="card hero"><div class="label">Your address</div><div class="addr">{host}</div>
<dl class="recs">{rec('ipv4', 'IPV4')}{rec('ipv6', 'IPV6')}</dl>
<p class="muted small">{when} DNSalsa only changes the record when your address changes.</p></div>''')
    opts = ''.join(f'<option value="{v}"{" selected" if v == settings["interval"] else ""}>{l}</option>' for v, l in INTERVALS)
    v6_note = ('<small>Your account gets IPv4 only; IPv6 comes with Remote.</small>' if d.ipv6_remote
               else '<small>Turning one off removes that record.</small>')
    parts.append(f'''<div class="card"><h2>Settings</h2><form data-post="settings" data-auto>
<label class="set"><span>IPv4<small>The A record.</small></span><input class="switch" type="checkbox" name="ipv4"{' checked' if settings['ipv4'] else ''}></label>
<label class="set"><span>IPv6{v6_note}</span><input class="switch" type="checkbox" name="ipv6"{' checked' if settings['ipv6'] else ''}></label>
<label class="set"><span>Check every<small>A lookup costs nothing; DNS only changes when your address does.</small></span>
<select name="interval">{opts}</select></label></form></div>''')
    parts.append('''<div class="card"><h2>Reaching Home Assistant from outside</h2>
<p class="muted small">This address points at your home. To open Home Assistant with it from outside, your router needs a port forward to
Home Assistant. With DNSalsa Remote you need no port forward, and your home IP stays hidden.</p></div>''')
    parts.append('''<div class="card"><h2>Pair again</h2><p class="muted small">Use another hostname, another DNSalsa account, or Remote.
This app stops updating the current address.</p>
<form class="row" data-post="unpair"><button>Pair again</button></form></div>''')
    refresh = '' if d.last_ok and not d.error else ' data-refresh'
    return PAGE.replace('__PILL__', pill).replace('__BODY__', '\n'.join(parts)).replace('__REFRESH__', refresh)


def render():
    if mode(STATE.saved) == 'ddns' and STATE.saved:
        return render_ddns(STATE.saved)
    with STATE.lock:
        saved, code, expires, error = STATE.saved, STATE.code, STATE.code_expires, STATE.error
        proxy_ok, connected = STATE.proxy_ok, STATE.connected
        ha_reachable, public_ok, public_status, route_error = STATE.ha_reachable, STATE.public_ok, STATE.public_status, STATE.route_error
        target = STATE.ha['url'] if STATE.ha else HA_URL
    server = html.escape(options()['server'])
    # The app can't see Home Assistant's IP ban settings without reading its whole configuration, so the user says when it's done.
    ban_tip = not prefs().get('ban_tip_hidden')
    parts = []
    if not saved:
        pill = '<span class="pill"><i></i>Not paired</span>'
    elif connected and public_ok is False:
        pill = '<span class="pill wait"><i></i>Not reachable</span>'
    elif connected:
        pill = '<span class="pill ok"><i></i>Connected</span>'
    else:
        pill = '<span class="pill wait"><i></i>Connecting</span>'
    if error:
        parts.append(f'<div class="card warn"><p>{html.escape(error)}</p></div>')
    if not saved:
        if code:
            mins = max(1, int((expires - time.time()) / 60))
            parts.append(f'''<div class="card hero"><div class="label">Pairing code</div>
<div class="row"><div class="code" id="pair-code">{html.escape(code)}</div><button type="button" data-copy="pair-code">Copy</button></div>
<ol><li>Open <a href="{server}/dashboard" target="_blank" rel="noopener">your DNSalsa dashboard</a>.</li>
<li>At the hostname you want, pick what it should do:<br>
<b>Setup → Home Assistant</b> to open Home Assistant from anywhere, without a port forward. This needs Remote, and the first
month is free.<br>
<b>Setup → Dynamic DNS → Home Assistant app</b> to only keep the hostname on your home IP address. That's free, but you need a
port forward on your router to reach Home Assistant with it.</li>
<li>Enter the code above. This page updates by itself.</li></ol>
<p class="muted small">The code works for {mins} more minute{'s' if mins != 1 else ''}; after that you get a new one here.</p></div>''')
        else:
            parts.append('<div class="card"><p class="muted">Getting a pairing code…</p></div>')
    else:
        url = html.escape(saved['url'])
        parts.append(f'''<div class="card hero"><div class="label">Your Home Assistant address</div>
<a class="addr" href="{url}" target="_blank" rel="noopener">{url}</a>
<p class="muted small">In the Home Assistant app on your phone: Settings → Companion app → Server → External URL.</p></div>''')
    if saved and ha_reachable is False:
        parts.append(f'''<div class="card warn"><h2 class="warn">Can't reach Home Assistant</h2>
<p>This app gets no answer from Home Assistant at <code>{html.escape(target)}</code>.
If Home Assistant is restarting, this clears by itself in a minute.</p></div>''')
    elif saved and connected and public_ok is False:
        why = (f'DNSalsa could not update the tunnel: {html.escape(route_error)}. Press "Pair again" to set it up fresh.'
               if route_error else 'This app is pointing the tunnel at the right address now; give it a minute.')
        parts.append(f'''<div class="card warn"><h2 class="warn">Your address doesn't reach Home Assistant yet</h2>
<p>Cloudflare answers <code>{html.escape(str(public_status))}</code> for your address: the tunnel is up, but it can't get through
to Home Assistant on <code>{html.escape(target)}</code>.</p><p class="muted small">{why}</p>
<form class="row" data-post="check"><button>Check again</button></form></div>''')
    if proxy_ok is False:
        parts.append('''<div class="card warn"><h2 class="warn">One setting needed in Home Assistant</h2>
<p>Home Assistant blocks traffic from the tunnel until you allow it. This takes about 30 seconds:</p>
<div class="row"><a class="btn primary" href="/config/network" target="_top">Open network settings</a></div>
<ol><li>Scroll to <b>HTTP Server</b>, part <b>Reverse proxy</b>.</li>
<li>Turn on <b>Trust X-Forwarded-For</b>.</li>
<li>Add this address as a trusted proxy:
<span class="row copy"><code id="net">172.30.33.0/24</code><button type="button" data-copy="net">Copy</button></span></li>
__BAN_STEP__<li>Save, and confirm the new settings after Home Assistant restarts.</li></ol>
<p class="muted small">Already have a proxy in that list? Add this address, keep the others. This page turns green by itself once it works.</p>
<form class="row" data-post="check"><button>Check again</button></form></div>'''.replace('__BAN_STEP__', (
            '<li>Recommended, same section: turn on <b>Enable IP banning</b>, turn off <b>Unlimited login attempts</b> '
            'and set <b>Login attempts before ban</b> to <b>10</b>.</li>\n') if ban_tip else ''))
    elif proxy_ok and saved and public_ok is not False:
        tip = ('''<p class="muted small">Not done yet? In <a href="/config/network" target="_top">network settings</a>, turn on <b>Enable IP banning</b>
with <b>Login attempts before ban</b> at 10. Bans stay until you remove them from <code>ip_bans.yaml</code>.</p>
<form class="row" data-post="hide-ban-tip"><button>Done, hide this</button></form>''' if ban_tip else '')
        parts.append(f'''<div class="card"><div class="check">{CHECK}<p>Home Assistant accepts traffic from the tunnel.</p></div>
{tip}</div>''')
    if saved:
        parts.append('''<div class="card"><h2>Pair again</h2><p class="muted small">Use another hostname or another DNSalsa account.
This disconnects the current address from this Home Assistant.</p>
<form class="row" data-post="unpair"><button>Pair again</button></form></div>''')
    refresh = '' if (saved and connected and proxy_ok and public_ok is not False) else ' data-refresh'
    return PAGE.replace('__PILL__', pill).replace('__BODY__', '\n'.join(parts)).replace('__REFRESH__', refresh)


def save_settings(form):
    """The settings form: a ticked switch is sent as 'on', an unticked one not at all."""
    f = urllib.parse.parse_qs(form)
    try:
        interval = int(f.get('interval', [DEFAULT_INTERVAL])[0])
    except ValueError:
        interval = DEFAULT_INTERVAL
    p = {**prefs(), 'ipv4': 'ipv4' in f, 'ipv6': 'ipv6' in f, 'interval': interval if interval in dict(INTERVALS) else DEFAULT_INTERVAL}
    tmp = PREFS_FILE + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump(p, fh)
    os.replace(tmp, PREFS_FILE)
    log(f"Settings: IPv4 {'on' if p['ipv4'] else 'off'}, IPv6 {'on' if p['ipv6'] else 'off'}, every {p['interval']} s")
    STATE.ddns.wake.set()   # apply now


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _allowed(self):
        if self.client_address[0] not in INGRESS_PEERS:
            self.send_error(403)
            return False
        return True

    def _send(self, code, body, ctype='text/html; charset=utf-8', cache='no-store'):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', cache)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._allowed():
            return
        name = self.path.split('?')[0].rsplit('/', 1)[-1]
        if '/fonts/' in self.path and name in FONTS:
            return self._send(200, FONTS[name], 'font/woff2', 'public, max-age=31536000, immutable')
        if self.path.rstrip('/').endswith('status'):
            with STATE.lock:
                d = STATE.ddns
                return self._send(200, json.dumps({'paired': bool(STATE.saved), 'mode': mode(STATE.saved) if STATE.saved else None,
                                                   'records': d.records, 'lastUpdate': d.last_ok, 'ddnsError': d.error,
                                                   'code': STATE.code, 'connected': STATE.connected,
                                                   'proxyOk': STATE.proxy_ok, 'haReachable': STATE.ha_reachable,
                                                   'publicOk': STATE.public_ok, 'error': STATE.error}), 'application/json')
        self._send(200, render())

    def do_POST(self):
        if not self._allowed():
            return
        path = self.path.rstrip('/')
        if path.endswith('check'):
            check_all()
        elif path.endswith('settings'):
            save_settings(self.rfile.read(min(int(self.headers.get('Content-Length') or 0), 2000)).decode('utf-8', 'replace'))
        elif path.endswith('hide-ban-tip'):
            set_pref('ban_tip_hidden', True)
        elif path.endswith('unpair'):
            STATE.forget()
            log('Unpaired; getting a new pairing code')
            STATE.wake.set()
        self._send(200, '{"ok":true}', 'application/json')


def main():
    os.makedirs(DATA, exist_ok=True)
    for fn in (defaults_loop, pairing_loop, tunnel_loop, health_loop, ddns_loop):
        threading.Thread(target=fn, daemon=True).start()
    log(f'DNSalsa app running, server {options()["server"]}')
    ThreadingHTTPServer(('0.0.0.0', INGRESS_PORT), Handler).serve_forever()


if __name__ == '__main__':
    main()
