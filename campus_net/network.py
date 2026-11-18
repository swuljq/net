"""只封装 NetworkManager；不输出可能包含敏感信息的原始命令错误。"""

import getpass
import ipaddress
import os
import subprocess
import uuid
from dataclasses import dataclass


class NetworkError(RuntimeError):
    pass


def split_escaped(line):
    result, part, escape = [], [], False
    for char in line:
        if escape:
            part.append(char)
            escape = False
        elif char == "\\":
            escape = True
        elif char == ":":
            result.append("".join(part))
            part = []
        else:
            part.append(char)
    if escape:
        part.append("\\")
    result.append("".join(part))
    return result


@dataclass(frozen=True)
class Profile:
    name: str
    uuid: str
    ssid: str
    interface: str = ""
    key_mgmt: str = ""

    @property
    def security_label(self):
        if self.key_mgmt in ("", "--"):
            return "开放网络"
        if self.key_mgmt == "wpa-psk":
            return "WPA/WPA2 密码网络"
        if self.key_mgmt in ("wpa-eap", "ieee8021x", "wpa-eap-suite-b-192"):
            return "企业认证网络"
        return "加密网络"


@dataclass(frozen=True)
class Snapshot:
    interface: str
    ssid: str
    uuid: str
    visible: frozenset
    device_state: int = 100

    @property
    def connecting(self):
        return 40 <= self.device_state <= 90


