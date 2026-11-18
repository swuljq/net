#!/usr/bin/python3
"""使用系统 Python 和 dpkg-deb 构建，无 pip 或 root 构建依赖。"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from campus_net import __version__  # noqa: E402


def build():
    if not shutil.which("dpkg-deb"):
        raise SystemExit("请先安装 dpkg：sudo apt install dpkg")
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    stage = ROOT / "build/deb"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    library = stage / "usr/lib/campus-network-assistant/campus_net"
    library.mkdir(parents=True)
    for source in (ROOT / "campus_net").glob("*.py"):
        shutil.copyfile(source, library / source.name)
    launcher = stage / "usr/bin/campus-network-assistant"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(
        '#!/usr/bin/python3\nimport sys\nsys.path.insert(0, "/usr/lib/campus-network-assistant")\n'
        'from campus_net.ui import main\nraise SystemExit(main())\n', encoding="utf-8",
    )
    for source, target in (
        ("packaging/campus-network-assistant.desktop", "usr/share/applications/campus-network-assistant.desktop"),
        ("packaging/campus-network-assistant.svg", "usr/share/icons/hicolor/scalable/apps/campus-network-assistant.svg"),
        ("README.md", "usr/share/doc/campus-network-assistant/README.md"),
        ("LICENSE", "usr/share/doc/campus-network-assistant/copyright"),
    ):
        destination = stage / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, destination)
    installed_size = (sum(p.stat().st_size for p in stage.rglob("*") if p.is_file()) + 1023) // 1024
    control = stage / "DEBIAN/control"
    control.parent.mkdir()
    control.write_text(
        f"Package: campus-network-assistant\nVersion: {__version__}\n"
        "Section: net\nPriority: optional\nArchitecture: all\n"
        "Maintainer: Campus Network Assistant <noreply@users.noreply.github.com>\n"
        f"Installed-Size: {installed_size}\n"
        "Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-3.0, python3-requests, "
        "python3-keyring, python3-secretstorage, network-manager, dbus-user-session\n"
        "Recommends: gnome-keyring\n"
        "Homepage: https://github.com/swuljq/net\n"
        "Description: Configurable Wi-Fi priority and campus portal assistant\n"
        " Native GTK desktop application with NetworkManager integration, configurable\n"
        " portal authentication, retry backoff and desktop autostart.\n", encoding="utf-8",
    )
    for path in stage.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    stage.chmod(0o755)
    launcher.chmod(0o755)
    # 允许 CI 用 SOURCE_DATE_EPOCH 复现包内文件时间。
    if os.environ.get("SOURCE_DATE_EPOCH"):
        epoch = int(os.environ["SOURCE_DATE_EPOCH"])
        for path in stage.rglob("*"):
            os.utime(path, (epoch, epoch))
    destination = output / f"campus-network-assistant_{__version__}_all.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(stage), str(destination)], check=True)
    print(f"安装包：{destination}")


if __name__ == "__main__":
    build()
