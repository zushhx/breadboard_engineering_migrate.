# 直插式面包板样机

智能刀柄振动监测原型的工程工作区。此页仅提供导航；当前事实、目标、流程分别在下列入口维护。

## 从这里开始

1. [当前状态与验收边界](docs/STATUS.md)
2. [下一版目标与任务](docs/ROADMAP.md)
3. [工程、Git 与发布流程](docs/WORKFLOW.md)
4. [项目操作约定](AGENTS.md)
5. [TF 访问异常复盘](docs/reports/TF_ACCESS_POSTMORTEM_20261015.md)

## 文件分区

| 目录 | 用途 |
|---|---|
| `面包板样机_固件/` | 固件源码、诊断实验和配套工具；先读其 README |
| `baselines/` | 已验证产物、冻结构建输入和证据，按基线编号保存 |
| `docs/reports/` | 按日期保存的原始测试报告与事件复盘 |
| `docs/hardware/` | 接线和采购资料，使用前核对日期与适用接法 |
| `docs/history/` | 旧方案、旧业务说明、旧记忆；按需追溯 |
| `tools/` | 工程检查、冻结基线构建和本地 Git 辅助工具 |
| `.shell-tools/` | 已验证 Windows 命令入口及本地 PowerShell 路径 |
| `references/` | 本地数据手册、原设计参考和素材 |
| `deliverables/` | 导出交付物 |
| `local-data/` | 不入 Git 的快照、安装包和生成缓存 |

## 安全的日常检查

在 WSL 的项目根目录执行：

```bash
git status --short --branch
python3 tools/project_check.py --local
git diff main...HEAD --stat
```

上述检查不连接串口、不复位设备、不写 TF 卡。固件构建和硬件操作见[固件入口](面包板样机_固件/README.md)。

文件迁移前后路径及内容校验见 [MIGRATION.json](docs/MIGRATION.json)。历史文件内的“当前”指写作当时，状态判断从本页第一项开始。
