# TF 写入诊断软件检查与审查记录

日期：2026-10-15，时区 Asia/Shanghai。适用源码提交：`31dda6f23cab21f9a07330a829bae747211334b0`。后续仅文档记录提交不改变这个候选的源码、输入或产物身份。

## 交付与证据

用户授权仅为[开发计划](../plans/TF_WRITE_DIAGNOSTIC_20261015.md)中的软件开发、构建、测试和审查。本轮开发未连接串口、复位、烧录、写 TF 卡或修改供电接线。此前 19:21 只读检查属于前一个操作，不是本候选的实机验收。

源码工程：`面包板样机_固件/diagnostics/tf_write_probe/`；构建入口：`.shell-tools/build_tf_write_probe_pwsh7.ps1`。产物与全部输入哈希记录在[原始构建清单](TF_WRITE_BUILD_20261015.json)。完整构建日志保留在该清单产物同级的本地 `build.log`。

| 项目 | 结果及条件 |
|---|---|
| 干净提交构建 | Windows PowerShell 7.6.6、PlatformIO 6.2.0、ESP-IDF 5.4.2、espressif32 6.11.0；87.84 秒，构建通过 |
| 源码输入 SHA-256 | `c2e97347d0d7558cc514c5e90f95201423b27bb57b95f1b84a63bb30c5281e0e` |
| firmware.bin | 317,456 字节；`728e50380e55a08c653142721f064dd5ef9028fe28bb7ed197ef561f10d08ec3` |
| 生成配置 | 与冻结只读配置逐项比较，所有 `CONFIG_` 值无差异；诊断副本换行为 LF，未改冻结原件 |
| 分区表 | 与冻结正常产物哈希一致：`1d9cca96de0fe07ad7fc0648b9878ddecd9ce565e38b589ad20fea698ed4c80c` |
| 链接器报告 | 静态 RAM 26,968 / 327,680 字节；程序占用 317,060 / 3,342,336 字节。不是实测堆或栈余量 |
| 自动化测试 | `python -m unittest discover -s tests -v`，8 项通过；Windows 主机编译器 Zig 0.13.0，ESP32 编译器未替换 |
| 项目检查 | `python tools/project_check.py --local` 通过，冻结文件/迁移内容、链接、索引敏感文件、上传 guard、原主控备份和全局规则校验通过 |
| 原始软件测试记录 | 本地 `local-data/tf-write-native-tests.log`、`local-data/tf-write-final-project-check.json`、`local-data/tf-write-stack-frames.txt` |

主机测试编译的是固件实际使用的 `tf_test_core.cpp`，以故障注入 Store 检查完整流程。覆盖卡身份不符、目录已存在/查询错误、目录创建/文件新建失败、短写、写失败、同步失败、写关闭失败、重新打开失败、长度不符、内容损坏、短读、读失败、读关闭失败、哈希各阶段失败和错误摘要；确认失败锁定、只读验证不解除已锁定状态、所有出口关闭写门。解析器覆盖容量溢出、路径、额外参数、控制字符、行溢出与 CRLF。上传 guard 用模拟 SCons 目标验证，没有执行真实上传目标。

另外由原生测试程序调用实际数据生成函数导出 1 MiB 文件，再用 Python hashlib 独立计算 SHA-256，与固定参考值 `7c79a64ca466f57076b4fa369dd7861cf4657c7b00ed8c1bad0c9c9336cc87b7` 一致。故障注入的哈希接口是模拟的，不冒充已在 ESP32 上运行 mbedTLS。

## 审查方式、发现与处置

本记录为作者自行复核实际 diff、冻结配置及 SDK 源码，并结合故障注入和编译产物检查；不是独立审查。已建立[草稿 PR #2](https://github.com/zushhx/breadboard_engineering_migrate./pull/2)，base 为 `chore/engineering-baseline`，依赖尚未合并的 PR #1。CI 实时结果由 [PR 检查页](https://github.com/zushhx/breadboard_engineering_migrate./pull/2/checks)维护，不把旧提交的通过转移到新提交。

调用可用 Code Review 插件的 PR 检查诊断工具时返回 `Can't connect to GitHub. Try again.`。这个工具提供 CI 诊断读取，并非独立源码审查；调用失败不计为通过。GitHub 连接器能够建立 PR，但也不据此声称自动源码审查已经启用。

自行复核的重要处置：

1. **栈溢出风险**：首个软件候选 `9cf53ac` 的 ELF 显示 `testCommand` 栈帧 4,512 字节，嵌套的 `Runner::run` 为 4,176 字节，二者已超过既有 8,192 字节主任务栈。该候选未试烧、已废止。提交 `31dda6f` 将 FIL 适配器和 4 KiB 工作缓冲移至静态区；新 ELF 两个栈帧分别为 176 和 80 字节。保持既有任务栈、PSRAM 与启动配置。此检查不等于完整动态栈深度验收。
2. **同步语义**：核对本机 IDF 5.4.2 源码，官方 SDMMC 写扇区已同步等待卡就绪，官方 `CTRL_SYNC` 为无操作返回成功；不能把这个返回值简单认定为 SDK 缺陷。新适配层在 FatFS 排出缓存后额外检查 CMD13 传输错误、R1 错误、READY_FOR_DATA 和 TRAN，并设超时。不声称已经验证卡内部持久性。
3. **失败不误报**：检查写入实际接受字节数（含短写最后一块）、同步、关闭、回读及固定 SHA-256；只读复核与写入流程输出不同阶段名。失败仍尝试关闭已打开文件，可能追加元数据写入，随后关闭写门、保留文件并锁定后续写入。
4. **原数据保护边界**：新目录拒绝重名、文件 CREATE_NEW，不提供格式化/修复/删除，也不接受任意路径。软件写门不能隔离 FAT 元数据损坏风险；先备份或使用备用卡仍是实机前提。

## 未测试与恢复

未测试实际写卡、FatFS/SDMMC 实物行为、DMA、完整动态栈、断电后文件持久性、异常中断、持续负载或传感器集成。卡片选择、卡数据备份和具体烧录/写卡操作仍需准备并单独确认；本记录不是这些操作的批准。

冻结正常基线保持完整。候选与正常产物分开存放；试烧后恢复固件使用冻结基线并另取确认，恢复方式尚未在本轮实际执行。主控 Flash 备份不包含 TF 文件，回退固件不能回滚 TF 内容。异常断电与持续写入按后续单独实机计划进行。
