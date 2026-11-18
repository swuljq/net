import json
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import requests

from campus_net.auth import Authenticator
from campus_net.config import Config, cqu_endpoint, load_config, uses_cqu
from campus_net.cqu_auth import CquAuthenticator, parse_response
from campus_net.connectivity import LinkState, ProbeResult
from campus_net.controller import Controller
from campus_net.network import NetworkError, Snapshot


class CquPortal(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        params = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        self.server.calls.append((parsed.path, params, self.headers.get("Referer")))
        body = self.server.body
        if body is None:
            body = f"{params['callback']}({json.dumps(self.server.payload)});"
        body = body.encode("utf-8")
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class CquTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), CquPortal)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.calls = []
        self.server.status = 200
        self.server.payload = {"result": 1, "msg": "ok"}
        self.server.body = None
        self.network = Mock()
        self.network.ipv4_address.return_value = "192.0.2.20"
        self.config = Config(auth_mode="cqu_drcom", login_url=self.base, username="test-student")
        self.adapter = CquAuthenticator(self.network)

    def test_real_get_request_matches_capture_without_hardcoded_identity(self):
        result = self.adapter.authenticate(self.config, "test-only-password", "wlan-test")
        self.assertTrue(result.submitted)
        path, params, referer = self.server.calls[-1]
        self.assertEqual(path, "/eportal/portal/login")
        self.assertEqual(params["user_account"], ",0,test-student")
        self.assertEqual(params["user_password"], "test-only-password")
        self.assertEqual(params["wlan_user_ip"], "192.0.2.20")
        self.assertEqual(params["wlan_user_ipv6"], "")
        self.assertEqual(params["wlan_user_mac"], "000000000000")
        self.assertEqual(params["login_method"], "1")
        self.assertEqual(params["terminal_type"], "1")
        self.assertEqual(params["jsVersion"], "4.2.2")
        self.assertRegex(params["callback"], r"^dr\d+$")
        self.assertEqual(referer, self.base + "/")
        self.network.ipv4_address.assert_called_once_with("wlan-test")

    def test_ipv4_is_read_again_after_dhcp_changes(self):
        self.network.ipv4_address.side_effect = ["192.0.2.20", "192.0.2.99"]
        self.adapter.authenticate(self.config, "test-only-password", "wlan-test")
        self.adapter.authenticate(self.config, "test-only-password", "wlan-test")
        self.assertEqual([call[1]["wlan_user_ip"] for call in self.server.calls], ["192.0.2.20", "192.0.2.99"])

    def test_no_ipv4_or_password_does_not_submit(self):
        self.network.ipv4_address.side_effect = NetworkError("尚未取得 IPv4 地址")
        self.assertFalse(self.adapter.authenticate(self.config, "test-only-password").submitted)
        self.assertFalse(self.adapter.authenticate(self.config, "").submitted)
        self.assertEqual(self.server.calls, [])

    def test_prefix_is_configurable_and_not_duplicated(self):
        for username, prefix, expected in (
            (",0,test-student", ",0,", ",0,test-student"),
            ("test-student", "", "test-student"),
            ("test-student", ",1,", ",1,test-student"),
        ):
            with self.subTest(prefix=prefix):
                self.adapter.authenticate(replace(self.config, username=username, account_prefix=prefix), "test-only-password")
                self.assertEqual(self.server.calls[-1][1]["user_account"], expected)

    def test_protocol_failure_never_echoes_server_message(self):
        self.server.payload = {"result": 0, "msg": "error: test-only-password"}
        result = self.adapter.authenticate(self.config, "test-only-password")
        self.assertFalse(result.submitted)
        self.assertNotIn("test-only-password", result.message)

    def test_already_online_is_submitted_but_requires_independent_verification(self):
        self.server.payload = {"result": 0, "msg": "already online"}
        result = self.adapter.authenticate(self.config, "test-only-password")
        self.assertTrue(result.submitted)
        self.assertIn("复查", result.message)

    def test_bad_jsonp_and_http_error_are_not_success(self):
        for body, status in (("unrelated({});", 200), ("<html>login</html>", 200), ("{}", 403), ("{}", 302)):
            with self.subTest(status=status, body=body):
                self.server.body, self.server.status = body, status
                self.assertFalse(self.adapter.authenticate(self.config, "test-only-password").submitted)

    def test_static_overrides_supported_but_dynamic_parameters_cannot_be_saved(self):
        config = replace(self.config, extra_params={"wlan_ac_name": "test-ac", "login_method": "1"})
        self.assertTrue(self.adapter.authenticate(config, "test-only-password").submitted)
        self.assertEqual(self.server.calls[-1][1]["wlan_ac_name"], "test-ac")
        for key in ("user_account", "user_password", "wlan_user_ip", "callback"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                replace(self.config, extra_params={key: "value"}).validate()

    def test_authenticator_dispatches_and_controller_passes_actual_adapter(self):
        snapshot = Snapshot("wlan-test", "cqu-wifi", "", frozenset({"cqu-wifi"}))
        self.network.snapshot.return_value = snapshot
        checker = Mock(side_effect=[ProbeResult(LinkState.PORTAL, "门户"), ProbeResult(LinkState.ONLINE, "正常")])
        controller = Controller(self.config, self.network, lambda _: "test-only-password", probe_fn=checker)
        controller.tick()
        self.assertEqual(controller.status.phase, "已联网")
        self.network.ipv4_address.assert_called_with("wlan-test")
        self.assertEqual(len(self.server.calls), 1)

    def test_positive_drcom_response_without_online_probe_is_not_complete(self):
        snapshot = Snapshot("wlan-test", "cqu-wifi", "", frozenset({"cqu-wifi"}))
        self.network.snapshot.return_value = snapshot
        checker = Mock(return_value=ProbeResult(LinkState.PORTAL, "门户"))
        controller = Controller(self.config, self.network, lambda _: "test-only-password", probe_fn=checker)
        controller.tick()
        self.assertEqual(controller.status.phase, "认证未成功")

    def test_transport_error_does_not_expose_url_or_password(self):
        with patch("requests.Session.get", side_effect=requests.exceptions.SSLError("user_password=test-only-password")):
            result = self.adapter.authenticate(self.config, "test-only-password")
        self.assertFalse(result.submitted)
        self.assertNotIn("test-only-password", result.message)


class CquConfigTests(unittest.TestCase):
    def test_auto_detection_and_endpoint(self):
        config = Config(login_url="https://login.cqu.edu.cn/", username="test-student")
        self.assertTrue(uses_cqu(config))
        self.assertFalse(uses_cqu(replace(config, auth_mode="form")))
        self.assertFalse(uses_cqu(replace(config, login_url="https://portal.example/")))
        self.assertEqual(cqu_endpoint(config.login_url), "https://login.cqu.edu.cn:802/eportal/portal/login")
        self.assertEqual(cqu_endpoint("http://login.cqu.edu.cn/"), "http://login.cqu.edu.cn:801/eportal/portal/login")
        self.assertEqual(cqu_endpoint("https://login.cqu.edu.cn:802/eportal/portal/login"), "https://login.cqu.edu.cn:802/eportal/portal/login")

    def test_capture_url_with_query_is_rejected_before_persistence(self):
        with self.assertRaises(ValueError):
            Config(login_url="https://login.cqu.edu.cn:802/eportal/portal/login?user_password=test-only-password").validate()

    def test_old_config_without_mode_loads_and_detects_cqu(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"login_url": "https://login.cqu.edu.cn/", "username": "test-student"}))
            self.assertTrue(uses_cqu(load_config(path)))

    def test_jsonp_parsing_does_not_execute_javascript(self):
        self.assertEqual(parse_response('/* JSONP */ dr1001({"result":1});', "dr1001"), {"result": 1})
        self.assertEqual(parse_response('{"result":"1"}', "dr1001"), {"result": "1"})
        for body in ('dr1001({"result":1}); alert(1)', 'other({"result":1})', 'dr1001([1])', 'dr1001(not_json)'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                parse_response(body, "dr1001")
