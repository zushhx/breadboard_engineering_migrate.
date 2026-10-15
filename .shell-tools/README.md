# 工程 PowerShell

当前版本：PowerShell 7.6.6，2026-10-12 核对为官方最新稳定版。

普通 Windows 命令及管理员检查统一使用本目录中的 `PowerShell-7.6.6/pwsh.exe`。WSL 可用时仍优先使用 WSL。

普通命令示例（从工程目录执行）：

```powershell
& '.\.shell-tools\PowerShell-7.6.6\pwsh.exe' -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'
```

管理员读卡检查入口：`面包板样机_固件/tools/start_tf_boot_check_pwsh7.ps1`。它直接用新版申请管理员权限；加 `-VerifyHost` 时只验证 PowerShell 版本，不读取卡片。原管理员脚本即使被旧的 `powershell.exe` 启动，也会先转到本工程的 7.6.6。

新版运行时由 WinGet 官方源下载的微软 MSIX 包提取到工程目录，未注册为系统应用。已核对官方 SHA-256、微软程序签名及实际运行版本。它不修改系统自带的 Windows PowerShell 5.1，也不替换 Codex 管理的运行时。工具启动程序可能仍是内置 7.6.5；项目命令显式转交 7.6.6 执行。

来源：[PowerShell 7.6.6 官方发布](https://github.com/PowerShell/PowerShell/releases/tag/v7.6.6)。

