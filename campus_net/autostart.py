import os
import sys
from pathlib import Path

from .config import atomic_write


def set_autostart(enabled):
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = root / "autostart/campus-network-assistant.desktop"
    if not enabled:
        path.unlink(missing_ok=True)
        return
    installed = Path("/usr/bin/campus-network-assistant")
    if installed.exists():
        command = "/usr/bin/campus-network-assistant --background"
    else:
        # 源码模式也可使用，Desktop Entry Exec 的转义不同于 shell。
        launcher = Path(__file__).resolve().parent.parent / "run.py"
        def quote(value):
            return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$").replace("%", "%%") + '"'
        command = f"{quote(sys.executable)} {quote(launcher)} --background"
    atomic_write(path, "\n".join([
        "[Desktop Entry]", "Type=Application", "Name=校园网自动连接助手",
        f"Exec={command}", "Icon=campus-network-assistant", "Terminal=false",
        "X-GNOME-Autostart-enabled=true", "X-GNOME-Autostart-Delay=10", "",
    ]))
