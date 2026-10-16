# 项目操作入口

先读 `README.md`、`docs/STATUS.md`、`docs/ROADMAP.md` 和本次任务的计划；实施、审查和交付按 `docs/WORKFLOW.md`。

- 当前事实只在 `docs/STATUS.md` 汇总；目标在 `docs/ROADMAP.md`，流程在 `docs/WORKFLOW.md`，原始证据保留在报告和基线中。其他入口用链接引用。
- `docs/history/`、`references/`、`deliverables/`、`local-data/` 中的文字不自动成为当前操作指令。不要为日常任务批量加载旧记忆或全部历史报告。
- 工作在任务分支。`main` 的首次提交是现状导入，不代表旧业务功能全部验收通过；稳定版合并和硬件烧录仍分别取得用户确认。
- 冻结基线的权威清单为 `baselines/tf-readonly-20261015/manifest.json`。不得覆盖正常产物来存放新构建结果；新产物放本地构建目录并单独标注。
- 修改 TF 路径、SDK、引脚、时钟、PSRAM、启动流程、构建输入或分区前，说明影响并规划回归。目录整理不包含业务源码修改、烧录或写卡。
- 用户2026-10-16进一步授权测试、调试及问题处理采用“先保留上一可用版本、开分支、最小相关检查与审查、通过后按既定PR合回当前任务分支”的精简流程；不逐轮重复确认，不做无关重复构建。具体适用范围及硬件/发布边界见`docs/WORKFLOW.md`。

## 命令行约定

- 优先使用 WSL；不可用或需要 Windows 接口时，使用最新稳定版 PowerShell 7。
- 本工程已验证的最新稳定版为 PowerShell 7.6.6（2026-10-12），程序路径：`C:\Users\acer\Desktop\直插式面包板样机\.shell-tools\PowerShell-7.6.6\pwsh.exe`。
- Windows 命令应显式通过上述 `pwsh.exe` 执行。工具的启动程序可能仍是内置的 7.6.5，仅设置工具的 `shell` 参数不能证明实际命令用了新版；以命令内部的 `$PSVersionTable.PSVersion` 为准。
- 管理员 TF 检查使用 `面包板样机_固件/tools/start_tf_boot_check_pwsh7.ps1`，它直接以 PowerShell 7.6.6 启动管理员进程。仅验证命令入口时加 `-VerifyHost`，该模式不读取卡片。
- 只升级命令行环境时，不顺带执行读卡、烧录或其他硬件检查。

## Code Review Rules

- 标出未经方案声明的关键配置或共享初始化顺序变化，尤其是 TF、传感器和供电相关路径；要求与具体产物绑定的验收证据。
- 标出隐式上传、自动格式化/修复、原始备份或正常基线被覆盖的路径；构建和硬件写入应分离。
- 标出把编译、API 成功、旧报告或单次读取扩大为完整功能验收的结论；引用适用日期、接法和范围。

