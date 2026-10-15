# 模块接入候选：软件检查与自行复核

日期：2026-10-15。任务范围和授权见[接入计划](../plans/SENSOR_MONITOR_BRINGUP_20261015.md)，具体源码、输入、产物 SHA-256 及构建日志位置见[候选构建清单](SENSOR_MONITOR_BUILD_20261015.json)。实物当前状态只在[STATUS](../STATUS.md)维护。

最终候选来自干净提交 `7994b9d57854a2a0b702a8eed083c478b5d75590`；`firmware.bin` 为 362912 字节，SHA-256 为 `db7815d6545ab795d5a6e1b75d5e4a66f3109cd2e1ebf95073ea87bb7b0a2601`。输入聚合哈希和本地构建目录以清单为准。早期 `0965b47` 候选已由包含只读身份查询的新候选替代，未进行过试烧。

## 改动与依据

新建 `diagnostics/sensor_monitor_probe`，不修改原 `tf_write_probe`、原业务驱动或冻结基线。复制的 `tf_test_core.cpp`、`.h` 与前置工程逐字节一致（SHA-256 分别为 `f3d2077e5807836d49ced6e6df011ebbfb3443791fb4921f9307b33fb5bec9ed`、`99745aee6f44a126784e37e7fb8612788759e4d576429665ea84c992aff66b05`）。新增 SPI/I2C 初始化发生在原 TF 只读检查之后；构建配置、分区、CPU、PSRAM、TF 引脚/频率/等待不变。新增初始化调用及负载仍必须经过实机 TF 回归。

两模块身份读回、IIS3DWB 基本三轴快照、INA226 的 CONFIG/CAL/状态和电压电流功率由独立原生 ESP-IDF 驱动处理。旧 Arduino 驱动不直接带入本工程。显式初始化仅一次尝试，后续读取不会自动修复校准。允许当前 TI 文档列出的 die ID 0x2260/0x2261，保留旧实测地址 0x44 作为预期而非盲写目标。

技术核对依据为 [ST DS12569 Rev8](https://www.st.com/resource/en/datasheet/iis3dwb.pdf)、[TI SBOS547C](https://www.ti.com/lit/ds/symlink/ina226.pdf)、[ST 套件原理图](https://www.st.com/resource/en/schematic_pack/steval-mki208v1k_schematic.pdf)及本机固定 SDK 的公开 `driver/spi_master.h`、`driver/i2c_master.h`、`esp_mac.h`。对电流换算采用 0.1 Ω / 25 µA / CAL=2048 假设，尚无本轮实物精度验证。

## 复核发现和处置

这是作者自行复核，不是独立源码审查。检查实际新增代码、与 TF 工程的差异、共享配置/调用顺序、存储写门及恢复路径，发现并处理：

1. 旧整机表把 TF 也放在 INA226 输出下，不适用于用户本轮选择；新接线表保留 TF 直供，仅传感器经过 R100。
2. INA226 CAL=0 时电流/功率寄存器可为零，不能称为有效零读数；代码要求身份、传输、CONFIG、CAL、转换就绪、无溢出同时成立，失败不打印有效电流/功率。
3. 主控复位不使模块断电，不能假设传感器寄存器已复位；IIS 初始化先 power-down，再改轴配置。MCU 复位后只读观察保留状态；真正冷启动后才再进行一次显式初始化。
4. 冷启动后用户回复可能晚于启动横幅；增加 `help` 的只读源码/输入/MAC 查询，避免为了取横幅再复位而混淆冷启动条件。
5. 恢复写入必须先确认 MAC；新增恢复入口在写之前调用只读身份核对，不能等写后输出 MAC 才判定设备。
6. 新 app 超过旧批处理 0x60000 前缀覆盖范围，不能直接复用旧快照长度；本入口保存 0x70000 字节并检查候选所有受影响扇区均被覆盖。恢复只写 0x10000 之后的备份 app 范围，保留 NVS/OTA/分区/bootloader；本轮未实际恢复。

Code Review 插件的 `pull_requests_checks` 在 PR #3 返回 “Can't connect to GitHub. Try again.”；它仅提供 CI 诊断，不是源码独立审查。不能因插件已安装或返回失败就记录审查通过。草稿 PR 为 [#3](https://github.com/zushhx/breadboard_engineering_migrate./pull/3)。

## 已执行的软件验证

- PowerShell 7.6.6、PlatformIO 6.2.0、ESP-IDF 5.4.2 / espressif32 6.11.0 构建；具体通过产物及干净工作区来源以候选清单为准。生成 SDK 配置值与冻结基线无差异，生成分区表哈希相同；构建产物中的新 bootloader 不用于试烧。
- `python -m unittest discover -s tests -v`：19 项通过。其中新增实际换算/有效性逻辑测试，及缺少批准、错误范围、无效硬件回复、恢复前设备身份核对的故障路径测试；均不连接硬件。
- 本地项目检查核对冻结清单、迁移原件、文档路径、Git 索引、上传阻挡和本地备份/全局规范；检查记录保留本地上下文核查目录。`sensor_monitor_batch.py --prepare-only` 已验证原 TF 候选、模块候选和依赖文件，不打开串口。
- 原始源码对象中，模块函数本身栈帧最大 176 字节，快照为 96 字节；来自本机 Xtensa objdump 自行检查。此局部观察不代表完整调用链最大栈深度或实机栈余量通过，原 TF 核心/大缓冲分配未变。
- PR 的 CI 状态见[检查页](https://github.com/zushhx/breadboard_engineering_migrate./pull/3/checks)，不以旧提交 CI 代替最新提交。19 项为本地软件测试数量，不是实机验收次数。

## 未测与下一步

本节记录试烧前的软件交付边界：当时尚未连接串口、复位、试烧或在新组合写 TF，不能把构建和软件测试当作模块验收。后续已按具体授权执行首次试烧，在 IIS 身份门失败并恢复原 TF 固件，详见[实机报告](SENSOR_MONITOR_FIRST_TRIAL_20261015.md)；以该报告和当前状态页为准。用户确认接好仅证明人工操作反馈，不能代替功能验收。

构建和批准入口分离；具体候选获批后才执行合并批次。模块快照不是 26.667 kHz 满速数据，不验证 FIFO、无丢样、频响、六姿态精度、长期负载、断电恢复或整机功耗。若失败，保存证据并按已批准恢复范围处理，停止叠加修改。
