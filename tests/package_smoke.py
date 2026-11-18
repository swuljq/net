"""解包后从包内代码验证启动，不安装到系统或更改系统网络。"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    package = Path(sys.argv[1]).resolve()
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="campus-deb-") as directory:
        subprocess.run(["dpkg-deb", "--extract", str(package), directory], check=True)
        extracted = Path(directory)
        library = extracted / "usr/lib/campus-network-assistant"
        env = {**os.environ, "PYTHONPATH": str(library), "CAMPUS_TEST_LIBRARY": str(library)}
        launcher = extracted / "usr/bin/campus-network-assistant"
        subprocess.run([str(launcher), "--version"], cwd=directory, env=env, check=True)
        subprocess.run([sys.executable, str(root / "tests/gui_smoke.py")], cwd=directory, env=env, check=True, timeout=75)
    print("安装包隔离解包验证通过：包内入口、GTK 界面和后台控制。")


if __name__ == "__main__":
    main()
