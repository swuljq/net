# 验证记录

## 0.2.0：重庆大学 Dr.COM 适配

依据用户提供的实际请求结构实现 GET/JSONP 认证；测试中仅使用虚构账号、密码、IP，不保留真实抓包参数值。

| 项目 | 操作 | 结果 |
|---|---|---|
| 核心行为 | `/usr/bin/python3 -m unittest discover -s tests -v` | 50 项通过，包含 Dr.COM 本地 HTTP 协议测试 |
| 动态 IPv4 | 从指定无线网卡读取 IPv4；模拟 DHCP 地址变化、无地址和多个地址 | 通过；每次请求重新读取，不固定抓包地址 |
| 参数与返回 | 本地服务检查账号前缀、GET 参数、JSONP、错误响应、成功后独立探测 | 通过；成功响应不直接等于联网成功 |
| 图形界面 | `/usr/bin/python3 tests/gui_smoke.py` | 自动识别、专用模式、普通模式切换、配置持久化及原有启停检查通过 |
| 包内运行 | `/usr/bin/python3 tests/package_smoke.py dist/campus-network-assistant_0.2.0_all.deb` | 通过；包含新认证模式的 GUI 检查 |
| 本机依赖 | `apt-get --simulate install ./dist/campus-network-assistant_0.2.0_all.deb` | 依赖满足，仅计划新增本应用 |
| Python 和桌面入口 | `compileall`、`desktop-file-validate` | 通过 |

真实只读检查确认当前无线网卡能返回有效 IPv4。没有使用用户提供的真实密码提交登录，没有执行真实 Wi-Fi 切换，也没有将安装包安装到系统。

现场验证步骤：

1. 从旧窗口退出后台程序，再运行 `/usr/bin/python3 /home/ldp/App/network/run.py`。
2. 选择“重庆大学 Dr.COM”或“自动识别”，登录网址填写 `https://login.cqu.edu.cn/`，填写原始账号和密码，额外参数保持 `{}`。
3. 确认当前已连接配置的备用校园网，点击“保存并测试认证”。若已联网，将显示已联网并跳过重复认证。
4. 在需要认证时观察真实响应与联网复查结果；如失败，收集界面脱敏日志和脱敏后的响应结构用于进一步适配。
5. 在允许切换网络时启动自动管理，现场验证热点切换和认证过期自动重登。

尚需确认真实服务器响应、真实账号密码可用性、真实登录与认证失效重登。系统密钥环及实际安装/卸载的现场项目仍与下方记录一致。

## 0.1.0：初始版本（历史记录）

环境：Ubuntu 22.04.5 LTS，系统 Python 3.10，GTK 3，NetworkManager，GNOME/X11。

## 已验证

| 项目 | 操作 | 结果 |
|---|---|---|
| 核心行为 | `/usr/bin/python3 -m unittest discover -s tests -v` | 34 项通过 |
| Python 语法 | `/usr/bin/python3 -m compileall -q campus_net packaging tests run.py` | 通过 |
| 桌面入口 | `desktop-file-validate packaging/campus-network-assistant.desktop` | 通过 |
| 原生界面 | `/usr/bin/python3 tests/gui_smoke.py`，以及独立 `dbus-run-session` 中运行 | 通过配置保存、自动启动入口、启停、暂停只读检查、窗口隐藏恢复、单实例检查 |
| 系统只读接口 | 读取真实网卡、已保存连接、当前连接、扫描附近 SSID | 成功；未提交连接切换 |
| 新建 Wi-Fi 参数 | 捕获 D-Bus 参数，以 `NM.SimpleConnection.new_from_dbus(...).verify()` 离线校验 | 通过；未创建真实连接 |
| 表单认证 | 本地 HTTP 认证服务 | 表单/JSON/GET 字段映射、Cookie、认证后探测、认证过期重试、失败退避通过 |
| 安装包构建 | `/usr/bin/python3 packaging/build_deb.py` | 生成 `dist/campus-network-assistant_0.1.0_all.deb` |
| 包结构 | `dpkg-deb --info`、`dpkg-deb --contents` | 元数据、依赖、root 文件归属、入口和资源路径正确 |
| 包内运行 | `/usr/bin/python3 tests/package_smoke.py dist/campus-network-assistant_0.1.0_all.deb` | 隔离解包后使用包内代码通过版本入口及 GUI 冒烟测试 |
| 本机依赖 | `apt-get --simulate install ./dist/campus-network-assistant_0.1.0_all.deb` | 依赖可满足，仅计划新增此应用 |

GUI 测试隔离用户配置和日志，并使用模拟网络及凭据接口；没有读取真实校园网密码。独立 D-Bus 会话下，本机桌面门户有一次初始化等待和挂载警告，等待完成后测试通过；正常桌面会话下测试直接通过。

## 尚需现场验证

- `ljq` 与 `cqu-wifi` 的真实切换和真实密码可用性。
- `cqu-wifi` 实际认证协议、接口字段、真实登录及认证过期后的自动重登。
- 真实系统密钥环密码保存/解锁的交互体验。
- `.deb` 实际安装和卸载后的桌面集成；当前执行了包结构检查、依赖模拟和隔离解包运行，未更改系统安装状态。

上述现场项目没有用模拟测试结果替代。当前实现的是普通 HTTP 表单适配器；真实校园网若使用动态令牌或加密协议，需要对应的协议适配。
