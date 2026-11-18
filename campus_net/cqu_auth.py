"""根据实际抓包实现的重庆大学 Dr.COM Portal GET/JSONP 协议。"""

import json
import random
import re
from urllib.parse import urlsplit, urlunsplit

import requests

from .auth import AuthResult
from .config import cqu_endpoint
from .http import session, small_body
from .network import NetworkError

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def parse_response(body, callback):
    """只解析 JSON，不执行服务器返回的 JavaScript。"""
    body = body.lstrip("\ufeff").strip()
    try:
        payload = json.loads(body)
    except ValueError:
        match = re.fullmatch(
            rf"(?:/\*.*?\*/\s*)?{re.escape(callback)}\s*\((.*)\)\s*;?",
            body, re.DOTALL,
        )
        if not match:
            raise ValueError("认证接口未返回预期的 JSONP") from None
        try:
            payload = json.loads(match.group(1))
        except ValueError:
            raise ValueError("认证接口返回的数据无法解析") from None
    if not isinstance(payload, dict):
        raise ValueError("认证接口返回的数据结构不正确")
    return payload


class CquAuthenticator:
    def __init__(self, network):
        self.network = network

    def authenticate(self, config, password, interface=""):
        config.validate(require_auth=True)
        if not password:
            return AuthResult(False, "未提供校园网密码，请在认证配置中填写")
        endpoint = cqu_endpoint(config.login_url)
        try:
            ip = self.network.ipv4_address(interface or config.interface)
        except NetworkError as exc:
            return AuthResult(False, str(exc))
        callback = f"dr{random.randint(1000, 99999999)}"
        username = config.username
        if not username.startswith(config.account_prefix):
            username = config.account_prefix + username
        params = {
            "login_method": "1",
            "wlan_user_ipv6": "",
            "wlan_user_mac": "000000000000",
            "wlan_ac_ip": "",
            "wlan_ac_name": "",
            "term_type": "1",
            "jsVersion": "4.2.2",
            "terminal_type": "1",
            "lang": "zh",
            **config.extra_params,
            "callback": callback,
            "user_account": username,
            "user_password": password,
            "wlan_user_ip": ip,
            "term_ua": USER_AGENT,
            "v": str(random.randint(500, 10499)),
        }
        parsed = urlsplit(config.login_url)
        referer = urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))
        try:
            with session() as client:
                client.headers.update({"User-Agent": USER_AGENT, "Referer": referer})
                with client.get(endpoint, params=params, timeout=config.timeout, allow_redirects=False, stream=True) as response:
                    if response.status_code != 200:
                        return AuthResult(False, f"Dr.COM 认证接口返回 HTTP {response.status_code}，请检查网址与端口")
                    payload = parse_response(small_body(response), callback)
            if payload.get("result") in (1, "1"):
                return AuthResult(True, "Dr.COM 返回成功，正在复查互联网连接")
            # 已在线不等于确认联网，仍交由 Controller 独立探测。
            message = str(payload.get("msg", "")).lower()
            if "已经在线" in message or "已在线" in message or "already online" in message:
                return AuthResult(True, "Dr.COM 提示已在线，正在复查互联网连接")
            # 不转发服务器正文，避免错误响应回显账号、密码或令牌。
            return AuthResult(False, "Dr.COM 未接受认证，请检查账号、密码、账号前缀和校园网连接")
        except ValueError:
            return AuthResult(False, "Dr.COM 返回格式不符合预期，请确认认证地址和认证模式")
        except requests.RequestException:
            return AuthResult(False, "Dr.COM 请求超时、证书验证失败或连接失败，请检查网络及认证端口")
