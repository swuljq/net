# 0.1.0 验证记录

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
