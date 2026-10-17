# 主控控制台 v2

Windows WPF / .NET Framework 4.8 桌面入口，需要配套主控 v2 固件。用户批准范围见[任务方案](../docs/plans/CONTROLLER_V2_20261017.md)，实际交付状态只在[STATUS](../docs/STATUS.md)维护。

桌面提供原生 USB 连接、Wi-Fi 地址保存/只读检查/受限局域网查找、打开检测页和主控网络设置、Windows 网络设置及日志。不增加桌面 TF 页面；TF 管理只在主控统一网页提供。旧固件缺少 v2 能力时明确拒绝新控制入口。

USB 使用 ESP32-S3 原生 Serial/JTAG 端口（VID303A/PID1001），CH340 UART 保留诊断。程序启动不打开串口；点击连接后使用 Win32 端口 I/O，仅配置超时，不切换 DTR/RTS，不发送复位/烧录命令。恢复旧版上的打开/关闭探针已通过无复位检查，候选采集中的整体验证见本轮报告。

USB 和 Wi-Fi 均经随机端口的 127.0.0.1 HTTP 转接打开网页，不需要管理员 URL ACL、服务或防火墙规则。浏览器以原生 WebSocket 心跳续期；关闭所属网页、刷新、离开或关闭桌面程序释放关联会话。主控失联保护10秒发起停止，排空/同步/校验耗时另计。程序只释放自己转接页面的会话，直接局域网页面的所有者由该页面负责。后台/最小化不主动停止；不自动重发开始或恢复录制。

同一局域网可直接访问主控实际 Wi-Fi/AP 地址；USB 本机地址仅当前电脑可用。网页显示网络网址和热点密码，不回显已保存的 STA 密码。按用户选择不设管理码，同网络用户具有控制和文件删除权限；热点保留 WPA2 密码。

## 分开的入口

使用项目 PowerShell 7.6.6：

~~~powershell
# 仅编译，不访问设备或安装
& .\desktop-controller\build.ps1
# 临时 loopback 模拟服务器、协议检查
& '<build-output>\CoreTests.exe' '<test-output>'
# 离线 UI 预览；不访问设备
Start-Process -FilePath '<build-output>\主控控制台.exe' -ArgumentList @('--preview', '<preview-output>') -WindowStyle Hidden -Wait
# 安装具体干净候选；保留已有 v1 程序，不自动启动
& .\desktop-controller\install-desktop.ps1 -Candidate -BuildDirectory '<build-output>' -ExpectedExecutableSha256 '<sha256>'
~~~

toolchain.json 固定编译器和系统程序集哈希，无 NuGet/第三方桌面运行时。构建清单绑定源码/引用/测试/EXE，安装核对产物并拒绝覆盖同名目标。候选安装名为“主控控制台 v2（待实机验收）”，旧版保持原处。

## 相关验证

WSL/Linux：g++、libssl-dev、libcjson-dev；运行 python3 tools/test_controller_v2.py。
Windows：固定 Node、Playwright1.62.1、本机 Edge；运行 tools/test_controller_v2_ui.cjs 和 tools/test_sensor_web_ui.cjs。

文件底层测试编译实际 files_v2.cpp / device_v2.cpp，以临时文件系统替代 FatFS/板卡；不能替代真实介质、ESP32 调度/堆内存或断电验收。旧设备试验脚本显式拒绝 v2，不沿用“关闭页面继续采集”的旧断言。

候选源码/桌面安装不代表固件已部署。烧录、真实 TF 写入/删除及稳定分支合并遵循独立门槛，见任务报告。
