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

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        if self.path == '/api/pair/start':
            out = {'code': 'K7QP-4XM2', 'secret': 's' * 32, 'expiresAt': (time.time() + 900) * 1000}
        elif self.path == '/api/pair/poll':
            assert body.get('secret') == 's' * 32
            FakeDNSalsa.polls += 1
            out = {'status': 'waiting'} if FakeDNSalsa.polls < 2 else \
                {'status': 'paired', 'token': 'tok-123', 'hostname': 'jansen.dnsalsa.nl', 'url': 'https://jansen.dnsalsa.nl'}
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
        try:
            main.STATE.forget()
            threading.Thread(target=main.pairing_loop, daemon=True).start()
            for _ in range(200):
                if main.STATE.saved:
                    break
                real_time.sleep(0.02)
            self.assertEqual(main.STATE.saved, {'token': 'tok-123', 'hostname': 'jansen.dnsalsa.nl', 'url': 'https://jansen.dnsalsa.nl'})
            self.assertIsNone(main.STATE.code, 'the code disappears once paired')
            mode = stat.S_IMODE(os.stat(main.STATE_FILE).st_mode)
            self.assertEqual(mode, 0o600, 'the tunnel token is only readable by the app')
            self.assertEqual(main.State()._load()['token'], 'tok-123', 'survives a restart')
        finally:
            main.time = real_time
            srv.shutdown()
            srv.server_close()
            main.STATE.forget()


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
        self.assertNotIn('setTimeout(()=>location.reload()', page, 'no auto-refresh once everything works')

    def test_fonts_are_embedded(self):
        self.assertIn('data:font/woff2;base64,', main.PAGE, 'the page has no external requests')


class Ingress(unittest.TestCase):
    def test_only_home_assistant_ingress_may_talk_to_the_app(self):
        sent = []
        stub = types.SimpleNamespace(client_address=('192.168.1.50', 1234), send_error=lambda code: sent.append(code))
        self.assertFalse(main.Handler._allowed(stub))
        self.assertEqual(sent, [403])
        stub.client_address = ('172.30.32.2', 1234)
        self.assertTrue(main.Handler._allowed(stub))

    def test_standalone_page_open_on_its_published_port(self):
        stub = types.SimpleNamespace(client_address=('192.168.1.50', 1234), send_error=lambda code: None)
        main.STANDALONE = True
        try:
            self.assertTrue(main.Handler._allowed(stub))
        finally:
            main.STANDALONE = False


class Standalone(unittest.TestCase):
    def setUp(self):
        main.STANDALONE, main.TRUSTED_PROXY = True, '172.30.57.0/24'
        with main.STATE.lock:
            main.STATE.saved = {'token': 't', 'hostname': 'jansen.dnsalsa.nl', 'url': 'https://jansen.dnsalsa.nl'}
            main.STATE.connected, main.STATE.proxy_ok, main.STATE.error = True, False, None

    def tearDown(self):
        main.STANDALONE, main.TRUSTED_PROXY = False, '172.30.33.0/24'
        with main.STATE.lock:
            main.STATE.saved = None

    def test_steps_name_the_docker_network_and_no_ingress_link(self):
        page = main.render()
        self.assertIn('172.30.57.0/24', page)
        self.assertNotIn('172.30.33.0/24', page)
        self.assertNotIn('href="/config/network"', page, 'no Home Assistant around this page to link into')
        self.assertIn('Settings → System → Network', page)


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

    def test_standalone_dockerfile_matches_the_app(self):
        version = re.search(r'(?m)^  CLOUDFLARED_VERSION: (\S+)$', self.read('dnsalsa', 'build.yaml')).group(1)
        app, docker = self.read('dnsalsa', 'Dockerfile'), self.read('docker', 'Dockerfile')
        self.assertIn(f'ARG CLOUDFLARED_VERSION={version}\n', docker)
        for arch in ('amd64', 'arm64'):
            sums = [re.search(rf'ARCH={arch}; SUM=([0-9a-f]{{64}})', d).group(1) for d in (app, docker)]
            self.assertEqual(sums[0], sums[1], f'{arch} checksum differs between the two Dockerfiles')
        self.assertIn('sha256sum -c', docker)
        compose = self.read('docker', 'compose.yaml')
        net = re.search(r'DNSALSA_TRUSTED_PROXY: (\S+)', compose).group(1)
        self.assertIn(f'subnet: {net}', compose, 'the trusted proxy shown to the user is the compose network')

    def test_ingress_port_matches(self):
        cfg = self.read('dnsalsa', 'config.yaml')
        port = int(re.search(r'^ingress_port: (\d+)', cfg, re.M).group(1))
        self.assertEqual(port, main.INGRESS_PORT)
        self.assertIn(f'[PORT:{port}]', cfg, 'the watchdog checks the same port')


if __name__ == '__main__':
    unittest.main()