class NetworkManager:
    def run(self, *args, timeout=25, wait=20):
        try:
            result = subprocess.run(
                ["nmcli", "--colors", "no", "--wait", str(wait), *args],
                capture_output=True, text=True, timeout=timeout,
                env={**os.environ, "LC_ALL": "C"},
            )
        except FileNotFoundError:
            raise NetworkError("未找到 nmcli，请安装 network-manager") from None
        except subprocess.TimeoutExpired:
            raise NetworkError("NetworkManager 操作超时") from None
        if result.returncode:
            messages = {3: "网络操作超时", 4: "连接失败，请检查 Wi-Fi 密码和信号", 8: "NetworkManager 未运行", 10: "网络连接或设备不存在"}
            raise NetworkError(messages.get(result.returncode, "NetworkManager 操作失败，请检查系统网络设置和用户权限"))
        return result.stdout.strip("\n")

    def interfaces(self):
        rows = [split_escaped(row) for row in self.run("-t", "-f", "DEVICE,TYPE,STATE", "device", "status").splitlines()]
        return [row[0] for row in rows if len(row) == 3 and row[1] == "wifi"]

    def profile(self, profile_uuid):
        value = self.run("-g", "connection.id,connection.uuid,802-11-wireless.ssid,connection.interface-name,802-11-wireless-security.key-mgmt", "connection", "show", "uuid", profile_uuid)
        rows = value.split("\n")
        if len(rows) < 3:
            raise NetworkError("所选连接不是有效的 Wi-Fi 配置")
        # -g 仍使用转义，字段各占一行。
        return Profile(*(split_escaped(v)[0] for v in (rows + ["", ""])[:5]))

    def profiles(self):
        rows = [split_escaped(row) for row in self.run("-t", "-f", "NAME,UUID,TYPE", "connection", "show").splitlines()]
        return [self.profile(row[1]) for row in rows if len(row) == 3 and row[2] == "802-11-wireless"]

    def select_interface(self, interface=""):
        if self.run("radio", "wifi") != "enabled":
            raise NetworkError("Wi-Fi 已关闭；请先在系统设置中开启 Wi-Fi")
        devices = self.interfaces()
        if not devices:
            raise NetworkError("未找到无线网卡")
        if not interface:
            if len(devices) != 1:
                raise NetworkError("存在多个无线网卡，请在网络配置中选择网卡")
            interface = devices[0]
        if interface not in devices:
            raise NetworkError("所选无线网卡不存在或已移除")
        return interface

    def _current(self, interface, visible=frozenset()):
        raw_state = self.run("-g", "GENERAL.STATE", "device", "show", interface)
        try:
            device_state = int(raw_state.split()[0])
        except (ValueError, IndexError):
            raise NetworkError("无法识别无线网卡连接状态，请在系统网络设置中检查") from None
        active_uuid = self.run("-g", "GENERAL.CON-UUID", "device", "show", interface).strip()
        if active_uuid in ("", "--"):
            return Snapshot(interface, "", "", visible, device_state)
        active = self.profile(active_uuid)
        return Snapshot(interface, active.ssid, active.uuid, visible, device_state)

    def current(self, interface=""):
        return self._current(self.select_interface(interface))

    def ipv4_address(self, interface=""):
        """每次认证时读取指定网卡的地址，不使用 VPN/默认路由的源地址。"""
        interface = self.select_interface(interface)
        output = self.run("-g", "IP4.ADDRESS", "device", "show", interface)
        addresses = []
        for line in output.splitlines():
            try:
                address = ipaddress.IPv4Interface(line.strip()).ip
            except ValueError:
                continue
            if not (address.is_unspecified or address.is_loopback or address.is_link_local or address.is_multicast):
                addresses.append(str(address))
        addresses = list(dict.fromkeys(addresses))
        if len(addresses) > 1:
            raise NetworkError("所选无线网卡有多个 IPv4 地址，请在系统网络设置中确认校园网地址")
        if not addresses:
            raise NetworkError("所选无线网卡尚未取得有效 IPv4 地址，等待 DHCP 分配后重试")
        return addresses[0]

    def snapshot(self, interface=""):
        interface = self.select_interface(interface)
        current = self._current(interface)
        if current.connecting:
            # 不在系统关联热点、协商密码或 DHCP 时触发扫描和新的连接请求。
            return current
        # rescan yes 失败时保留现有连接，不能将失败解释为“没有热点”。
        scan = self.run("-t", "-f", "SSID", "device", "wifi", "list", "ifname", interface, "--rescan", "yes")
        visible = frozenset(split_escaped(line)[0] for line in scan.splitlines() if line)
        return self._current(interface, visible)

    def connect(self, ssid, profile_uuid, interface):
        if profile_uuid:
            selected = self.profile(profile_uuid)
            if selected.ssid != ssid or (selected.interface and selected.interface != interface):
                raise NetworkError("已保存连接与 Wi-Fi 名称或网卡不匹配，请重新选择连接配置")
        else:
            candidates = [p for p in self.profiles() if p.ssid == ssid and (not p.interface or p.interface == interface)]
            if len(candidates) > 1:
                raise NetworkError("同名 Wi-Fi 有多个已保存连接，请在界面中明确选择")
            if not candidates:
                raise NetworkError("未找到此 Wi-Fi 的系统连接配置，请先保存连接或在系统网络设置中连接一次")
            selected = candidates[0]
            profile_uuid = selected.uuid
        self.check_security(selected, interface)
        # 覆盖正常的 Wi-Fi 关联及 DHCP 等待，不与 HTTP 认证超时混用。
        self.run("connection", "up", "uuid", profile_uuid, "ifname", interface, timeout=65, wait=60)

    def check_security(self, profile, interface):
        """启动连接前验证缓存中的热点安全方式，避免先断开现有好连接。"""
        rows = self.run("-t", "-f", "SSID,SECURITY", "device", "wifi", "list", "ifname", interface, "--rescan", "no")
        advertised = [parts[1] for parts in map(split_escaped, rows.splitlines()) if len(parts) == 2 and parts[0] == profile.ssid]
        if not advertised:
            raise NetworkError("目标 Wi-Fi 已不在扫描列表中，暂不发起连接，请稍后重试")
        open_ap = any(value in ("", "--") for value in advertised)
        secured_ap = any(value not in ("", "--") for value in advertised)
        secured_profile = profile.key_mgmt not in ("", "--")
        if secured_profile and not secured_ap:
            raise NetworkError("目标 Wi-Fi 是开放网络，但所选连接配置为加密网络；请选择开放网络连接，校园网密码请填写在认证页")
        if not secured_profile and not open_ap:
            raise NetworkError("目标 Wi-Fi 需要无线密码，但所选连接配置为开放网络；请重新选择或创建匹配的系统连接")

    def create_profile(self, ssid, interface, password=""):
        """通过系统 D-Bus 保存，不连接；密码不会出现在进程命令行。"""
        from gi.repository import Gio, GLib

        new_uuid = str(uuid.uuid4())
        connection = {
            "id": GLib.Variant("s", f"校园网助手 · {ssid}"),
            "uuid": GLib.Variant("s", new_uuid),
            "type": GLib.Variant("s", "802-11-wireless"),
            "autoconnect": GLib.Variant("b", False),
            "permissions": GLib.Variant("as", [f"user:{getpass.getuser()}:"]),
        }
        if interface:
            connection["interface-name"] = GLib.Variant("s", interface)
        wifi = {"ssid": GLib.Variant("ay", list(ssid.encode("utf-8"))), "mode": GLib.Variant("s", "infrastructure")}
        settings = {
            "connection": connection,
            "802-11-wireless": wifi,
            "ipv4": {"method": GLib.Variant("s", "auto")},
            "ipv6": {"method": GLib.Variant("s", "auto")},
        }
        if password:
            settings["802-11-wireless-security"] = {
                "key-mgmt": GLib.Variant("s", "wpa-psk"),
                "psk": GLib.Variant("s", password),
            }
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            bus.call_sync(
                "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager/Settings",
                "org.freedesktop.NetworkManager.Settings", "AddConnection",
                GLib.Variant("(a{sa{sv}})", (settings,)), None,
                Gio.DBusCallFlags.NONE, 25000, None,
            )
        except GLib.Error:
            raise NetworkError("无法保存系统连接，请检查 Wi-Fi 密码格式或在系统网络设置中创建连接") from None
        return new_uuid
