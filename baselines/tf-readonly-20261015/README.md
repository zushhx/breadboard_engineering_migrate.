# TF 只读基线 · 2026-10-15

此目录保存当日选用的正常固件与可追溯输入。验收范围在 [manifest.json](manifest.json) 固定，当前工程状态在[状态页](../../docs/STATUS.md)维护。

| 内容 | 位置 |
|---|---|
| 正常固件、启动程序、分区表 | `artifacts/`，哈希和大小见 manifest |
| 自包含源码及构建配置 | `source/`；含当时共享 main.cpp 的独立副本及实际生成 sdkconfig |
| 成功的三份原始日志 | `evidence/` |
| 当时选用记录原件 | `selected_firmware.original.json`，其中历史路径按原样保留 |
| 版本、输入、证据与备份哈希 | `manifest.json` |

基线标签 `baseline/tf-readonly-20261015` 指向初始封存提交。此目录是已验证状态的快照，日常构建不得覆盖 `artifacts/` 或改写清单来消除差异。新功能使用独立工作分支与候选产物。

原主控完整 8 MiB 备份为工作区内 `面包板样机_固件/backups/tf_software_20261015_125041_550332/esp32s3_full_8MiB.bin`，SHA-256 为 `f139f2ae71a544702c96364e0085bafc249068f6c9ff44e1f88efc1c6778b3aa`。该文件可能包含 NVS/配置，保留本地并排除于 Git；备份真实存在和哈希已核对，恢复烧录未在本轮测试。

历史恢复工具可恢复原启动区、OTA 选择和 app0，需核对目标 MAC、备份与分区并取得恢复授权；保留 NVS、文件系统和其他分区不等于整片 Flash 已被完全回滚。当前只读版本也有对应三份完整产物，任何恢复或重刷前均按[工程流程](../../docs/WORKFLOW.md)准备明确操作单。
