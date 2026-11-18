"""直连 HTTP 会话，不继承环境代理，不将带认证信息的异常写入日志。"""

import requests


def session():
    client = requests.Session()
    client.trust_env = False
    client.max_redirects = 5
    client.headers["User-Agent"] = "CampusNetworkAssistant/0.1"
    return client


def small_body(response, limit=16384):
    result = bytearray()
    for chunk in response.iter_content(chunk_size=2048):
        result.extend(chunk[:max(0, limit - len(result))])
        if len(result) >= limit:
            break
    return bytes(result).decode(response.encoding or "utf-8", errors="replace")
