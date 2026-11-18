"""普通表单适配器；专用协议可实现相同的 authenticate 接口。"""

from dataclasses import dataclass

import requests

from .http import session, small_body


@dataclass(frozen=True)
class AuthResult:
    submitted: bool
    message: str


class FormAuthenticator:
    def authenticate(self, config, password):
        config.validate(require_auth=True)
        if not password:
            return AuthResult(False, "未提供校园网密码，请在认证配置中填写")
        params = {**config.extra_params, config.username_field: config.username, config.password_field: password}
        try:
            with session() as client:
                if config.preflight_url:
                    with client.get(config.preflight_url, timeout=config.timeout, stream=True) as response:
                        if response.status_code >= 400:
                            return AuthResult(False, "预访问登录页失败，请检查预访问地址")
                        small_body(response)
                kwargs = {"timeout": config.timeout, "allow_redirects": False, "stream": True}
                if config.method == "GET":
                    response = client.get(config.login_url, params=params, **kwargs)
                elif config.method == "POST_JSON":
                    response = client.post(config.login_url, json=params, **kwargs)
                else:
                    response = client.post(config.login_url, data=params, **kwargs)
                with response:
                    if response.status_code >= 400:
                        return AuthResult(False, f"认证服务器返回 HTTP {response.status_code}，请检查认证配置")
                    body = small_body(response).lower()
                    if any(marker in body for marker in ("captcha", "recaptcha", "扫码登录", "请输入验证码")):
                        return AuthResult(False, "页面要求验证码或扫码，普通表单无法自动完成；请打开登录页")
                    return AuthResult(True, "认证请求已提交，正在复查互联网连接")
        except requests.RequestException:
            return AuthResult(False, "认证请求超时、证书验证失败或连接失败；请检查地址及网络")
