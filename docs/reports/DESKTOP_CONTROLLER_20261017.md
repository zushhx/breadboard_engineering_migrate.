# Windows 主控控制台交付

2026-10-17。按[已确认方案及范围调整](../plans/DESKTOP_CONTROLLER_20261017.md)交付本机便携程序。用户要求采集时长、开始/停止、曲线、INA/TF 和故障沿用现有检测页面，因此桌面主按钮只检查连接并用默认浏览器打开原页面。当前结论只在 [STATUS](../STATUS.md) 汇总。

## 交付与版本

- 源码：2672ebd371c3e803a8da13963138d694a019188b，分支 codex/desktop-controller，起点 daabee24e12e18766e12ee3b0d955b9a365f6f6f。后续交付文档提交不改变此产物输入。
- EXE：75776 字节，SHA-256 282cab0578823d96eb007ab6c00b506991bd54ae416c489a9b57d301bdc91f07。干净工作区构建，清单 source_worktree_dirty=false。
- 本机桌面：主控控制台.lnk → 主控控制台/主控控制台.exe，同目录包含 EXE.config、使用说明及构建清单。快捷方式目标、安装后 EXE 和配置哈希均读回核对。
- Windows 原生 WPF/.NET Framework 4.8；编译器 14.8.10001.0、SHA-256 eca02e6b15cde93009a59a3d129d5a77fb5008f2e1cf62cf573b6737c747ecc3，十个框架引用的固定摘要见 desktop-controller/toolchain.json。禁止隐式编译配置/引用，未引入 NuGet、Python 运行时或系统服务。
- Wi-Fi / 移动热点按钮使用微软文档列出的设置 URI。它们打开系统设置，由用户操作网络，不等于主控配网或自动创建 AP。[Microsoft Learn：启动 Windows 设置](https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-settings)。

## 实际检查

独立输出目录为 local-data/desktop-controller/release-2672ebd，普通构建不安装、启动浏览器或访问设备。

1. build.ps1：干净提交下固定编译器/框架检查、桌面 EXE 和 CoreTests.exe 编译通过。完整输入、引用及产物摘要在 build-manifest.json。
2. CoreTests.exe：48 项检查通过。覆盖局域网 URL 约束、旧 UI 链接归一化、状态/卡字段、零剩余容量、JSON/响应上限、HTTP 失败/重定向/登录页、超时不重试、取消、发现结果、大小子网范围及本机设置损坏恢复。所有 HTTP 测试为临时 loopback 模拟服务器，未访问主控。
3. EXE --preview：13 项 UI 操作检查通过，覆盖启动不访问设备、键盘绑定、地址错误/保存、操作互斥、模拟检查/打开、导航、查找和选中地址、最小尺寸。生成七张带模拟标识的 PNG；自行查看中文布局、按钮、信息、四页及查找结果，1160×820/1000×700 无阻断主要操作的布局问题。预览不发 HTTP、不启动外部程序、不写用户设置。
4. install-desktop.ps1：绑定上述 EXE 哈希及干净构建清单，拒绝覆盖已有同名入口；安装复制及快捷方式回读通过。安装后以桌面为工作目录，用安装的 EXE 再次执行离线 UI 检查通过，验证资源嵌入和启动不依赖工程目录。输出在 installed-preview。
5. WSL Python3 执行 tools/project_check.py --local，以及提交 hook 的 checkout 模式检查通过；冻结输入/路径/凭据模式/默认上传门槛未受影响。两份 PowerShell 脚本语法检查、最终 staged diff 空白检查通过。未重复本地全套固件构建。

自行审查了实际最终差异、HTTP/地址/状态数据路径、异步取消、命令和浏览器调用顺序、构建/安装分离及原固件配置无变化。处置包括：拒绝任意 URL/重定向，限制搜索及响应大小；boot 只用于识别协议而不写入显示模型/设置/日志；旧异步搜索回调不覆盖新操作；单实例/重复打开提醒；截图明确为模拟并绘制完整背景。没有独立源码审查通过结论。可用 Code Review 工具提供的是 PR CI 诊断，PR 建立后的返回原文保存在本机 code-review-tool.json；CI 状态以关联 PR 检查页为准，不视为桌面运行或硬件验收。

## 实机边界与恢复

20:05 +08:00，以无代理且不跟随重定向的 HttpClient 对历史地址 172.28.247.119 执行一次 GET /api/status，三秒不可达；当时电脑 WLAN 为 10.100.*。保留 readonly-connectivity.json，不保存 boot 令牌。这不能证明主控断电、热点隔离或其他单一根因。

本轮没有实机检测页加载、局域网完整查找/发现、真实采集、网络切换、跨显示器 DPI、实际浏览器重复页管理或系统设置页面交互验收。网络和浏览器启动路径经源码/微软 URI 文档复核，预览主动不执行这些外部动作。界面检查不替代上述未测项。

桌面程序只有按需 GET；正常启动不查询主控，打开页面后没有后台轮询；不发送采集 POST、不使用串口、不复位/烧录、不改变固件/TF/NVS/Windows 网络。单实例不能阻止用户在其他浏览器或重启程序后另开页面，既有多页面并发采集溢出问题仍未修复。Wi-Fi 名称/密码目前编译在固件中，更换网络及主控 AP 需另立任务。

本次可恢复位置是已保存的源提交和 release-2672ebd 构建产物；已验收固件及更早恢复基线保持原位置。移除软件时先关闭程序，再删除本次创建的快捷方式和便携目录，LocalAppData/BreadboardController 设置与滚动日志按需保留。软件移除及主控恢复没有执行，不称为恢复实测。未合入原 UI 分支或 main，未作固件或对外正式发布。
