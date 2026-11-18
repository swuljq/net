# 校园网自动连接助手

Ubuntu/Linux 原生桌面应用：优先连接 `ljq`，不可用时连接 `cqu-wifi`，检测校园网认证过期并重新登录。Wi-Fi 名称、认证地址、账号、密码和运行参数均可在界面配置。

技术栈：Python 3 + GTK 3 + NetworkManager。当前目标环境为 Ubuntu 22.04（GNOME 桌面）。

## 安装

构建后的安装包位于 `dist/campus-network-assistant_0.1.0_all.deb`。GitHub Actions 每次推送也会构建安装包，可从对应工作流运行的 **Artifacts → ubuntu-deb** 下载并解压。

```bash
sudo apt install ./campus-network-assistant_0.1.0_all.deb
```

从应用菜单打开“校园网自动连接助手”，或运行：

```bash
campus-network-assistant
```

安装包自身较小，Python、GTK、NetworkManager 等运行库由 Ubuntu 包管理器按需安装。安装过程需要能访问已配置的软件源；不是将全部系统依赖捆绑的离线包。

## 首次使用

1. **网络配置**：选择无线网卡；设置优先 Wi-Fi 和备用校园网。默认分别是 `ljq`、`cqu-wifi`。
2. 为两个网络选择已保存的系统连接。没有配置时点击“新建系统连接…”，填写开放网络或 WPA/WPA2 个人网络信息。保存系统连接不会立即切换网络。企业 Wi-Fi 请先在系统网络设置中配置。
3. **校园网认证**：填写登录接口地址、账号、密码。密码默认保存到系统密钥环；取消勾选后仅在本次运行中保留。清除已经保存的密码需点击“清除已保存密码”。
4. 根据实际登录协议调整“高级认证参数”，然后保存。
5. **运行选项**：按需开启“登录桌面后自动启动”。
6. 点击“启动自动管理”。首次安装默认不会自动连接或提交认证。

关闭窗口后继续后台运行；从应用菜单再次打开显示同一个进程。点击“暂停自动管理”会保留当前连接并停止后续自动操作；暂停时“立即检查”只读取网络和探测联网状态，不切换 Wi-Fi、不提交认证。点击右上角“退出程序”结束后台进程。

退出进程保留上次启用状态，下次启动会恢复；需要下次也不自动连接时，请先暂停再退出。

```bash
campus-network-assistant --background  # 不显示窗口启动
campus-network-assistant --quit        # 退出当前后台实例
campus-network-assistant --version
```

## 认证兼容性：地址可配置，协议仍需匹配

**当前实现普通 HTTP 表单认证，不表示已经适配并验证 `cqu-wifi` 的真实协议。** 仅填写浏览器的登录页面地址通常不够，还需要知道页面向哪个接口提交、字段名称及额外参数。

支持：

- POST 表单、POST JSON、GET 查询参数。
- 自定义账号字段名、密码字段名和非敏感额外参数。
- 可选预访问地址，用同一个会话保存 Cookie。
- 认证后独立检查联网结果；不会把 HTTP 200 直接当作成功。

例如，一个普通表单服务的配置可能是：

| 项目 | 示例（不是 cqu-wifi 的已知配置） |
|---|---|
| 登录网址 | `http://portal.example/login` |
| 提交方式 | POST 表单 |
| 账号字段 | `username` |
| 密码字段 | `password` |
| 额外参数 | `{"action": "login"}` |

额外参数只接受字符串键值 JSON 对象，禁止用其持久化密码和令牌。动态令牌、JavaScript 加密、挑战协议、验证码或扫码页面需要专用适配器；可通过“打开登录页”先手动登录。拿到真实登录页面或脱敏请求结构后，可在 `campus_net/auth.py` 的适配器接口上接入。

“保存并测试认证”只会在当前无线网卡已经连接配置的备用校园网时提交认证；它不会自动切换网络。即使自动管理暂停，此按钮也会按用户的明确操作执行一次测试。

