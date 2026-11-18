"""与 GTK 无关的串行网络状态机，可用假网络和假时钟测试。"""

import logging
import threading
import time
from dataclasses import dataclass

from .auth import Authenticator
from .connectivity import LinkState, probe
from .network import NetworkError

log = logging.getLogger("campus_net")


@dataclass(frozen=True)
class Status:
    ssid: str = "未连接"
    interface: str = "未选择"
    phase: str = "等待启动"
    detail: str = "配置网络后，点击启动自动管理"


class Controller:
    def __init__(self, config, network, password_getter, notify=lambda status: None,
                 authenticator=None, probe_fn=probe, clock=time.monotonic):
        self.config = config
        self.network = network
        self.password_getter = password_getter
        self.notify = notify
        self.authenticator = authenticator or Authenticator(network)
        self.probe = probe_fn
        self.clock = clock
        self.cancel = threading.Event()
        self.status = Status()
        self.reset()

    def reset(self):
        self.preferred_since = None
        self.connection_failures = {}
        self.cooldowns = {}
        self.auth_failures = 0
        self.auth_retry_at = 0

    def configure(self, config):
        self.config = config
        self.reset()

    def report(self, phase, detail, snapshot=None):
        status = Status(
            snapshot.ssid or "未连接" if snapshot else self.status.ssid,
            snapshot.interface if snapshot else self.status.interface,
            phase, detail,
        )
        if status != self.status:
            log.info("%s · %s", phase, detail)
        self.status = status
        self.notify(status)

    def _authenticate(self, snapshot, force=False):
        cfg, now = self.config, self.clock()
        result = self.probe(cfg)
        if self.cancel.is_set():
            return
        if result.state == LinkState.ONLINE:
            self.auth_failures = 0
            self.auth_retry_at = 0
            self.report("已联网", result.message, snapshot)
            return
        if result.state != LinkState.PORTAL and not force:
            self.report("状态未知", result.message, snapshot)
            return
        if not cfg.auth_enabled:
            self.report("需要认证", "自动认证已关闭，可打开登录页手动认证", snapshot)
            return
        if now < self.auth_retry_at and not force:
            self.report("等待重试", f"认证暂缓，约 {int(self.auth_retry_at - now) + 1} 秒后重试", snapshot)
            return
        try:
            cfg.validate(require_auth=True)
            password = self.password_getter(cfg.credential_id)
        except (ValueError, RuntimeError) as exc:
            self.report("需要配置", str(exc), snapshot)
            return
        if self.cancel.is_set():
            return
        self.report("正在认证", "正在提交校园网认证请求", snapshot)
        result = self.authenticator.authenticate(cfg, password, interface=snapshot.interface)
        if self.cancel.is_set():
            return
        if result.submitted:
            checked = self.probe(cfg)
            if self.cancel.is_set():
                return
            if checked.state == LinkState.ONLINE:
                self.auth_failures = 0
                self.auth_retry_at = 0
                self.report("已联网", "校园网认证成功，互联网连接正常", snapshot)
                return
            message = "认证后仍未确认联网，请检查字段、密码或校园网协议适配"
        else:
            message = result.message
        self.auth_failures += 1
        delay = min(30 * 2 ** min(self.auth_failures - 1, 5), 600)
        self.auth_retry_at = self.clock() + delay
        self.report("认证未成功", f"{message}；{delay} 秒后重试", snapshot)

    def tick(self, authenticate_only=False):
        if self.cancel.is_set():
            return
        cfg, now = self.config, self.clock()
        try:
            snapshot = self.network.snapshot(cfg.interface)
            if self.cancel.is_set():
                return
            if authenticate_only:
                if snapshot.ssid != cfg.fallback_ssid:
                    self.report("无法测试认证", "请先连接配置的校园网，再点击测试认证", snapshot)
                    return
                self._authenticate(snapshot, force=True)
                return
            visible = snapshot.visible
            if cfg.preferred_ssid in visible:
                if self.preferred_since is None:
                    self.preferred_since = now
            else:
                self.preferred_since = None
            preferred_ready = self.preferred_since is not None and (
                now - self.preferred_since >= cfg.stable_seconds or snapshot.ssid == cfg.preferred_ssid
            )
            candidates = []
            if preferred_ready:
                candidates.append((cfg.preferred_ssid, cfg.preferred_uuid))
            if cfg.fallback_ssid in visible:
                candidates.append((cfg.fallback_ssid, cfg.fallback_uuid))
            target = next(((ssid, uid) for ssid, uid in candidates if now >= self.cooldowns.get(ssid, 0)), None)
            if not target:
                if snapshot.ssid == cfg.preferred_ssid:
                    checked = self.probe(cfg)
                    if self.cancel.is_set():
                        return
                    self.report("已联网" if checked.state == LinkState.ONLINE else "优先 Wi-Fi 已连接", checked.message, snapshot)
                elif snapshot.ssid == cfg.fallback_ssid:
                    self._authenticate(snapshot)
                else:
                    self.report("等待网络", "目标 Wi-Fi 未就绪，保留当前连接并继续扫描", snapshot)
                return
            ssid, profile_uuid = target
            if snapshot.ssid != ssid or (profile_uuid and profile_uuid != snapshot.uuid):
                self.report("正在连接", f"正在连接 {ssid}", snapshot)
                if self.cancel.is_set():
                    return
                try:
                    self.network.connect(ssid, profile_uuid, snapshot.interface)
                    self.connection_failures[ssid] = 0
                except NetworkError:
                    count = self.connection_failures.get(ssid, 0) + 1
                    self.connection_failures[ssid] = count
                    if count >= 2:
                        self.cooldowns[ssid] = now + 120
                        self.connection_failures[ssid] = 0
                    raise
                if self.cancel.is_set():
                    return
                snapshot = self.network.snapshot(snapshot.interface)
                if snapshot.ssid != ssid or (profile_uuid and snapshot.uuid != profile_uuid):
                    self.report("连接未确认", "系统尚未切换到目标网络，将在下一轮重新检查", snapshot)
                    return
            if self.cancel.is_set():
                return
            if snapshot.ssid == cfg.fallback_ssid:
                self._authenticate(snapshot)
            else:
                checked = self.probe(cfg)
                if self.cancel.is_set():
                    return
                self.report("已联网" if checked.state == LinkState.ONLINE else "优先 Wi-Fi 已连接", checked.message, snapshot)
        except NetworkError as exc:
            if not self.cancel.is_set():
                self.report("网络操作未完成", str(exc))
