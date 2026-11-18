"""无敏感信息的用户配置和 XDG 路径。"""

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

APP_NAME = "campus-network-assistant"
DEFAULT_PROBE_URL = "http://connectivity-check.ubuntu.com/"


def config_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME


def state_dir():
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / APP_NAME


def validate_url(value, label):
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in ("http", "https") and parsed.hostname and parsed.port != 0
        if not valid or parsed.username or parsed.password or parsed.fragment:
            raise ValueError
    except ValueError:
        raise ValueError(f"{label}必须是有效的 HTTP/HTTPS 地址，且不含用户名、密码或片段") from None


def uses_cqu(config):
    return config.auth_mode == "cqu_drcom" or (
        config.auth_mode == "auto" and urlsplit(config.login_url).hostname == "login.cqu.edu.cn"
    )


def cqu_endpoint(value):
    """支持入口页或不含参数的接口地址，不接受含密码的完整抓包 URL。"""
    validate_url(value, "登录网址")
    parsed = urlsplit(value)
    if parsed.query or parsed.path not in ("", "/", "/eportal/portal/login", "/eportal/portal/login/"):
        raise ValueError("重庆大学认证请填写入口网址或不含查询参数的 /eportal/portal/login 地址")
    netloc = parsed.netloc
    if parsed.port is None:
        netloc += ":802" if parsed.scheme == "https" else ":801"
    return urlunsplit((parsed.scheme, netloc, "/eportal/portal/login", "", ""))


@dataclass
class Config:
    version: int = 1
    interface: str = ""
    preferred_ssid: str = "ljq"
    fallback_ssid: str = "cqu-wifi"
    preferred_uuid: str = ""
    fallback_uuid: str = ""
    auth_enabled: bool = True
    auth_mode: str = "auto"
    account_prefix: str = ",0,"
    login_url: str = ""
    preflight_url: str = ""
    username: str = ""
    username_field: str = "username"
    password_field: str = "password"
    method: str = "POST_FORM"
    extra_params: dict = field(default_factory=dict)
    timeout: int = 10
    probe_url: str = DEFAULT_PROBE_URL
    probe_expected: str = ""
    probe_status: int = 204
    interval: int = 30
    stable_seconds: int = 15
    autostart: bool = False
    enabled: bool = False
    credential_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def validate(self, require_auth=False):
        for f in fields(self):
            value = getattr(self, f.name)
            if f.type is int and (not isinstance(value, int) or isinstance(value, bool)):
                raise ValueError(f"配置项 {f.name} 必须是整数")
            if f.type is bool and not isinstance(value, bool):
                raise ValueError(f"配置项 {f.name} 必须是布尔值")
            if f.type is str and (not isinstance(value, str) or "\x00" in value or "\n" in value):
                raise ValueError(f"配置项 {f.name} 格式错误")
        if self.version != 1:
            raise ValueError("不支持此配置版本")
        for ssid in (self.preferred_ssid, self.fallback_ssid):
            if not ssid or len(ssid.encode("utf-8")) > 32:
                raise ValueError("Wi-Fi 名称不能为空，且不能超过 32 字节")
        if self.preferred_ssid == self.fallback_ssid:
            raise ValueError("优先和备用 Wi-Fi 不能相同")
        if not 5 <= self.interval <= 3600 or not 0 <= self.stable_seconds <= 600:
            raise ValueError("检查间隔应为 5–3600 秒，稳定时间应为 0–600 秒")
        if not 2 <= self.timeout <= 60:
            raise ValueError("请求超时应为 2–60 秒")
        if self.method not in ("POST_FORM", "POST_JSON", "GET"):
            raise ValueError("认证提交方式无效")
        if self.auth_mode not in ("auto", "form", "cqu_drcom"):
            raise ValueError("认证模式无效")
        if not isinstance(self.extra_params, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in self.extra_params.items()
        ):
            raise ValueError("额外参数应为字符串键值组成的 JSON 对象")
        sensitive = ("password", "passwd", "pwd", "token", "secret", "authorization")
        if any(any(word in k.lower() for word in sensitive) for k in self.extra_params):
            raise ValueError("额外参数不能存放密码或令牌；密码请填写到专用密码框")
        if self.login_url:
            validate_url(self.login_url, "登录网址")
            if uses_cqu(self):
                cqu_endpoint(self.login_url)
                allowed = {
                    "login_method", "wlan_user_ipv6", "wlan_user_mac", "wlan_ac_ip", "wlan_ac_name",
                    "term_type", "jsVersion", "terminal_type", "lang",
                }
                if self.extra_params.keys() - allowed:
                    raise ValueError("重庆大学额外参数仅支持静态协议选项；账号、密码、IPv4 和回调由程序生成")
        if self.preflight_url:
            validate_url(self.preflight_url, "预访问地址")
        validate_url(self.probe_url, "联网检测网址")
        if not 200 <= self.probe_status <= 299:
            raise ValueError("联网检测预期状态码应为 200–299")
        if not self.probe_expected and self.probe_status != 204:
            raise ValueError("除预期状态码 204 外，必须设置联网检测的预期内容")
        for value in (self.preferred_uuid, self.fallback_uuid, self.credential_id):
            if value:
                try:
                    uuid.UUID(value)
                except ValueError:
                    raise ValueError("连接或凭据标识格式错误") from None
        if self.auth_enabled and (require_auth or self.login_url):
            if not self.login_url or not self.username:
                raise ValueError("启用自动认证时，请填写登录网址和账号")
            if not uses_cqu(self) and (not self.username_field or not self.password_field):
                raise ValueError("请填写账号字段名和密码字段名")
            if not uses_cqu(self) and self.username_field == self.password_field:
                raise ValueError("账号字段和密码字段不能相同")
            if not uses_cqu(self) and (self.username_field in self.extra_params or self.password_field in self.extra_params):
                raise ValueError("额外参数不能覆盖账号或密码字段")
        return self


def load_config(path=None):
    path = Path(path) if path else config_dir() / "config.json"
    if not path.exists():
        return Config()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        # Ubuntu 检测服务当前返回 204，无正文。仅迁移完整匹配的旧默认组合，
        # 不覆盖用户设置的其他检测服务/状态码/内容，也不在加载时重写文件。
        if (data.get("probe_url"), data.get("probe_status"), data.get("probe_expected")) == (
            DEFAULT_PROBE_URL, 200, "NetworkManager is online"
        ):
            data.update(probe_status=204, probe_expected="")
        allowed = {f.name for f in fields(Config)}
        return Config(**{k: v for k, v in data.items() if k in allowed}).validate()
    except (ValueError, TypeError, OSError):
        raise ValueError("配置文件无法读取或校验失败，请检查用户配置文件") from None


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save_config(config, path=None):
    config.validate()
    atomic_write(path or config_dir() / "config.json", json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n")
