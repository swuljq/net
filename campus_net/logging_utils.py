import logging
import re
from logging.handlers import RotatingFileHandler

from .config import state_dir


class SafeFormatter(logging.Formatter):
    def format(self, record):
        message = super().format(record)
        message = re.sub(r"(https?://[^\s?#]+)[?#][^\s]*", r"\1?[已隐藏]", message)
        message = re.sub(r"(?i)((?:password|passwd|pwd|token|secret|authorization)\s*[=:]\s*)[^\s,;]+", r"\1[已隐藏]", message)
        return message


def setup_logging():
    logger = logging.getLogger("campus_net")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        root = state_dir()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = root / "app.log"
        # 创建时即限制权限，而非先写入再 chmod。
        import os
        fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.close(fd)
        handler = RotatingFileHandler(path, maxBytes=256 * 1024, backupCount=2, encoding="utf-8")
        handler.setFormatter(SafeFormatter("%(asctime)s  %(message)s", "%m-%d %H:%M:%S"))
        logger.addHandler(handler)
    return logger
