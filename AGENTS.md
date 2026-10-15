# 命令行约定

- 优先使用 WSL；不可用或需要 Windows 接口时，使用最新稳定版 PowerShell 7。
- 本工程已验证的最新稳定版为 PowerShell 7.6.6（2026-10-12），程序路径：`C:\Users\acer\Desktop\直插式面包板样机\.shell-tools\PowerShell-7.6.6\pwsh.exe`。
- Windows 命令应显式通过上述 `pwsh.exe` 执行。工具的启动程序可能仍是内置的 7.6.5，仅设置工具的 `shell` 参数不能证明实际命令用了新版；以命令内部的 `$PSVersionTable.PSVersion` 为准。
- 管理员 TF 检查使用 `面包板样机_固件/tools/start_tf_boot_check_pwsh7.ps1`，它直接以 PowerShell 7.6.6 启动管理员进程。仅验证命令入口时加 `-VerifyHost`，该模式不读取卡片。
- 只升级命令行环境时，不顺带执行读卡、烧录或其他硬件检查。

