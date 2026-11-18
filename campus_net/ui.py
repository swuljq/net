"""GTK 桌面界面。所有网络和密钥环操作在同一个工作线程串行执行。"""

import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __version__
from .autostart import set_autostart
from .config import Config, load_config, save_config
from .connectivity import probe
from .controller import Controller, Status
from .logging_utils import SafeFormatter, setup_logging
from .network import NetworkManager
from .secrets import SecretStore

APP_ID = "io.github.swuljq.CampusNetwork"
CSS = b"""
window.campus { background: #F7F9FC; color: #17324D; }
.campus .brand { font-size: 22px; font-weight: bold; }
.campus .section-title { font-size: 19px; font-weight: bold; }
.campus .caption { color: #5A7085; font-size: 12px; }
.campus .card { background: #FFFFFF; border: 1px solid #DCE4ED; border-radius: 10px; padding: 18px; }
.campus .route-title { color: #246BCE; font-weight: bold; }
.campus .status-title { font-size: 20px; font-weight: bold; }
.campus .online { color: #137C70; }
.campus .route-arrow { color: #5A7085; font-size: 22px; }
.campus .sidebar { background: #EEF3F9; border-right: 1px solid #DCE4ED; }
.campus .mono { font-family: monospace; }
.campus button.suggested-action { background: #246BCE; color: white; border-color: #246BCE; }
.campus textview, .campus textview text { background: #FFFFFF; color: #17324D; }
.campus stacksidebar row { padding: 14px 16px; }
"""


def label(text, css=None):
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_line_wrap(True)
    if css:
        widget.get_style_context().add_class(css)
    return widget


def button(text, callback, suggested=False):
    widget = Gtk.Button(label=text)
    widget.connect("clicked", callback)
    if suggested:
        widget.get_style_context().add_class("suggested-action")
    return widget


