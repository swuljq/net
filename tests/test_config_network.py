import json
import logging
import os
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from campus_net.autostart import set_autostart
from campus_net.config import Config, load_config, save_config
from campus_net.logging_utils import SafeFormatter
from campus_net.network import NetworkError, NetworkManager, Profile, split_escaped
from campus_net.secrets import SecretStore


class ConfigTests(unittest.TestCase):
    def test_atomic_private_roundtrip_without_password(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app/config.json"
            config = Config(username="student", login_url="http://portal.example/login")
            save_config(config, path)
            self.assertEqual(load_config(path), config)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotIn("password", json.loads(path.read_text()))
            save_config(replace(config, interval=60), path)
            self.assertEqual(load_config(path).interval, 60)
            self.assertEqual(list(path.parent.glob(".tmp-*")), [])

    def test_invalid_types_urls_and_parameters_are_rejected(self):
        cases = [
            {"interval": "30"}, {"interval": True}, {"interval": 0},
            {"login_url": "file:///tmp/login"}, {"login_url": "https://u:p@portal.example"},
            {"preferred_ssid": "cqu-wifi"}, {"extra_params": {"token": "secret"}},
            {"extra_params": {"action": 1}}, {"probe_expected": ""},
            {"preferred_uuid": "bad"}, {"interface": "wlan0\nextra"},
        ]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(Config(), **values).validate()

    def test_bad_file_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("broken")
            with self.assertRaises(ValueError):
                load_config(path)
            self.assertEqual(path.read_text(), "broken")

    def test_autostart_can_be_enabled_and_removed(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"XDG_CONFIG_HOME": directory}):
            path = Path(directory) / "autostart/campus-network-assistant.desktop"
            set_autostart(True)
            self.assertIn("--background", path.read_text())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            set_autostart(False)
            self.assertFalse(path.exists())


class NetworkTests(unittest.TestCase):
    def test_nmcli_escaping(self):
        self.assertEqual(split_escaped(r"a\:b:c\\d:last"), ["a:b", "c\\d", "last"])

    def test_matching_by_ssid_not_connection_name(self):
        network = NetworkManager()
        network.profiles = Mock(return_value=[Profile("arbitrary name", "profile-id", "ljq")])
        network.run = Mock(return_value="")
        network.connect("ljq", "", "wlan0")
        self.assertEqual(network.run.call_args.args[:4], ("connection", "up", "uuid", "profile-id"))

    def test_duplicate_ssids_and_wrong_explicit_profile_do_not_connect(self):
        network = NetworkManager()
        network.profiles = Mock(return_value=[Profile("one", "id1", "ljq"), Profile("two", "id2", "ljq")])
        network.run = Mock()
        with self.assertRaises(NetworkError):
            network.connect("ljq", "", "wlan0")
        network.profile = Mock(return_value=Profile("one", "id1", "other"))
        with self.assertRaises(NetworkError):
            network.connect("ljq", "id1", "wlan0")
        network.run.assert_not_called()

    def test_multiple_adapters_requires_selection(self):
        network = NetworkManager()
        network.run = Mock(return_value="enabled")
        network.interfaces = Mock(return_value=["wlan0", "wlan1"])
        with self.assertRaises(NetworkError):
            network.snapshot()

    def test_raw_nmcli_failure_is_not_exposed(self):
        import subprocess
        network = NetworkManager()
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 4, "private", "password=private")):
            with self.assertRaises(NetworkError) as caught:
                network.run("connection", "up", "uuid", "test")
        self.assertNotIn("private", str(caught.exception))

    def test_new_wifi_saves_over_dbus_without_connecting_or_subprocess_password(self):
        from gi.repository import Gio
        network = NetworkManager()
        network.run = Mock()
        bus = Mock()
        with patch.object(Gio, "bus_get_sync", return_value=bus):
            saved_uuid = network.create_profile("ljq", "wlan0", "test-password")
        args = bus.call_sync.call_args.args
        self.assertEqual(args[3], "AddConnection")
        settings = args[4].unpack()[0]
        self.assertEqual(settings["connection"]["uuid"], saved_uuid)
        self.assertFalse(settings["connection"]["autoconnect"])
        self.assertEqual(bytes(settings["802-11-wireless"]["ssid"]), b"ljq")
        self.assertEqual(settings["802-11-wireless-security"]["psk"], "test-password")
        network.run.assert_not_called()


class SecretsAndLogsTests(unittest.TestCase):
    def test_memory_only_password_does_not_invoke_backend(self):
        store = SecretStore()
        store.backend = Mock(side_effect=RuntimeError("unavailable"))
        store.set("test-id", "private", remember=False)
        self.assertEqual(store.get("test-id"), "private")
        store.backend.assert_not_called()

    def test_failed_keyring_has_no_silent_plaintext_fallback(self):
        store = SecretStore()
        store.backend = Mock(side_effect=RuntimeError("private"))
        with self.assertRaises(RuntimeError) as caught:
            store.set("test-id", "password", remember=True)
        self.assertNotIn("test-id", store.memory)
        self.assertNotIn("private", str(caught.exception))

    def test_log_formatter_hides_query_and_secret_values(self):
        record = logging.LogRecord("test", logging.INFO, "", 0, "URL http://portal/login?token=private password=private", (), None)
        formatted = SafeFormatter("%(message)s").format(record)
        self.assertNotIn("private", formatted)
        self.assertIn("已隐藏", formatted)
