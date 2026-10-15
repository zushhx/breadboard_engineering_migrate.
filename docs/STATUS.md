# 当前状态

更新日期：2026-10-15。当前事实唯一入口。历史测试结果保留其适用条件，不自动延伸至其他固件或接法。

## 当前任务：独立 TF 写入诊断与基础实机验收

工作分支为 `codex/tf-write-diagnostic`，起点为下述工程基线分支的 `ce8dc03`。用户已确认当前卡可清空，豁免本轮卡内资料备份，并追加整批实机授权：指定候选试烧、10 次 1 MiB 写入回读、必要主控复位及复核、失败后恢复冻结只读固件；详见[计划中的授权追加](plans/TF_WRITE_DIAGNOSTIC_20261015.md)。尚未取得 main 合并授权。实机操作结果按实际日志更新。

固件从干净提交 `31dda6f` 构建通过；受控批处理加入后，共 11 项自动化测试通过，本地项目检查通过。生成 SDK 配置值及分区表与冻结基线一致。自行复核发现并修正了测试路径栈帧叠加超过既有主任务栈的问题。[软件检查记录](reports/TF_WRITE_SOFTWARE_REVIEW_20261015.md)保留初始软件阶段的证据与当时未测边界。

随后已按授权试烧并完成：10 次 1 MiB 写入/同步/关闭/完整回读、一次保留卡供电的 MCU EN 复位后 10 份文件复核、两次用户分别确认全断电后的各 10 份文件复核，合计 40 次完整校验均通过。当前保留写入诊断候选在主控上，空闲默认只读。详见[实机报告](reports/TF_WRITE_HARDWARE_20261015.md)和[原始证据清单](reports/TF_WRITE_HARDWARE_EVIDENCE_20261015.json)。未验证写入中突然断电、长期负载或真实采集。

[草稿 PR #2](https://github.com/zushhx/breadboard_engineering_migrate./pull/2) 以工程基线分支为 base，依赖 PR #1；最新 CI 以 [PR 检查页](https://github.com/zushhx/breadboard_engineering_migrate./pull/2/checks)为准。尚无独立源码审查通过证据；Code Review 的 CI 诊断工具本次连接失败，不能记为审查通过。

2026-10-15 19:21，经 COM5 不主动复位的只读复查，卡身份与容量与基线记录一致，根目录两轮读取成功，参考文件四次 SHA-256 匹配；未重新读取 Flash 核实当前镜像，也未重新核对接线。日志：固件本地 `logs/tf_software_capture_controller_20261015_192149_000725.log`。

## 前置工程基线版本

| 项目 | 状态 |
|---|---|
| 本轮目标 | 工程基线版 v0.1.0：目录、上下文、Git、可恢复基线及操作门槛 |
| 工作分支 | `chore/engineering-baseline`；尚未合并 `main` |
| 初始快照 | `09479ff0f3c66d070ba842b17b6c9cab63a58c1a`；标签 `baseline/import-20261015` |
| 正常 TF 基线 | 产物首次封存标签 `baseline/tf-readonly-20261015`；全部输入逐字节检出使用 `baseline/tf-readonly-20261015-inputs-v1`，不是原业务功能发布 |
| 目录迁移 | 原清单 443 项校验通过；后续主动修订在迁移清单中另列 |
| GitHub 远端 | 用户确认改为公开：[breadboard_engineering_migrate.](https://github.com/zushhx/breadboard_engineering_migrate.)；原始备份、私人资料和本机全局规则仓库未上传 |
| main 服务端保护 | 已设置并读回核验：要求 PR、分支保持最新及两项 CI；约束管理员，禁止强推/删除，要求讨论已解决；独立审查人数为 0，用户合并确认另行执行 |
| PR、CI | [PR #1](https://github.com/zushhx/breadboard_engineering_migrate./pull/1) 已建立；最新提交的 CI 结果在 [PR 检查页](https://github.com/zushhx/breadboard_engineering_migrate./pull/1/checks)维护，两项检查均为合并门槛 |
| 本机全局规范 | engineering-policy-1.0.0 已部署，当前会话已重新载入；规则源单独本地 Git 管理 |
| 本轮硬件操作 | 未连接串口、未复位、未烧录、未写 TF 卡；不据此更新实物状态 |
| 本轮工程验证 | 443 项迁移原始清单、冻结文件哈希、文档路径、6 项操作门槛测试及干净检出验证已通过；Windows 冻结构建通过，新产物未试烧；见[整改检查](reports/ENGINEERING_BASELINE_REVIEW_20261015.md) |

## 最后一次已验证的主控程序

当前主控程序为 `tf_write_idf542_4bit_20m`，源码 `31dda6f`，固件 SHA-256 `728e50380e55a08c653142721f064dd5ef9028fe28bb7ed197ef561f10d08ec3`。本轮实机日志已核对身份：ESP-IDF 5.4.2、CPU 160 MHz、四位 SDMMC、请求最高 20000 kHz、启动等待 5000 ms。该频率为软件配置上限，未用示波器测实际 SD 时钟。先前 15:31–15:34 的只读程序仍保存在冻结基线中。

记录接法：主控 3V3 直接连接 TF VDD，VSS 接 GND；CLK38、CMD39、DAT0/40、DAT1/41、DAT2/42、DAT3/47；CMD 与 DAT0～3 各有约 10 kΩ 上拉，SD-CD 不接。其他模块当时断开。

| 能力 | 最后验证结果与范围 |
|---|---|
| 卡片识别及容量 | SU08G，7,948,206,080 字节，与电脑读卡器记录一致 |
| 初始化与读取 | 当前写入诊断候选经过两次用户确认全断电启动、一次保持卡供电的主控 EN 复位后通过 |
| 文件读取 | 本轮 10 个 1 MiB 文件共 40 次完整内容和 SHA-256 校验通过；原有 18,327 字节参考文件在本轮三份日志中共 10 次摘要匹配 |
| 基础写入与同步 | 当前候选 10 次新建文件、1 MiB 写入、同步、关闭和重新打开完整读取通过，总写 10 MiB；关闭后两次用户确认全断电重启后数据匹配 |
| 异常中断与断电保护 | 写入进行中断电、文件系统异常恢复、长期负载未验收，不能由本轮文件关闭后的断电验证推定 |
| 原业务采集与 WiFi | 未迁移到当前诊断程序 |
| 电脑盘符式 USB MSC | 未实现 |
| 全卡完整性、长期运行、整机联调 | 未验收 |

当前候选产物及配置见[候选构建清单](reports/TF_WRITE_BUILD_20261015.json)，当前实机证据见[写入报告](reports/TF_WRITE_HARDWARE_20261015.md)。此前只读正常产物保留在[冻结基线](../baselines/tf-readonly-20261015/README.md)，其当时证据见[旧实板对照报告](reports/TF卡软件实测_2026-10-15.md)。

## 未决事项

- TF 失效的单一技术根因尚未定位。新 SDK 成功前发生过重新接线，SDK/CPU/PSRAM/启动路径也存在多项差异，不能写成 SDK 单独修复已证实。
- 卡座本地去耦缺失、接触电阻与瞬态供电等条件尚未闭环；静态电压正常不足以排除这些因素。
- IIS3DWB、INA226 曾有各自条件下的检查记录；当前 TF 单独接法不代表整机仍保持原连接。见[报告索引](reports/README.md)。
- 原主控 8 MiB Flash 备份及日志保留在固件目录的本地 `backups/`、`logs/`，排除于 Git。备份哈希已复核；未重新执行恢复试烧。

下一步按[路线与验收](ROADMAP.md)推进；每次试烧另取得确认。