class UILogHandler(logging.Handler):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setFormatter(SafeFormatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record):
        GLib.idle_add(self.app.append_log, self.format(record))


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="校园网自动连接助手")
        self.app = app
        self.widgets = {}
        self.profiles = []
        self.updating_profiles = False
        self.set_default_size(1020, 780)
        self.set_size_request(850, 620)
        self.get_style_context().add_class("campus")
        self.set_icon_name("campus-network-assistant")
        self.connect("delete-event", self.on_close)
        header = Gtk.HeaderBar(title="校园网助手", subtitle="Wi-Fi 优先连接 · 校园网自动认证")
        header.set_show_close_button(True)
        header.pack_end(button("退出程序", lambda *_: app.exit_app()))
        self.set_titlebar(header)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.add(root)
        navigation = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        navigation.set_size_request(176, -1)
        navigation.get_style_context().add_class("sidebar")
        brand = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        brand.set_margin_top(26)
        brand.set_margin_start(20)
        brand.set_margin_bottom(12)
        brand.pack_start(label("校园网助手", "brand"), False, False, 0)
        brand.pack_start(label("让连接持续在线", "caption"), False, False, 0)
        navigation.pack_start(brand, False, False, 0)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE)
        sidebar = Gtk.StackSidebar(stack=self.stack)
        navigation.pack_start(sidebar, True, True, 0)
        footer = label(f"本机桌面应用\nv{__version__}", "caption")
        footer.set_margin_start(20)
        footer.set_margin_bottom(20)
        navigation.pack_end(footer, False, False, 0)
        root.pack_start(navigation, False, False, 0)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_border_width(24)
        content.set_hexpand(True)
        root.pack_start(content, True, True, 0)
        status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        status.get_style_context().add_class("card")
        status_top = Gtk.Box(spacing=12)
        self.status_title = label("等待启动", "status-title")
        self.spinner = Gtk.Spinner()
        status_top.pack_start(self.status_title, True, True, 0)
        status_top.pack_end(self.spinner, False, False, 0)
        status.pack_start(status_top, False, False, 0)
        self.status_detail = label("配置网络后，点击启动自动管理", "caption")
        self.current_network = label("当前 Wi-Fi：读取中…", "mono")
        status.pack_start(self.status_detail, False, False, 0)
        status.pack_start(self.current_network, False, False, 0)
        controls = Gtk.Box(spacing=8)
        self.run_button = button("启动自动管理", self.on_toggle, True)
        self.check_button = button("立即检查", lambda *_: app.check_now())
        self.open_button = button("打开登录页", self.on_open_login)
        controls.pack_start(self.run_button, False, False, 0)
        controls.pack_start(self.check_button, False, False, 0)
        controls.pack_start(self.open_button, False, False, 0)
        status.pack_start(controls, False, False, 4)
        content.pack_start(status, False, False, 0)

        self.message = label("", "caption")
        self.message.set_no_show_all(True)
        content.pack_start(self.message, False, False, 0)
        self._network_page()
        self._auth_page()
        self._options_page()
        self._logs_page()
        content.pack_start(self.stack, True, True, 0)
        bottom = Gtk.Box(spacing=12)
        bottom.pack_start(label("关闭窗口后继续后台运行", "caption"), True, True, 0)
        self.save_button = button("保存配置", lambda *_: self.save_form(), True)
        bottom.pack_end(self.save_button, False, False, 0)
        content.pack_end(bottom, False, False, 0)
        self.populate(app.config)

    def page(self, name, title, description):
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        outer.set_margin_end(8)
        outer.pack_start(label(title, "section-title"), False, False, 0)
        outer.pack_start(label(description, "caption"), False, False, 0)
        scroll.add(outer)
        self.stack.add_titled(scroll, name, title)
        return outer

    def form(self, parent):
        grid = Gtk.Grid(column_spacing=16, row_spacing=12)
        grid.set_hexpand(True)
        parent.pack_start(grid, False, False, 0)
        return grid

    def row(self, grid, title, widget):
        row = len(grid.get_children()) // 2
        title_widget = label(title)
        title_widget.set_valign(Gtk.Align.START)
        title_widget.set_margin_top(8)
        grid.attach(title_widget, 0, row, 1, 1)
        widget.set_hexpand(True)
        grid.attach(widget, 1, row, 1, 1)

    def entry(self, name, placeholder="", secret=False):
        entry = Gtk.Entry(placeholder_text=placeholder)
        entry.set_visibility(not secret)
        entry.set_input_purpose(Gtk.InputPurpose.PASSWORD if secret else Gtk.InputPurpose.FREE_FORM)
        self.widgets[name] = entry
        return entry

    def spin(self, name, lower, upper):
        spin = Gtk.SpinButton.new_with_range(lower, upper, 1)
        self.widgets[name] = spin
        return spin

    def check(self, name, text):
        widget = Gtk.CheckButton(label=text)
        self.widgets[name] = widget
        return widget

    def _network_page(self):
        page = self.page("networks", "网络配置", "按下面的顺序选择网络。自动管理只使用你指定的两个 Wi-Fi。")
        grid = self.form(page)
        self.device_combo = Gtk.ComboBoxText()
        self.row(grid, "无线网卡", self.device_combo)
        self.device_combo.append("auto", "自动选择（仅一张无线网卡时）")
        self.refresh_button = button("刷新网卡和已保存连接", lambda *_: self.app.refresh_networks())
        self.row(grid, "系统连接", self.refresh_button)
        route = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        route.set_homogeneous(False)
        for key, title in (("preferred", "优先连接"), ("fallback", "备用校园网")):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            card.get_style_context().add_class("card")
            card.set_hexpand(True)
            card.pack_start(label(title, "route-title"), False, False, 0)
            card.pack_start(self.entry(f"{key}_ssid", "Wi-Fi 名称"), False, False, 0)
            combo = Gtk.ComboBoxText()
            combo.append("auto", "自动匹配已保存的连接")
            self.widgets[f"{key}_uuid"] = combo
            combo.connect("changed", self.on_profile_changed, key)
            card.pack_start(combo, False, False, 0)
            create = button("新建系统连接…", lambda _button, k=key: self.create_profile_dialog(k))
            self.widgets[f"{key}_create"] = create
            card.pack_start(create, False, False, 0)
            route.pack_start(card, True, True, 0)
            if key == "preferred":
                arrow = label("→", "route-arrow")
                route.pack_start(arrow, False, False, 0)
        page.pack_start(route, False, False, 4)
        page.pack_start(label("找不到优先 Wi-Fi 或连接多次失败时使用备用网络；优先网络恢复稳定后自动切回。", "caption"), False, False, 0)
        page.pack_start(label("企业 Wi-Fi（802.1X）请先在系统网络设置中配置，再在这里选择已保存连接。新建连接不会立即切换网络。", "caption"), False, False, 0)

    def _auth_page(self):
        page = self.page("auth", "校园网认证", "认证地址、账号和参数都保存在本机。密码使用系统密钥环保存。")
        page.pack_start(self.check("auth_enabled", "启用校园网自动认证"), False, False, 0)
        grid = self.form(page)
        self.row(grid, "登录网址", self.entry("login_url", "http://校园网认证服务器/登录接口"))
        self.row(grid, "校园网账号", self.entry("username"))
        self.row(grid, "校园网密码", self.entry("password", "输入密码", secret=True))
        password_options = Gtk.Box(spacing=12)
        remember = self.check("remember_password", "保存密码到系统密钥环")
        remember.set_active(True)
        password_options.pack_start(remember, False, False, 0)
        show = Gtk.CheckButton(label="显示密码")
        show.connect("toggled", lambda toggle: self.widgets["password"].set_visibility(toggle.get_active()))
        password_options.pack_start(show, False, False, 0)
        self.row(grid, "", password_options)
        buttons = Gtk.Box(spacing=8)
        self.test_button = button("保存并测试认证", lambda *_: self.save_form(test=True))
        self.clear_password_button = button("清除已保存密码", lambda *_: self.app.clear_password())
        buttons.pack_start(self.test_button, False, False, 0)
        buttons.pack_start(self.clear_password_button, False, False, 0)
        page.pack_start(buttons, False, False, 0)
        expander = Gtk.Expander(label="高级认证参数 · 普通 HTTP 表单")
        advanced = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        advanced.set_border_width(12)
        fields = self.form(advanced)
        method = Gtk.ComboBoxText()
        for key, title in (("POST_FORM", "POST 表单（默认）"), ("POST_JSON", "POST JSON"), ("GET", "GET 查询参数")):
            method.append(key, title)
        self.widgets["method"] = method
        self.row(fields, "提交方式", method)
        self.row(fields, "账号字段名", self.entry("username_field"))
        self.row(fields, "密码字段名", self.entry("password_field"))
        self.row(fields, "预访问地址", self.entry("preflight_url", "可选；用于建立登录会话"))
        self.row(fields, "额外参数 JSON", self.entry("extra_params", '{"action": "login"}'))
        advanced.pack_start(label("登录网址应填写真正接收账号密码的接口地址。动态加密、验证码、扫码等专用认证需要额外适配；普通表单不保证兼容所有校园网。", "caption"), False, False, 0)
        advanced.pack_start(label("GET 方式会把账号密码放入请求 URL，建议优先使用 POST。额外参数只填写非敏感选项；密码使用上方专用字段。", "caption"), False, False, 0)
        expander.add(advanced)
        page.pack_start(expander, False, False, 0)
        page.pack_start(label("测试认证前需要已连接备用校园网。返回 HTTP 200 不代表成功，程序会再次检查互联网连接。", "caption"), False, False, 0)

    def _options_page(self):
        page = self.page("options", "运行选项", "调整检查频率、稳定等待和联网检测。暂停后保留当前 Wi-Fi 连接。")
        grid = self.form(page)
        self.row(grid, "检查间隔（秒）", self.spin("interval", 5, 3600))
        self.row(grid, "优先网络稳定时间（秒）", self.spin("stable_seconds", 0, 600))
        self.row(grid, "请求超时（秒）", self.spin("timeout", 2, 60))
        page.pack_start(self.check("autostart", "登录桌面后自动启动"), False, False, 0)
        page.pack_start(label("自动启动会恢复上次启用或暂停的状态。关闭窗口继续运行；点击右上角“退出程序”才会结束进程。", "caption"), False, False, 0)
        expander = Gtk.Expander(label="高级联网检测")
        advanced = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        advanced.set_border_width(12)
        fields = self.form(advanced)
        self.row(fields, "检测网址", self.entry("probe_url"))
        self.row(fields, "预期内容", self.entry("probe_expected"))
        self.row(fields, "预期 HTTP 状态码", self.spin("probe_status", 200, 299))
        advanced.pack_start(label("默认使用 Ubuntu 连通性检测服务。检测异常不会直接判断为密码错误；只有识别出门户页面后才自动认证。检测不使用环境代理，VPN 路由仍可能影响结果。", "caption"), False, False, 0)
        expander.add(advanced)
        page.pack_start(expander, False, False, 0)

    def _logs_page(self):
        page = self.page("logs", "运行日志", "只记录运行状态和脱敏错误，不记录认证请求正文。显示最近 500 条。")
        tools = Gtk.Box(spacing=8)
        tools.pack_start(button("复制日志", self.copy_logs), False, False, 0)
        tools.pack_start(button("清空显示", lambda *_: self.log_view.get_buffer().set_text("")), False, False, 0)
        page.pack_start(tools, False, False, 0)
        self.log_view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.log_view.set_left_margin(12)
        self.log_view.set_right_margin(12)
        self.log_view.set_top_margin(12)
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(180)
        scroll.set_vexpand(True)
        scroll.add(self.log_view)
        page.pack_start(scroll, True, True, 0)

    def populate(self, config):
        for name, widget in self.widgets.items():
            if not hasattr(config, name):
                continue
            value = getattr(config, name)
            if name.endswith("_uuid"):
                widget.set_active_id(value or "auto")
            elif isinstance(widget, Gtk.SpinButton):
                widget.set_value(value)
            elif isinstance(widget, Gtk.Entry):
                widget.set_text(json.dumps(value, ensure_ascii=False) if name == "extra_params" else value)
            elif isinstance(widget, Gtk.CheckButton):
                widget.set_active(value)
            elif isinstance(widget, Gtk.ComboBoxText):
                widget.set_active_id(value)
        self.device_combo.set_active_id(config.interface or "auto")

    def update_profiles(self, interfaces, profiles):
        self.updating_profiles = True
        self.profiles = profiles
        selected_device = self.device_combo.get_active_id() or self.app.config.interface or "auto"
        self.device_combo.remove_all()
        self.device_combo.append("auto", "自动选择（仅一张无线网卡时）")
        for name in interfaces:
            self.device_combo.append(name, name)
        if selected_device not in interfaces and selected_device != "auto":
            self.device_combo.append(selected_device, f"{selected_device}（未找到）")
        self.device_combo.set_active_id(selected_device)
        for key in ("preferred", "fallback"):
            combo = self.widgets[f"{key}_uuid"]
            selected = combo.get_active_id() or getattr(self.app.config, f"{key}_uuid") or "auto"
            combo.remove_all()
            combo.append("auto", "自动匹配已保存的连接")
            for profile in profiles:
                combo.append(profile.uuid, f"{profile.name} · {profile.ssid}")
            if selected != "auto" and selected not in [p.uuid for p in profiles]:
                combo.append(selected, "原连接未找到，请重新选择")
            combo.set_active_id(selected)
        self.updating_profiles = False

    def on_profile_changed(self, combo, key):
        if self.updating_profiles:
            return
        selected = next((p for p in self.profiles if p.uuid == combo.get_active_id()), None)
        if selected:
            self.widgets[f"{key}_ssid"].set_text(selected.ssid)

    def read_form(self):
        values = {}
        for name, widget in self.widgets.items():
            if not hasattr(self.app.config, name):
                continue
            if isinstance(widget, Gtk.SpinButton):
                values[name] = widget.get_value_as_int()
            elif isinstance(widget, Gtk.Entry):
                values[name] = widget.get_text().strip()
            elif isinstance(widget, Gtk.CheckButton):
                values[name] = widget.get_active()
            elif isinstance(widget, Gtk.ComboBoxText):
                value = widget.get_active_id()
                values[name] = "" if value == "auto" else (value or "")
        try:
            values["extra_params"] = json.loads(values["extra_params"] or "{}")
        except ValueError:
            raise ValueError("额外参数不是有效的 JSON，请填写类似 {\"action\": \"login\"} 的对象") from None
        device = self.device_combo.get_active_id()
        values["interface"] = "" if device in (None, "auto") else device
        return replace(self.app.config, **values).validate()

    def save_form(self, start=False, test=False):
        try:
            config = self.read_form()
            config.enabled = start or self.app.running
            if start or test:
                config.validate(require_auth=True)
            password = self.widgets["password"].get_text()
            remember = self.widgets["remember_password"].get_active()
        except ValueError as exc:
            self.show_message(str(exc))
            return
        self.app.save_form(config, password, remember, start, test)

    def on_toggle(self, *_):
        if self.app.running:
            self.app.pause()
        else:
            self.save_form(start=True)

    def on_open_login(self, *_):
        from .config import validate_url
        url = self.widgets["login_url"].get_text().strip()
        try:
            validate_url(url, "登录网址")
            Gio.AppInfo.launch_default_for_uri(url, None)
        except (ValueError, GLib.Error):
            self.show_message("无法打开登录页，请检查网址和默认浏览器")

    def create_profile_dialog(self, key):
        if self.app.busy:
            return
        ssid = self.widgets[f"{key}_ssid"].get_text().strip()
        if not ssid or len(ssid.encode("utf-8")) > 32:
            self.show_message("请先填写有效的 Wi-Fi 名称")
            return
        dialog = Gtk.Dialog(title=f"保存 Wi-Fi · {ssid}", transient_for=self, modal=True)
        dialog.add_buttons("取消", Gtk.ResponseType.CANCEL, "保存到系统", Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_border_width(18)
        box.set_spacing(12)
        box.pack_start(label("只保存连接，不立即切换。支持开放网络或 WPA/WPA2 个人网络。"), False, False, 0)
        security = Gtk.ComboBoxText()
        security.append("wpa", "WPA/WPA2 个人网络")
        security.append("open", "开放网络（无 Wi-Fi 密码）")
        security.set_active_id("wpa")
        box.pack_start(security, False, False, 0)
        password = Gtk.Entry(placeholder_text="Wi-Fi 密码", visibility=False)
        security.connect("changed", lambda c: password.set_sensitive(c.get_active_id() == "wpa"))
        box.pack_start(password, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        value = password.get_text() if security.get_active_id() == "wpa" else ""
        secured = security.get_active_id() == "wpa"
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        if secured and not (8 <= len(value.encode("utf-8")) <= 63 or (len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value))):
            self.show_message("WPA/WPA2 密码须为 8–63 字节，或 64 位十六进制密钥")
            return
        device = self.device_combo.get_active_id()
        interface = "" if device in (None, "auto") else device

        def job():
            new_uuid = self.app.network.create_profile(ssid, interface, value)
            return new_uuid, self.app.network.interfaces(), self.app.network.profiles()

        def done(result):
            new_uuid, interfaces, profiles = result
            self.update_profiles(interfaces, profiles)
            self.widgets[f"{key}_uuid"].set_active_id(new_uuid)
            self.show_message("系统连接已保存。点击“保存配置”应用到助手。")

        self.app.submit(job, done)

    def show_message(self, text):
        self.message.set_text(text)
        self.message.set_visible(bool(text))

    def set_busy(self, busy):
        self.spinner.start() if busy else self.spinner.stop()
        for widget in (self.save_button, self.check_button, self.test_button, self.clear_password_button, self.refresh_button,
                       self.widgets["preferred_create"], self.widgets["fallback_create"]):
            widget.set_sensitive(not busy)
        self.run_button.set_sensitive(not busy or self.app.running)
        self.run_button.set_label("暂停自动管理" if self.app.running else "启动自动管理")

    def set_status(self, status):
        self.status_title.set_text(status.phase)
        style = self.status_title.get_style_context()
        style.add_class("online") if status.phase == "已联网" else style.remove_class("online")
        self.status_detail.set_text(status.detail)
        self.current_network.set_text(f"当前 Wi-Fi：{status.ssid}    网卡：{status.interface}")

    def append_log(self, text):
        buf = self.log_view.get_buffer()
        buf.insert(buf.get_end_iter(), text + "\n")
        if buf.get_line_count() > 501:
            buf.delete(buf.get_start_iter(), buf.get_iter_at_line(buf.get_line_count() - 501))

    def copy_logs(self, *_):
        buf = self.log_view.get_buffer()
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False), -1)
        self.show_message("已复制脱敏日志")

    def on_close(self, *_):
        self.hide()
        notice = Gio.Notification.new("校园网助手已在后台运行")
        notice.set_body("从应用菜单再次打开可查看状态；使用窗口右上角“退出程序”可结束运行。")
        notice.set_default_action("app.show")
        self.app.send_notification("background", notice)
        return True


class CampusApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.busy = False
        self.running = False
        self.exiting = False
        self.next_tick = 0
        self.generation = 0
        self.logs = []
        self.interfaces = []
        self.profiles = []
        self.password = ""
        self.startup_error = ""

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.logger = setup_logging()
        self.logger.addHandler(UILogHandler(self))
        try:
            self.config = load_config()
        except ValueError as exc:
            self.config = Config()
            self.startup_error = str(exc)
        self.secrets = SecretStore()
        self.network = NetworkManager()
        self.controller = Controller(self.config, self.network, self.secrets.get, self.on_status)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="campus-network")
        for name, callback in (("show", lambda *_: self.show_window()), ("quit", lambda *_: self.exit_app())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        GLib.timeout_add_seconds(1, self.on_timer)
        self.submit(self.initialise, self.initialised)

    def initialise(self):
        # 读取凭据与扫描分开处理，Wi-Fi 暂时关闭不会阻止打开配置界面。
        warnings = []
        try:
            password = self.secrets.get(self.config.credential_id) if self.config.username else ""
        except RuntimeError as exc:
            password = ""
            warnings.append(str(exc))
        try:
            interfaces, profiles = self.network.interfaces(), self.network.profiles()
        except RuntimeError as exc:
            interfaces, profiles = [], []
            warnings.append(str(exc))
        try:
            current = self.network.current(self.config.interface)
        except RuntimeError:
            current = None
        return password, interfaces, profiles, warnings, current

    def initialised(self, result):
        self.password, self.interfaces, self.profiles, warnings, current = result
        if self.window:
            if not self.window.widgets["password"].get_text():
                self.window.widgets["password"].set_text(self.password)
            self.window.update_profiles(self.interfaces, self.profiles)
        if current:
            self.controller.report("准备启动" if self.config.enabled else "已暂停", "点击启动自动管理，或使用“立即检查”读取联网状态", current)
        for warning in warnings:
            self.logger.info(warning)
        if self.startup_error:
            self.message(self.startup_error)
        elif self.config.enabled:
            self.running = True
            self.controller.cancel.clear()
            self.next_tick = 0

    def do_activate(self):
        self.show_window()

    def do_command_line(self, command_line):
        args = command_line.get_arguments()[1:]
        unknown = set(args) - {"--background", "--quit"}
        if unknown:
            print("支持的参数：--background、--quit、--version", file=sys.stderr)
            if not command_line.get_is_remote():
                self.exit_app()
            return 2
        if "--quit" in args:
            self.exit_app()
        elif "--background" not in args:
            self.show_window()
        return 0

    def show_window(self):
        if self.exiting:
            return
        if self.window is None:
            self.window = MainWindow(self)
            self.window.update_profiles(self.interfaces, self.profiles)
            self.window.widgets["password"].set_text(self.password)
            for line in self.logs:
                self.window.append_log(line)
        self.window.show_all()
        if self.startup_error:
            self.window.show_message(self.startup_error)
        self.window.set_busy(self.busy)
        self.window.set_status(self.controller.status)
        self.window.present()

    def append_log(self, text):
        self.logs.append(text)
        self.logs = self.logs[-500:]
        if self.window and not self.exiting:
            self.window.append_log(text)
        return GLib.SOURCE_REMOVE

    def message(self, text):
        self.logger.info(text)
        if self.window:
            self.window.show_message(text)

    def on_status(self, status):
        def apply():
            if self.window and not self.exiting:
                self.window.set_status(status)
            return GLib.SOURCE_REMOVE
        GLib.idle_add(apply)

    def submit(self, work, done=None):
        if self.busy or self.exiting:
            return
        self.busy = True
        if self.window:
            self.window.set_busy(True)
        future = self.executor.submit(work)

        def finished(future):
            def apply():
                self.busy = False
                if self.exiting:
                    return GLib.SOURCE_REMOVE
                try:
                    result = future.result()
                    if done:
                        done(result)
                except (ValueError, RuntimeError, OSError) as exc:
                    self.message(str(exc))
                except Exception as exc:
                    self.logger.error("内部操作未完成（%s）", type(exc).__name__)
                    self.message("操作未完成，请检查配置和系统服务后重试")
                if not self.running:
                    self.controller.cancel.set()
                if self.window:
                    self.window.set_busy(False)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(apply)
        future.add_done_callback(finished)

    def save_form(self, config, password, remember, start, test):
        generation = self.generation
        previous = self.config

        def work():
            if password:
                self.secrets.set(config.credential_id, password, remember)
            if start and config.auth_enabled and not self.secrets.get(config.credential_id):
                raise ValueError("请先填写校园网密码，或关闭自动认证")
            try:
                set_autostart(config.autostart)
                save_config(config)
            except OSError:
                set_autostart(previous.autostart)
                raise RuntimeError("无法保存配置或自动启动入口，请检查目录权限") from None
            self.controller.configure(config)
            return config

        def done(saved):
            if generation != self.generation:
                saved = replace(saved, enabled=False)
                save_config(saved)
            self.config = saved
            self.password = password or self.password
            self.startup_error = ""
            if start and generation == self.generation:
                self.running = True
                self.controller.cancel.clear()
                self.next_tick = 0
            self.message("配置已保存" + ("，自动管理已启动" if start and self.running else ""))
            if test:
                # 等当前回调释放 busy，再串行提交测试。
                GLib.idle_add(self.test_auth)

        self.submit(work, done)

    def pause(self):
        self.generation += 1
        self.running = False
        self.controller.cancel.set()
        self.config = replace(self.config, enabled=False)
        try:
            save_config(self.config)
        except OSError:
            self.message("已暂停，但无法保存暂停状态，请检查目录权限")
        self.controller.report("已暂停", "保留当前 Wi-Fi；点击启动可恢复自动管理")
        if self.window:
            self.window.set_busy(self.busy)

    def on_timer(self):
        if self.exiting:
            return GLib.SOURCE_REMOVE
        if self.running and not self.busy and time.monotonic() >= self.next_tick:
            self.next_tick = time.monotonic() + self.config.interval
            self.submit(self.controller.tick)
        return GLib.SOURCE_CONTINUE

    def check_now(self):
        if self.running:
            self.submit(self.controller.tick)
            return

        def work():
            snapshot = self.network.snapshot(self.config.interface)
            result = probe(self.config)
            self.controller.report("已暂停 · 只读检查", result.message, snapshot)
        self.submit(work)

    def test_auth(self):
        if not self.busy:
            self.controller.cancel.clear()
            self.submit(lambda: self.controller.tick(authenticate_only=True))
        return GLib.SOURCE_REMOVE

    def refresh_networks(self):
        def done(result):
            self.interfaces, self.profiles = result
            if self.window:
                self.window.update_profiles(*result)
            self.message("网卡和已保存连接已刷新")
        self.submit(lambda: (self.network.interfaces(), self.network.profiles()), done)

    def clear_password(self):
        def done(_result):
            self.password = ""
            if self.window:
                self.window.widgets["password"].set_text("")
            self.message("已清除系统密钥环及本次运行中的校园网密码")
        self.submit(lambda: self.secrets.delete(self.config.credential_id), done)

    def exit_app(self):
        if self.exiting:
            return
        self.exiting = True
        self.controller.cancel.set()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.quit()

    def do_shutdown(self):
        self.exiting = True
        self.controller.cancel.set()
        self.executor.shutdown(wait=False, cancel_futures=True)
        Gtk.Application.do_shutdown(self)


def main():
    if "--version" in sys.argv[1:]:
        print(f"校园网自动连接助手 {__version__}")
        return 0
    return CampusApplication().run(sys.argv)
