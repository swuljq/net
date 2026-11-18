import unittest
from dataclasses import replace
from unittest.mock import Mock

from campus_net.auth import AuthResult
from campus_net.config import Config
from campus_net.connectivity import LinkState, ProbeResult
from campus_net.controller import Controller
from campus_net.network import NetworkError, Snapshot

ONLINE = ProbeResult(LinkState.ONLINE, "正常")
PORTAL = ProbeResult(LinkState.PORTAL, "需要认证")
UNKNOWN = ProbeResult(LinkState.UNKNOWN, "超时")


class FakeNetwork:
    def __init__(self, visible=("ljq", "cqu-wifi"), ssid="cqu-wifi"):
        self.visible = frozenset(visible)
        self.ssid = ssid
        self.calls = []
        self.failure = False

    def snapshot(self, interface=""):
        return Snapshot(interface or "wlan0", self.ssid, "active", self.visible)

    def connect(self, ssid, profile_uuid, interface):
        self.calls.append(ssid)
        if self.failure and ssid == "ljq":
            raise NetworkError("连接失败")
        self.ssid = ssid


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.time = 0
        self.network = FakeNetwork()
        self.auth = Mock()
        self.auth.authenticate.return_value = AuthResult(True, "已提交")
        self.probe = Mock(return_value=ONLINE)
        self.config = Config(login_url="http://portal.example/login", username="student")
        self.controller = Controller(self.config, self.network, lambda _: "test-password",
                                     authenticator=self.auth, probe_fn=self.probe, clock=lambda: self.time)

    def test_preferred_requires_stability_then_switches(self):
        self.controller.tick()
        self.assertEqual(self.network.calls, [])
        self.time = 15
        self.controller.tick()
        self.assertEqual(self.network.calls, ["ljq"])
        self.assertEqual(self.controller.status.phase, "已联网")

    def test_existing_preferred_does_not_wait_or_reconnect(self):
        self.network.ssid = "ljq"
        self.controller.tick()
        self.assertEqual(self.network.calls, [])
        self.assertEqual(self.controller.status.ssid, "ljq")

    def test_fallback_when_preferred_disappears(self):
        self.network.ssid = "other"
        self.network.visible = frozenset({"cqu-wifi"})
        self.controller.tick()
        self.assertEqual(self.network.calls, ["cqu-wifi"])

    def test_stability_resets_after_disappearance(self):
        self.controller.tick()
        self.time = 10
        self.network.visible = frozenset({"cqu-wifi"})
        self.controller.tick()
        self.time = 20
        self.network.visible = frozenset({"ljq", "cqu-wifi"})
        self.controller.tick()
        self.assertEqual(self.network.calls, [])
        self.time = 35
        self.controller.tick()
        self.assertEqual(self.network.calls, ["ljq"])

    def test_no_network_keeps_existing_connection(self):
        self.network.ssid = "unrelated"
        self.network.visible = frozenset()
        self.controller.tick()
        self.assertEqual(self.network.calls, [])
        self.assertEqual(self.network.ssid, "unrelated")

    def test_scan_error_is_not_empty_scan_and_does_not_connect(self):
        self.network.snapshot = Mock(side_effect=NetworkError("扫描失败"))
        self.controller.tick()
        self.assertEqual(self.network.calls, [])
        self.assertEqual(self.controller.status.phase, "网络操作未完成")

    def test_pending_system_connection_does_not_resubmit_or_authenticate(self):
        self.network.snapshot = Mock(return_value=Snapshot("wlan0", "cqu-wifi", "other-profile", frozenset(), device_state=70))
        self.controller.tick()
        self.controller.tick()
        self.assertEqual(self.network.calls, [])
        self.auth.authenticate.assert_not_called()
        self.probe.assert_not_called()
        self.assertEqual(self.controller.status.phase, "等待系统连接")
        self.assertIn("网络地址", self.controller.status.detail)

    def test_repeated_connection_failure_cools_preferred_and_uses_fallback(self):
        self.controller.config = replace(self.config, stable_seconds=0)
        self.network.ssid = "other"
        self.network.failure = True
        self.controller.tick()
        self.time = 30
        self.controller.tick()
        self.time = 60
        self.controller.tick()
        self.assertEqual(self.network.calls, ["ljq", "ljq", "cqu-wifi"])
        self.time = 151
        self.controller.tick()
        self.assertEqual(self.network.calls[-1], "ljq")

    def test_online_campus_does_not_authenticate(self):
        self.network.visible = frozenset({"cqu-wifi"})
        self.controller.tick()
        self.auth.authenticate.assert_not_called()

    def test_authentication_expiry_reauthenticates_on_next_check(self):
        self.network.visible = frozenset({"cqu-wifi"})
        self.probe.side_effect = [ONLINE, PORTAL, ONLINE]
        self.controller.tick()
        self.auth.authenticate.assert_not_called()
        self.time = 30
        self.controller.tick()
        self.auth.authenticate.assert_called_once()
        self.assertEqual(self.controller.status.phase, "已联网")

    def test_portal_authenticates_and_verifies_connectivity(self):
        self.network.visible = frozenset({"cqu-wifi"})
        self.probe.side_effect = [PORTAL, ONLINE]
        self.controller.tick()
        self.auth.authenticate.assert_called_once()
        self.assertEqual(self.controller.status.phase, "已联网")

    def test_http_submission_without_connectivity_is_not_success_and_backs_off(self):
        self.network.visible = frozenset({"cqu-wifi"})
        self.probe.return_value = PORTAL
        self.controller.tick()
        self.assertEqual(self.controller.status.phase, "认证未成功")
        self.time = 10
        self.controller.tick()
        self.auth.authenticate.assert_called_once()
        self.time = 30
        self.controller.tick()
        self.assertEqual(self.auth.authenticate.call_count, 2)
        self.assertEqual(self.controller.auth_retry_at, 90)

    def test_unknown_probe_does_not_authenticate_or_switch_preferred(self):
        self.network.ssid = "ljq"
        self.probe.return_value = UNKNOWN
        self.controller.tick()
        self.assertEqual(self.network.calls, [])
        self.auth.authenticate.assert_not_called()
        self.network.ssid = "cqu-wifi"
        self.network.visible = frozenset({"cqu-wifi"})
        self.controller.tick()
        self.auth.authenticate.assert_not_called()

    def test_cancelled_scan_cannot_trigger_connection_or_authentication(self):
        original = self.network.snapshot
        def snapshot(_):
            self.controller.cancel.set()
            return original()
        self.network.snapshot = snapshot
        self.controller.tick()
        self.assertEqual(self.network.calls, [])
        self.auth.authenticate.assert_not_called()

    def test_manual_auth_requires_campus_wifi(self):
        self.network.ssid = "ljq"
        self.controller.tick(authenticate_only=True)
        self.auth.authenticate.assert_not_called()
        self.assertEqual(self.network.calls, [])


if __name__ == "__main__":
    unittest.main()
