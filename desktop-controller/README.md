# 主控控制台

用户于 2026-10-17 确认的 Windows 桌面入口，参见[方案及范围调整](../docs/plans/DESKTOP_CONTROLLER_20261017.md)。采集和图表复用主控原检测页面。当前交付及验收只在 [STATUS](../docs/STATUS.md) 汇总。

- 原生 WPF、本机 .NET Framework 4.8，无 NuGet 或第三方运行时。
- 保存地址、一次性只读连接检查、受限局域网发现、调用默认浏览器打开检测页。
- Windows Wi-Fi / 移动热点设置入口、连接步骤、本机日志。
- 不轮询、不发送 POST、不操作串口，不提供固件升级或 Wi-Fi 凭据写入接口。重复打开检测页前提醒已有页面；无法跨浏览器或进程重启保证只有一个页面。

## 分开的入口

使用项目 PowerShell 7.6.6：

~~~powershell
# 仅构建和编译测试程序，不运行、不安装、不访问设备
& .\desktop-controller\build.ps1

# 用输出路径执行本地模拟测试；HTTP 只访问临时 loopback 服务器
& '<build-output>\CoreTests.exe' '<build-output>'

# 离线 UI 预览，输出 PNG 后退出；不访问设备、不写用户配置或启动外部程序
Start-Process -FilePath '<build-output>\主控控制台.exe' -ArgumentList @('--preview', '<preview-output>') -WindowStyle Hidden -Wait

# 将已核对哈希的具体产物安装到桌面；不启动程序、不改系统网络或设备
& .\desktop-controller\install-desktop.ps1 -BuildDirectory '<build-output>' -ExpectedExecutableSha256 '<sha256>'
~~~

toolchain.json 固定系统编译器和十个框架引用的 SHA-256；禁用隐式编译器配置/引用。构建清单记录源文件、系统引用程序集、产物及版本，不宣称不同 Windows 补丁环境字节可复现。构建和安装不互相调用。安装拒绝覆盖同名程序或快捷方式。正常启动不使用 --preview，用户可以直接双击 EXE/快捷方式。

网络查找最多四个不同地址并发，每个地址一次 GET /api/status、700ms 超时；较大网络限本机 /24，较小子网尊重掩码，另检查保存地址。手动检查 2800ms 超时。只接受局域网 IPv4/HTTP，不跟随重定向，响应上限 16KiB，验证既有 boot/卡/状态字段，只保留可显示的设备信息，不留控制令牌。单实例仅约束桌面应用；原检测页并发风险仍需用户只保留一页。

源代码自行复核及可用审查工具的实际结果记录在本轮报告，模拟通过不能替代主控实机连通或采集验收。
