"""隔离 XDG、D-Bus 和网络后验证真实 GTK 窗口，不操作实际 Wi-Fi。"""

import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

os.environ["GIO_USE_VFS"] = "local"
os.environ["GTK_USE_PORTAL"] = "0"
os.environ["NO_AT_BRIDGE"] = "1"
os.environ["GTK_MODULES"] = ""
os.environ["XDG_CURRENT_DESKTOP"] = ""
os.environ["GSETTINGS_BACKEND"] = "memory"
os.environ["GTK_THEME"] = "Adwaita"

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = Path(os.environ.get("CAMPUS_TEST_LIBRARY", ROOT))
sys.path.insert(0, str(LIBRARY))

from campus_net.config import load_config
from campus_net.network import Profile, Snapshot
from campus_net.ui import CampusApplication, Gdk, GLib, Gtk
from campus_net.connectivity import LinkState, ProbeResult


class FakeNetwork:
    def __init__(self):
        self.calls = []
        self.ssid = "cqu-wifi"

    def interfaces(self):
        return ["wlan-test"]

    def profiles(self):
        return [Profile("测试连接", "11111111-1111-1111-1111-111111111111", "ljq")]

    def snapshot(self, interface=""):
        return Snapshot("wlan-test", self.ssid, "", frozenset({"测试热点", "cqu-wifi"}))

    def current(self, interface=""):
        return self.snapshot(interface)

    def connect(self, ssid, *_):
        self.calls.append(ssid)
        self.ssid = ssid


class FakeSecrets:
    def get(self, _):
        return ""


def main():
    errors = []
    state = {"step": 0, "child": None}
    network = FakeNetwork()
    app = CampusApplication()
    app.set_application_id("io.github.swuljq.CampusNetwork.Smoke")

    def fail(exc):
        errors.append(str(exc))
        traceback.print_exc()
        app.exit_app()
        return GLib.SOURCE_REMOVE

    def step():
        try:
            if app.busy or not app.window:
                return GLib.SOURCE_CONTINUE
            window = app.window
            if state["step"] == 0:
                app.controller.probe = lambda _: ProbeResult(LinkState.ONLINE, "模拟联网正常")
                assert window.widgets["interval"].get_value_as_int() == 30
                assert window.widgets["preferred_ssid"].get_text() == "ljq"
                screenshot = os.environ.get("CAMPUS_GUI_SCREENSHOT")
                if screenshot:
                    surface = window.get_window()
                    Gdk.pixbuf_get_from_window(surface, 0, 0, window.get_allocated_width(), window.get_allocated_height()).savev(screenshot, "png", [], [])
                window.widgets["preferred_ssid"].set_text("测试热点")
                window.widgets["auth_enabled"].set_active(False)
                window.widgets["stable_seconds"].set_value(0)
                window.widgets["autostart"].set_active(True)
                window.save_form()
                state["step"] = 1
            elif state["step"] == 1:
                saved = load_config()
                assert saved.preferred_ssid == "测试热点"
                assert not saved.auth_enabled and not saved.enabled
                assert (Path(os.environ["XDG_CONFIG_HOME"]) / "autostart/campus-network-assistant.desktop").exists()
                window.on_toggle()
                state["step"] = 2
            elif state["step"] == 2:
                assert app.running
                app.on_timer()
                state["step"] = 3
            elif state["step"] == 3:
                assert network.calls == ["测试热点"]
                app.pause()
                assert not load_config().enabled
                app.check_now()
                state["step"] = 4
            elif state["step"] == 4:
                assert network.calls == ["测试热点"], "暂停后的检查不得切换网络"
                with patch.object(app, "send_notification"):
                    window.on_close()
                assert not window.get_visible()
                app.show_window()
                assert window.get_visible() and len(app.get_windows()) == 1
                code = "from campus_net.ui import CampusApplication; a=CampusApplication(); a.set_application_id('io.github.swuljq.CampusNetwork.Smoke'); raise SystemExit(a.run(['smoke','--background']))"
                state["child"] = subprocess.Popen([sys.executable, "-c", code], cwd=LIBRARY)
                state["step"] = 5
            elif state["step"] == 5:
                result = state["child"].poll()
                if result is None:
                    return GLib.SOURCE_CONTINUE
                assert result == 0, "第二实例应转交给已有进程"
                assert len(app.get_windows()) == 1
                app.exit_app()
                return GLib.SOURCE_REMOVE
        except Exception as exc:
            return fail(exc)
        return GLib.SOURCE_CONTINUE

    def timeout():
        errors.append(f"GUI 测试超过 60 秒（阶段 {state['step']}，busy={app.busy}，window={app.window is not None}）")
        for frame in sys._current_frames().values():
            traceback.print_stack(frame)
        if state["child"] and state["child"].poll() is None:
            state["child"].kill()
        app.exit_app()
        return GLib.SOURCE_REMOVE

    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
        "XDG_CONFIG_HOME": str(Path(directory) / "config"),
        "XDG_STATE_HOME": str(Path(directory) / "state"),
    }), patch("campus_net.ui.NetworkManager", return_value=network), patch("campus_net.ui.SecretStore", FakeSecrets), \
         patch("campus_net.ui.probe", return_value=ProbeResult(LinkState.ONLINE, "模拟联网正常")), \
         patch("campus_net.controller.probe", return_value=ProbeResult(LinkState.ONLINE, "模拟联网正常")):
        GLib.timeout_add(150, step)
        GLib.timeout_add_seconds(60, timeout)
        result = app.run([sys.argv[0]])
    if errors:
        raise SystemExit("GUI 冒烟测试失败：" + "; ".join(errors))
    assert result == 0
    print("GUI 冒烟测试通过：配置保存、自动启动、启停、只读检查、隐藏恢复、单实例。")


if __name__ == "__main__":
    main()
