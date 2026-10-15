# 固件与工具入口

先查看[项目当前状态](../docs/STATUS.md)。此页只维护操作入口，不另维护一份功能通过清单。

## 当前基线

[TF 只读冻结基线](../baselines/tf-readonly-20261015/README.md)保存已验证产物、实际生成配置、源码与三份证据。清单中 `source/` 可独立构建，正常产物始终保存在 `artifacts/`。

普通检查与构建入口：

```bash
# WSL 项目根目录：只检查文件与 Git 状态
python3 tools/project_check.py --local

# 查看冻结输入并准备构建；不访问串口
python3 tools/build_tf_baseline.py --prepare-only
```

Windows 工具链构建从 WSL 调用：

```bash
'/mnt/c/Users/acer/Desktop/直插式面包板样机/.shell-tools/PowerShell-7.6.6/pwsh.exe' -NoLogo -NoProfile -File 'C:\Users\acer\Desktop\直插式面包板样机\.shell-tools\build_tf_idf_probe_pwsh7.ps1'
```

脚本内部核对 PowerShell 7.6.6，以冻结输入构建，在独立本地目录输出文件，绝不自动上传。实际执行结果见[工程整改检查记录](../docs/reports/ENGINEERING_BASELINE_REVIEW_20261015.md)。

## 历史业务与实验

独立写入诊断的软件候选见 [tf_write_probe](diagnostics/tf_write_probe/README.md)，开发与验收范围见[本次计划](../docs/plans/TF_WRITE_DIAGNOSTIC_20261015.md)。构建入口为工作区根目录 `.shell-tools/build_tf_write_probe_pwsh7.ps1`；不执行烧录或写卡，不替换冻结基线。

`src/`、`include/`、`webui/` 和根 `platformio.ini` 是原 Arduino 业务程序；`diagnostics/` 与其他 `platformio.*.ini` 保留诊断实验。原业务的旧说明已移到[历史固件 README](../docs/history/firmware-README.before-engineering.md)，其中的“当前”和烧录步骤均属当时语境。

默认 PlatformIO 上传将被阻止，以免把旧业务程序覆盖到主控。需要开发业务功能时，按[工程流程](../docs/WORKFLOW.md)建立任务、审查并取得具体试烧确认。

## 本地硬件工具

- `.shell-tools/run_tf_software_trials_pwsh7.ps1` 是工作区根目录下的历史串口/备份/烧录入口；写设备动作需要明确候选与批准记录，不能因编译完成自动执行。
- `tools/start_tf_boot_check_pwsh7.ps1 -VerifyHost` 只验证管理员 PowerShell 入口；普通模式会读取卡片，不能将二者混用。
- 固件目录的 `logs/`、`backups/` 保留本地原始数据并排除于 Git；正常基线使用单独收录的只读证据。