## 切换和重试规则

- 默认每 30 秒扫描一次，优先 Wi-Fi 连续可见至少 15 秒后切换（实际切换时刻受检查间隔影响）。
- 当前已连接优先网络时不为稳定等待而先切走。
- 连续 2 次连接失败后，该目标冷却 120 秒，期间可使用备用网络。
- 已联网不重复认证；识别到门户重定向或登录表单时才自动认证。
- DNS 错误、超时等只标记为状态未知，避免反复提交密码。
- 认证失败按 30、60、120…秒退避，最多 10 分钟。
- 两个网络都不可用或扫描失败时保留现有连接。
- 优先网络没有互联网时仅显示状态，不因单次外网探测失败自动切走。
- 暂停可阻止后续操作；已发出的 NetworkManager 或 HTTP 操作会在自身超时后结束，无法撤回已提交的认证或连接请求。

默认联网探测使用 Ubuntu 连通性检查服务，可在运行选项中更换地址、预期内容和状态码。检测和认证不继承环境代理，但 VPN/策略路由仍可能影响探测流量；使用时应确保探测能反映校园网自身的连通性。

## 本机数据和权限

- 配置：`~/.config/campus-network-assistant/config.json`，只包含配置和校园网账号，不包含密码。
- 日志：`~/.local/state/campus-network-assistant/app.log`，最多 3 份，每份 256 KiB。
- 自动启动：`~/.config/autostart/campus-network-assistant.desktop`。
- 校园网密码：桌面 Secret Service 密钥环，服务名 `io.github.swuljq.CampusNetwork`；Wi-Fi 密码由 NetworkManager 保存。
- 支持 `XDG_CONFIG_HOME` / `XDG_STATE_HOME`。

无需使用 root 启动界面。NetworkManager 操作使用桌面用户的 Polkit 权限；系统策略不允许时，可先在 Ubuntu 网络设置中建立连接。密钥环不可用时不会退回明文存储，可取消“保存密码”在本次运行中使用。

## 从源码运行与构建

```bash
sudo apt install python3 python3-gi gir1.2-gtk-3.0 python3-requests \
  python3-keyring python3-secretstorage network-manager dbus-user-session gnome-keyring
/usr/bin/python3 run.py
/usr/bin/python3 packaging/build_deb.py
```

使用 `/usr/bin/python3`，避免 Conda 或虚拟环境缺少系统 GTK 绑定。

验证：

```bash
/usr/bin/python3 -m unittest discover -s tests -v
# GUI 冒烟测试不访问真实凭据，不连接真实 Wi-Fi；需要图形显示。
dbus-run-session -- /usr/bin/python3 tests/gui_smoke.py
# 无图形会话的 CI 可安装 xvfb 后运行：
xvfb-run -a dbus-run-session -- /usr/bin/python3 tests/gui_smoke.py
# 隔离解包后使用包内代码进行同样的 GUI 检查：
/usr/bin/python3 tests/package_smoke.py dist/campus-network-assistant_0.1.0_all.deb
```

测试使用模拟网络和本地 HTTP 服务。真实热点切换、真实校园网协议和认证过期重连需在目标网络现场验收。

## 卸载

先退出后台程序，再卸载：

```bash
campus-network-assistant --quit
sudo apt remove campus-network-assistant
```

卸载保留个人配置。若需彻底清理，请在卸载前通过界面清除已保存的校园网密码，并关闭自动启动；卸载后可删除上述个人配置、日志和自动启动文件。已创建的 NetworkManager 连接仍由系统网络设置管理。

## 开发约定

设计见 [`docs/superpowers/specs/2026-11-17-campus-network-design.md`](docs/superpowers/specs/2026-11-17-campus-network-design.md)。`main` 分支，提交说明使用中文；个人配置、密码、日志和生成的安装包不进入 Git。
