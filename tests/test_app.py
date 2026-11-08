"""Tests for the DNSalsa app. Standard library only: python3 -m unittest discover -s tests"""
import json
import os
import re
import stat
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, 'dnsalsa')
DATA = tempfile.mkdtemp(prefix='dnsalsa-test-')
os.environ['DNSALSA_DATA'] = DATA
sys.path.insert(0, os.path.join(APP, 'rootfs', 'app'))
import main  # noqa: E402  (needs DNSALSA_DATA set first)


def serve(handler_cls):
    """Start a throwaway HTTP server; returns (base_url, server)."""
    srv = ThreadingHTTPServer(('127.0.0.1', 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f'http://127.0.0.1:{srv.server_address[1]}', srv


class FakeHA(BaseHTTPRequestHandler):
    """Answers like Home Assistant: 400 to a forwarded request unless the proxy is trusted."""
    trusted = False
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        FakeHA.seen.append((self.path, dict(self.headers)))
        code = 400 if self.headers.get('X-Forwarded-For') and not FakeHA.trusted else 200
        self.send_response(code)
        self.end_headers()
        self.wfile.write(b'{}')


class ProxyCheck(unittest.TestCase):
    def setUp(self):
        self.url, self.srv = serve(FakeHA)
        main.HA_URL = self.url
        with main.STATE.lock:
            main.STATE.ha = None   # no Supervisor here: the fallback address
        FakeHA.seen = []

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_not_trusted_yet(self):
        FakeHA.trusted = False
        self.assertIs(main.check_proxy(), False)

    def test_trusted(self):
        FakeHA.trusted = True
        self.assertIs(main.check_proxy(), True)

    def test_uses_a_public_page_without_login(self):
        # /api/ without a token showed up in Home Assistant as a failed login every minute
        FakeHA.trusted = True
        main.check_proxy()
        path, headers = FakeHA.seen[-1]
        self.assertEqual(path, '/manifest.json')
        self.assertNotIn('Authorization', headers)
        self.assertEqual(headers.get('X-Forwarded-For'), '203.0.113.7')

    def test_home_assistant_down_is_unknown_not_wrong(self):
        main.HA_URL = 'http://127.0.0.1:9'
        self.assertIsNone(main.check_proxy())


class FakeDNSalsa(BaseHTTPRequestHandler):
    polls = 0
    start_body = None
    service_body = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        if self.path == '/api/pair/service':
            FakeDNSalsa.service_body = body
            out = {'changed': True, 'service': f"{'https' if body['ssl'] else 'http'}://homeassistant:{body['port']}"}
        elif self.path == '/api/pair/start':
            FakeDNSalsa.start_body = body
            out = {'code': 'K7QP-4XM2', 'secret': 's' * 32, 'expiresAt': (time.time() + 900) * 1000}
        elif self.path == '/api/pair/poll':
            assert body.get('secret') == 's' * 32
            FakeDNSalsa.polls += 1
            out = {'status': 'waiting'} if FakeDNSalsa.polls < 2 else \
                {'status': 'paired', 'token': 'tok-123', 'hostname': 'jansen.dnsalsa.nl', 'url': 'https://jansen.dnsalsa.nl',
                 'service': 'http://homeassistant:80'}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(data)


class Pairing(unittest.TestCase):
    def test_code_then_token_saved_privately(self):
        url, srv = serve(FakeDNSalsa)
        with open(main.OPTIONS_FILE, 'w') as f:
            json.dump({'server': url}, f)
        real_time = main.time
        main.time = types.SimpleNamespace(time=real_time.time, sleep=lambda s: real_time.sleep(0.02))
        real_core = main.core_info
        main.core_info = lambda: {'version': '2026.10.3', 'arch': 'aarch64', 'port': 80, 'ssl': False}
        try:
            main.STATE.forget()
            threading.Thread(target=main.pairing_loop, daemon=True).start()
            for _ in range(200):
                if main.STATE.saved:
                    break
                real_time.sleep(0.02)
            self.assertEqual(main.STATE.saved, {'mode': 'tunnel', 'token': 'tok-123', 'hostname': 'jansen.dnsalsa.nl',
                                                'url': 'https://jansen.dnsalsa.nl', 'service': 'http://homeassistant:80'})
            self.assertIs(FakeDNSalsa.start_body['ddns'], True, 'DNSalsa may offer dynamic DNS to this version')
            self.assertEqual(FakeDNSalsa.start_body['ha'], {'port': 80, 'ssl': False}, 'DNSalsa learns where Home Assistant listens')
            self.assertEqual(FakeDNSalsa.start_body['device'], 'Home Assistant 2026.10.3 (aarch64)')
            self.assertIsNone(main.STATE.code, 'the code disappears once paired')
            mode = stat.S_IMODE(os.stat(main.STATE_FILE).st_mode)
            self.assertEqual(mode, 0o600, 'the tunnel token is only readable by the app')
            self.assertEqual(main.State()._load()['token'], 'tok-123', 'survives a restart')
        finally:
            main.time = real_time
            main.core_info = real_core
            with main.STATE.lock:
                main.STATE.ha = None
            srv.shutdown()
            srv.server_close()
            main.STATE.forget()


class WhereHomeAssistantListens(unittest.TestCase):
    def tearDown(self):
        main.STATE.forget()
        with main.STATE.lock:
            main.STATE.ha = None
            main.STATE.ha_reachable = main.STATE.public_ok = main.STATE.route_error = None
            main.STATE.connected = False

    def test_sidebar_and_watchdog_on_once(self):
        calls = []

        class FakeSupervisor(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                calls.append((self.path, self.headers['Authorization'], json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"result":"ok","data":{}}')

        url, srv = serve(FakeSupervisor)
        old = main.SUPERVISOR, os.environ.get('SUPERVISOR_TOKEN')
        main.SUPERVISOR, os.environ['SUPERVISOR_TOKEN'] = url, 'sup-token'
        try:
            os.remove(main.PREFS_FILE)
        except OSError:
            pass
        try:
            self.assertTrue(main.apply_defaults())
            self.assertEqual(calls, [('/addons/self/options', 'Bearer sup-token', {'ingress_panel': True, 'watchdog': True})])
            self.assertTrue(main.apply_defaults())
            self.assertEqual(len(calls), 1, 'only once, so turning them off later sticks')
        finally:
            srv.shutdown()
            srv.server_close()
            main.SUPERVISOR = old[0]
            if old[1] is None:
                os.environ.pop('SUPERVISOR_TOKEN', None)
            else:
                os.environ['SUPERVISOR_TOKEN'] = old[1]
            os.remove(main.PREFS_FILE)

    def test_port_and_ssl_from_the_supervisor(self):
        self.assertEqual(main.refresh_ha({'port': 80, 'ssl': False})['url'], 'http://homeassistant:80')
        self.assertEqual(main.ha_url(), 'http://homeassistant:80')
        self.assertEqual(main.refresh_ha({'port': 8123, 'ssl': True})['url'], 'https://homeassistant:8123')
        self.assertIsNone(main.refresh_ha({'port': 'x'}), 'odd answers are ignored')
        self.assertEqual(main.ha_url(), 'https://homeassistant:8123', 'and the last good one stays')

    def test_no_answer_means_unreachable(self):
        main.HA_URL = 'http://127.0.0.1:9'
        self.assertIsNone(main.check_proxy())
        self.assertIs(main.STATE.ha_reachable, False)

    def test_route_fixed_when_home_assistant_moved(self):
        url, srv = serve(FakeDNSalsa)
        with open(main.OPTIONS_FILE, 'w') as f:
            json.dump({'server': url}, f)
        try:
            main.STATE.save({'token': 'tok-123', 'hostname': 'jansen.dnsalsa.nl', 'url': 'https://jansen.dnsalsa.nl',
                             'service': 'http://homeassistant:8123'})
            main.refresh_ha({'port': 80, 'ssl': False})
            main.sync_route([0.0])
            self.assertEqual(FakeDNSalsa.service_body, {'hostname': 'jansen.dnsalsa.nl', 'token': 'tok-123', 'port': 80, 'ssl': False})
            self.assertEqual(main.STATE.saved['service'], 'http://homeassistant:80')
            FakeDNSalsa.service_body = None
            main.sync_route([0.0])
            self.assertIsNone(FakeDNSalsa.service_body, 'nothing to do once they match')
        finally:
            srv.shutdown()
            srv.server_close()

    def test_bad_gateway_is_shown_not_hidden(self):
        class Cloudflare502(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(502)
                self.end_headers()
        url, srv = serve(Cloudflare502)
        try:
            main.STATE.save({'token': 't', 'hostname': 'jansen.dnsalsa.nl', 'url': url, 'service': 'http://homeassistant:80'})
            with main.STATE.lock:
                main.STATE.connected = True
                main.STATE.ha_reachable = True
            self.assertIs(main.check_public(), False)
            page = main.render()
            self.assertIn('Not reachable', page)
            self.assertNotIn('>Connected<', page)
            self.assertIn("doesn't reach Home Assistant", page)
        finally:
            srv.shutdown()
            srv.server_close()


class FakeUpdate(BaseHTTPRequestHandler):
    """DNSalsa's /api/ip and /api/update."""
    my_ip = '84.26.113.7'
    calls = []
    status = 200
    extra = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        data = (FakeUpdate.my_ip + '\n').encode()
        self.send_response(200 if self.path == '/api/ip' else 404)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        FakeUpdate.calls.append((self.path, self.headers.get('Authorization'), body))
        v4 = None if body.get('ipv4') in (None, 'none') else body['ipv4']
        out = {'hostname': 'jansen.dnsalsa.nl', 'type': 'both', 'ipv4': v4, 'ipv6': None, 'changed': True, **FakeUpdate.extra}
        if FakeUpdate.status != 200:
            out = {'error': 'x', 'message': 'nope'}
        data = json.dumps(out).encode()
        self.send_response(FakeUpdate.status)
        self.end_headers()
        self.wfile.write(data)


class DynamicDns(unittest.TestCase):
    """Without a tunnel the app looks up its addresses per family and sends them in one update call.
    The test server listens on 127.0.0.1 only, so the IPv6 lookup finds nothing, like a home without IPv6."""

    def setUp(self):
        self.url, self.srv = serve(FakeUpdate)
        with open(main.OPTIONS_FILE, 'w') as f:
            json.dump({'server': self.url}, f)
        try:
            os.remove(main.PREFS_FILE)
        except OSError:
            pass
        FakeUpdate.calls, FakeUpdate.status, FakeUpdate.extra, FakeUpdate.my_ip = [], 200, {}, '84.26.113.7'
        main.STATE.forget()
        main.STATE.save({'mode': 'ddns', 'token': 'dsa_tok', 'hostname': 'jansen.dnsalsa.nl', 'url': 'https://jansen.dnsalsa.nl'})

    def tearDown(self):
        main.STATE.forget()
        os.remove(main.OPTIONS_FILE)
        self.srv.shutdown()
        self.srv.server_close()

    def test_sends_only_the_addresses_it_found(self):
        wait = main.ddns_update()
        path, auth, body = FakeUpdate.calls[-1]
        self.assertEqual((path, auth), ('/api/update', 'Bearer dsa_tok'))
        self.assertEqual(body, {'type': 'both', 'ipv4': '84.26.113.7'}, 'no IPv6 found: not sent at all')
        self.assertEqual(main.STATE.ddns.records['ipv4'], '84.26.113.7')
        self.assertIsNone(main.STATE.ddns.error)
        self.assertGreater(wait, 300, 'default 5 minutes, plus a little')

    def test_an_address_gone_three_times_in_a_row_is_removed(self):
        for _ in range(main.MISSES_BEFORE_REMOVE - 1):
            main.ddns_update()
            self.assertNotIn('ipv6', FakeUpdate.calls[-1][2])
        main.ddns_update()
        self.assertEqual(FakeUpdate.calls[-1][2]['ipv6'], 'none')

    def test_switched_off_means_removed_right_away(self):
        main.save_settings('ipv6=on&interval=60')
        self.assertEqual(main.ddns_settings(), {'ipv4': False, 'ipv6': True, 'interval': 60})
        wait = main.ddns_update()
        self.assertEqual(FakeUpdate.calls[-1][2]['ipv4'], 'none')
        self.assertLess(wait, 70)

    def test_odd_interval_falls_back(self):
        main.save_settings('ipv4=on&ipv6=on&interval=7')
        self.assertEqual(main.ddns_settings()['interval'], main.DEFAULT_INTERVAL)

    def test_private_address_is_not_sent(self):
        FakeUpdate.my_ip = '192.168.1.20'
        self.assertIsNone(main.lookup_ip(self.url, main.socket.AF_INET))
        FakeUpdate.my_ip = 'garbage'
        self.assertIsNone(main.lookup_ip(self.url, main.socket.AF_INET))
        FakeUpdate.my_ip = '84.26.113.7'
        self.assertEqual(main.lookup_ip(self.url, main.socket.AF_INET), '84.26.113.7')

    def test_nothing_found_means_no_call(self):
        with open(main.OPTIONS_FILE, 'w') as f:   # nothing listens there: offline
            json.dump({'server': 'http://127.0.0.1:9'}, f)
        main.ddns_update()
        self.assertEqual(FakeUpdate.calls, [])
        self.assertIn('Could not find your address', main.STATE.ddns.error)

    def test_rejected_token_asks_to_pair_again(self):
        FakeUpdate.status = 401
        main.ddns_update()
        self.assertIn('Pair again', main.STATE.ddns.error)

    def test_free_account_hears_ipv6_comes_with_remote(self):
        FakeUpdate.extra = {'ipv6Skipped': 'remote_only'}
        main.ddns_update()
        self.assertTrue(main.STATE.ddns.ipv6_remote)
        self.assertIn('IPv6 comes with Remote', main.render())

    def test_page_has_switches_and_no_tunnel_steps(self):
        main.ddns_update()
        page = main.render()
        self.assertIn('name="ipv4" checked', page)
        self.assertIn('name="ipv6" checked', page)
        self.assertIn('<option value="300" selected>', page)
        self.assertIn('84.26.113.7', page)
        self.assertNotIn('Trust X-Forwarded-For', page)
        self.assertIn('port forward', page)

    def test_tunnel_loop_leaves_a_ddns_pairing_alone(self):
        self.assertEqual(main.mode(main.STATE.saved), 'ddns')
        self.assertEqual(main.mode({'token': 'old'}), 'tunnel', 'pairings from before 1.3.0 are tunnels')


class Page(unittest.TestCase):
    def tearDown(self):
        main.STATE.forget()
        with main.STATE.lock:
            main.STATE.code = None
            main.STATE.proxy_ok = None

    def test_pairing_code_and_escaping(self):
        with main.STATE.lock:
            main.STATE.saved = None
            main.STATE.code, main.STATE.code_expires = 'K7QP-4XM2', time.time() + 600
            main.STATE.error = '<script>alert(1)</script>'
        page = main.render()
        self.assertIn('K7QP-4XM2', page)
        self.assertIn('Not paired', page)
        self.assertNotIn('<script>alert(1)', page)
        self.assertIn('&lt;script&gt;', page)

    def test_proxy_steps_shown_until_it_works(self):
        with main.STATE.lock:
            main.STATE.saved = {'token': 't', 'hostname': 'jansen.dnsalsa.nl', 'url': 'https://jansen.dnsalsa.nl'}
            main.STATE.connected, main.STATE.proxy_ok = True, False
        page = main.render()
        self.assertIn('172.30.33.0/24', page)
        self.assertIn('Trust X-Forwarded-For', page)
        self.assertIn('href="/config/network"', page)
        self.assertIn('Login attempts before ban</b> to <b>10', page)
        with main.STATE.lock:
            main.STATE.proxy_ok = True
        page = main.render()
        self.assertNotIn('172.30.33.0/24', page)
        self.assertIn('Connected', page)
        self.assertIn('Enable IP banning', page, 'IP ban tip stays once the proxy works')
        self.assertIn('data-post="hide-ban-tip"', page)
        self.assertNotIn('<main data-refresh>', page, 'no auto-refresh once everything works')

    def test_ban_tip_can_be_hidden_for_good(self):
        try:
            os.remove(main.PREFS_FILE)
        except OSError:
            pass
        with main.STATE.lock:
            main.STATE.saved = {'token': 't', 'hostname': 'jansen.dnsalsa.nl', 'url': 'https://jansen.dnsalsa.nl'}
            main.STATE.connected, main.STATE.proxy_ok = True, True
        self.assertIn('Enable IP banning', main.render())
        url, srv = serve(main.Handler)
        try:
            import urllib.request
            urllib.request.urlopen(urllib.request.Request(f'{url}/hide-ban-tip', method='POST'), timeout=5).read()
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertNotIn('Enable IP banning', main.render())
        main.STATE.forget()   # "Pair again" keeps the choice
        with main.STATE.lock:
            main.STATE.saved = {'token': 't', 'hostname': 'jansen.dnsalsa.nl', 'url': 'https://jansen.dnsalsa.nl'}
            main.STATE.proxy_ok = False
        self.assertNotIn('Enable IP banning', main.render(), 'not in the setup steps either')
        os.remove(main.PREFS_FILE)

    def test_fonts_are_served_with_a_long_cache(self):
        self.assertNotIn('data:font', main.PAGE, 'fonts are not inlined in every page')
        self.assertIn('url(fonts/space-grotesk-latin-wght-normal.woff2)', main.PAGE, 'relative, so it works under the ingress path')
        url, srv = serve(main.Handler)
        try:
            import urllib.request
            with urllib.request.urlopen(f'{url}/fonts/space-mono-latin-700-normal.woff2', timeout=5) as r:
                self.assertEqual(r.headers['Content-Type'], 'font/woff2')
                self.assertIn('immutable', r.headers['Cache-Control'])
                self.assertEqual(r.read(), main.FONTS['space-mono-latin-700-normal.woff2'])
            with urllib.request.urlopen(f'{url}/', timeout=5) as r:
                self.assertEqual(r.headers['Cache-Control'], 'no-store')
                self.assertLess(len(r.read()), 20000)
        finally:
            srv.shutdown()
            srv.server_close()


class Ingress(unittest.TestCase):
    def test_only_home_assistant_ingress_may_talk_to_the_app(self):
        sent = []
        stub = types.SimpleNamespace(client_address=('192.168.1.50', 1234), send_error=lambda code: sent.append(code))
        self.assertFalse(main.Handler._allowed(stub))
        self.assertEqual(sent, [403])
        stub.client_address = ('172.30.32.2', 1234)
        self.assertTrue(main.Handler._allowed(stub))


class Release(unittest.TestCase):
    """Checks that keep a release consistent; the cloudflared workflow relies on these formats."""

    def read(self, *p):
        with open(os.path.join(ROOT, *p)) as f:
            return f.read()

    def test_version_has_a_changelog_entry(self):
        version = re.search(r'^version: "([\d.]+)"', self.read('dnsalsa', 'config.yaml'), re.M).group(1)
        first = re.search(r'^## ([\d.]+)', self.read('dnsalsa', 'CHANGELOG.md'), re.M).group(1)
        self.assertEqual(version, first, 'raise config.yaml version and add a CHANGELOG entry together')

    def test_cloudflared_pinned_with_checksums(self):
        self.assertRegex(self.read('dnsalsa', 'build.yaml'), r'(?m)^  CLOUDFLARED_VERSION: \d{4}\.\d+\.\d+$')
        dockerfile = self.read('dnsalsa', 'Dockerfile')
        self.assertRegex(dockerfile, r'amd64\)   ARCH=amd64; SUM=[0-9a-f]{64} ;;')
        self.assertRegex(dockerfile, r'aarch64\) ARCH=arm64; SUM=[0-9a-f]{64} ;;')
        self.assertIn('sha256sum -c', dockerfile)

    def test_ingress_port_matches(self):
        cfg = self.read('dnsalsa', 'config.yaml')
        port = int(re.search(r'^ingress_port: (\d+)', cfg, re.M).group(1))
        self.assertEqual(port, main.INGRESS_PORT)
        self.assertIn(f'[PORT:{port}]', cfg, 'the watchdog checks the same port')


if __name__ == '__main__':
    unittest.main()
