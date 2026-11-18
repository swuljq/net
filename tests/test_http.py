import json
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from campus_net.auth import FormAuthenticator
from campus_net.config import Config
from campus_net.connectivity import LinkState, probe


class PortalHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, body=b"", headers=None):
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path)
        if path.path == "/preflight":
            self.reply(200, b"login page", {"Set-Cookie": "campus=session"})
        elif path.path == "/check":
            if self.server.state.get("logged_in"):
                self.reply(200, b"ONLINE")
            else:
                self.reply(302, headers={"Location": "/page"})
        elif path.path == "/login":
            self.server.state["params"] = {k: v[0] for k, v in parse_qs(path.query).items()}
            self.reply(200, b"submitted")
        elif path.path == "/page":
            self.reply(200, b'<form><input type="password"></form>')
        elif path.path == "/unrelated":
            self.reply(200, b"maintenance")
        elif path.path == "/empty":
            self.reply(204)
        elif path.path == "/drcom":
            self.reply(200, b'<!--Dr.COMWebLoginID_0.htm--><script src="a41.js"></script>')
        else:
            self.reply(500)

    def do_POST(self):
        data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.headers.get("Content-Type", "").startswith("application/json"):
            params = json.loads(data)
        else:
            params = {k: v[0] for k, v in parse_qs(data.decode()).items()}
        self.server.state["params"] = params
        self.server.state["cookie"] = self.headers.get("Cookie", "")
        if self.path == "/redirect":
            self.reply(307, headers={"Location": "/leak"})
        elif self.path == "/leak":
            self.server.state["leaked"] = True
            self.reply(200)
        elif self.path == "/login":
            if params.get("user") == "student" and params.get("pass") == "test-password":
                self.server.state["logged_in"] = True
            self.reply(200, b"submitted")
        elif self.path == "/captcha":
            self.reply(200, b"captcha required")
        else:
            self.reply(403)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), PortalHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.state = {}
        self.config = Config(
            login_url=self.base + "/login", username="student", username_field="user", password_field="pass",
            probe_url=self.base + "/check", probe_status=200, probe_expected="ONLINE", extra_params={"action": "login"},
            preflight_url=self.base + "/preflight",
        )

    def test_real_form_login_preserves_cookie_and_verifies_connection(self):
        self.assertEqual(probe(self.config).state, LinkState.PORTAL)
        result = FormAuthenticator().authenticate(self.config, "test-password")
        self.assertTrue(result.submitted)
        self.assertEqual(self.server.state["cookie"], "campus=session")
        self.assertEqual(self.server.state["params"]["action"], "login")
        self.assertEqual(probe(self.config).state, LinkState.ONLINE)

    def test_json_and_get_field_mapping(self):
        for method in ("POST_JSON", "GET"):
            with self.subTest(method=method):
                result = FormAuthenticator().authenticate(replace(self.config, method=method), "test-password")
                self.assertTrue(result.submitted)
                self.assertEqual(self.server.state["params"]["user"], "student")
                self.assertEqual(self.server.state["params"]["pass"], "test-password")

    def test_bad_password_http_200_is_not_verified_online(self):
        self.assertTrue(FormAuthenticator().authenticate(self.config, "wrong-password").submitted)
        self.assertEqual(probe(self.config).state, LinkState.PORTAL)

    def test_credentials_are_not_forwarded_on_307(self):
        FormAuthenticator().authenticate(replace(self.config, login_url=self.base + "/redirect"), "test-password")
        self.assertNotIn("leaked", self.server.state)

    def test_server_errors_and_captcha_report_failure(self):
        for path in ("/forbidden", "/captcha"):
            self.assertFalse(FormAuthenticator().authenticate(replace(self.config, login_url=self.base + path), "test-password").submitted)

    def test_probe_distinguishes_portal_from_unrelated_response(self):
        self.assertEqual(probe(replace(self.config, probe_url=self.base + "/page")).state, LinkState.PORTAL)
        self.assertEqual(probe(replace(self.config, probe_url=self.base + "/unrelated")).state, LinkState.UNKNOWN)
        self.assertEqual(probe(replace(self.config, probe_url=self.base + "/empty", probe_status=204, probe_expected="")).state, LinkState.ONLINE)
        self.assertEqual(probe(replace(self.config, probe_url=self.base + "/drcom")).state, LinkState.PORTAL)

    def test_connection_failure_does_not_expose_credentials_or_url(self):
        # 已绑定但不监听的 TCP 端口，立即拒绝连接。
        import socket
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{reserved.getsockname()[1]}/?token=private"
            result = FormAuthenticator().authenticate(replace(self.config, login_url=url, preflight_url=""), "private-password")
        self.assertFalse(result.submitted)
        self.assertNotIn("private", result.message)
