# IIS3DWB 真实采集记录候选

由7994b9d模块诊断候选独立复制，不修改前置候选。当前状态看[STATUS](../../../docs/STATUS.md)，范围见[方案](../../../docs/plans/SENSOR_TF_RECORDER_20261015.md)，接线沿用[接线表](../../../docs/hardware/wiring/传感器与电流监测接入_2026-10-15.md)。尚未取得实机通过证据。

构建入口：工程根目录执行 `.shell-tools/build_sensor_tf_recorder_pwsh7.ps1`，需显式使用项目PowerShell7.6.6；支持 `-PrepareOnly`。固定PlatformIO6.2.0/espressif32@6.11.0/IDF5.4.2，CPU160 MHz、PSRAM关闭、分区不变，产物在local-data/build/sensor-recorder，不覆盖原产物。CI只构建，upload/erase等隐式设备目标由独立guard拒绝。

## 运行协议

上电保持原TF等待5秒及只读识别、参考文件校验，再建立SPI2 Mode3/8 MHz、I2C100 kHz读取身份，不写模块配置，不自动录制。TF引脚、四位SDMMC20 MHz、直供与上拉不变，INA仍只测传感器支路。提高SPI时钟、FIFO、双核任务、有界缓冲和TF持续写入属于新变更，需本候选实测。

UART115200命令：

| 命令 | 行为 |
|---|---|
| `help` / `i` / `b` / `l` / `m` | 既有身份/TF/低速模块只读查询；闲置时传感器未启用属预期 |
| `REC_START <8位大写十六进制卡序列> <卡容量字节> <8位大写十六进制run> <1..300秒>` | 核对卡身份/容量、目录不存在、可用空间、内存、模块；显式配置后新建唯一REC_run/DATA.BIN并记录；另需具体候选实机授权 |
| `REC_STOP` | 仅在正在记录时接受，停止进FIFO、排空、同步关闭并回读；闲置时不产生动作 |
| `REC_VERIFY <run>` | 完整只读结构/块CRC/计数/尾部检查，返回SHA-256；复位后也可使用 |

保留旧WRITE_TEST/VERIFY_TEST兼容入口，仍需对应写卡授权，本轮批处理不调用。记录中app_main同步作为唯一FatFS写入者，不同时处理其他命令；只接收REC_STOP。失败锁定本次启动，留存文件，不自动重试、清卡、格式化或补校准。

FIFO任务在CPU1以1 ms tick服务，一次SPI事务只读一帧TAG+XYZ，不假设跨0x7E自动回绕；CPU0的app_main写文件、另一任务约10 Hz读INA。40个448帧块的内部内存池约127 KiB，启动时分配不足即拒绝，未开启PSRAM。一个tag仅核对传感器类型0x02，保存原始计数/校验位；它不是可靠全局样本序号。SPI未提供XYZ负载校验，文件CRC只检测软件生成块之后的损坏，不能证明排线读取的每一位原始值都正确。

INA CONFIG4527/CAL0800仅首次显式记录准备写一次；之后只读验证。一次未就绪标invalid，配置/身份/溢出/传输异常终止；整轮至少一条有效INA记录。各寄存器并非原子同步快照。电压、电流精度未验收，不补偿比例。

## I3REC001 文件

整数小端，结构packed。文件头224字节，内容按 `record_format.h`：magic、头长/版本、卡序列/容量、目标时长、ODR26667、SPI8000000、61微g/LSB、flags=1（INA精度未验收）、源码/输入哈希、MAC、接线版本、100000微Ω、25000 nA/LSB、625000 nW/LSB、实际INA地址，最后为前220字节CRC32。

每块48字节头：`magic=0x314B4C42, type, sequence, payload_bytes, count`五个uint32，`first_us,last_us,first_sample`三个uint64，CRC32一个uint32。CRC覆盖前44字节头及payload，标准IEEE/zlib CRC32。

- type1：1..448帧，各为原始tag+XYZ小端有符号16位，共7字节。块内时间是读取窗口，样本时间按序号/标称ODR估算，不是独立硬件时间戳。
- type2：24字节，uint64主控时间及八个uint16：config/cal/mask/shunt/bus/current/power/valid。valid=0时不输出伪零电压电流。
- type3：68字节尾部，样本数/start_us/stop_us三个uint64，随后INA总数/无效数、错误位、近量程饱和轴值计数、FIFO/队列高水位、写入/同步最大耗时、最小堆及两任务最小剩余栈共11个uint32。错误位为SPI1/FIFO2/tag4/queue8/INA16/write32/config64/stop128/rate256/memory512。并非每个准备阶段失败都会生成尾部；串口与批次结果同样是验收证据。

正常完成要求采集、排空、关闭、完整回读结构/CRC及写入/读回SHA一致。±5%计数/主控时长只作预先确定的功能检查，不是采样时钟校准。尾部写入前汇总指标不含尾部自身最终写入/同步；串口汇总可能包含更大最终耗时。缺失尾部或任意损坏均判未完成/失败，解析器不修复输入。

## 导出

板上完整回读，串口只回摘要。全部冷启动检查后，在全断电状态把卡移到电脑读卡器，复制所需REC目录一次。用 `python tools/parse_sensor_record.py <DATA.BIN> --output <新目录> --csv --plot` 校验/导出；默认只打印JSON，`--plot`需matplotlib，`--csv`文件可能远大于原始二进制。对照板上摘要，真实文件波形必须由真实导出文件产生。

## 恢复

使用已保留7994b9d产物和[构建清单](../../../docs/reports/SENSOR_MONITOR_BUILD_20261015.json)，实机批处理先备份覆盖本次写入的完整应用扇区并校验当前app/boot/分区，只写app0。失败保存现场，按本轮具体授权恢复应用备份，校验原TF读取。未在本软件阶段执行烧录或恢复。

寄存器依据为ST DS12569 Rev8（2025-07），实现为本项目自有最小接口，未引入浮动版本ST驱动源码；官方[数据手册](https://www.st.com/resource/en/datasheet/iis3dwb.pdf)及[驱动接口](https://github.com/STMicroelectronics/iis3dwb-pid)仅供核对。
