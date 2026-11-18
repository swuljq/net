from dataclasses import dataclass
from enum import Enum

import requests

from .http import session, small_body


class LinkState(Enum):
    ONLINE = "online"
    PORTAL = "portal"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProbeResult:
    state: LinkState
    message: str


def probe(config):
    try:
        with session() as client:
            with client.get(config.probe_url, timeout=config.timeout, allow_redirects=False, stream=True) as response:
                if 300 <= response.status_code < 400 and response.headers.get("Location"):
                    return ProbeResult(LinkState.PORTAL, "联网检测被重定向，需要校园网认证")
                body = small_body(response)
                if response.status_code == config.probe_status and (
                    config.probe_expected in body if config.probe_expected else response.status_code == 204
                ):
                    return ProbeResult(LinkState.ONLINE, "互联网连接正常")
                lowered = body.lower()
                if response.status_code == 200 and "<form" in lowered and (
                    "password" in lowered or "login" in lowered or "登录" in body
                ):
                    return ProbeResult(LinkState.PORTAL, "联网检测返回登录页面，需要校园网认证")
                return ProbeResult(LinkState.UNKNOWN, "检测响应与预期不符；请检查网络或调整检测地址")
    except requests.RequestException:
        return ProbeResult(LinkState.UNKNOWN, "联网检测超时或连接失败，稍后重试")
