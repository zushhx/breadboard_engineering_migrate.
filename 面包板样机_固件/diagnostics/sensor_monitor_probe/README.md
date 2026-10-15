# 传感器与电流监测诊断候选

独立 ESP-IDF 5.4.2 工程，派生自 `tf_write_probe`，保留其 TF 核心及默认只读门。实际状态只看[项目状态](../../../docs/STATUS.md)，范围见[接入计划](../../../docs/plans/SENSOR_MONITOR_BRINGUP_20261015.md)，接法见[本轮接线表](../../../docs/hardware/wiring/传感器与电流监测接入_2026-10-15.md)。不包含满速采集、FIFO、WiFi 或整机功耗测量。

## 独立构建

从工程根目录执行：

```powershell
& '.\.shell-tools\PowerShell-7.6.6\pwsh.exe' -NoProfile -File '.\.shell-tools\build_sensor_monitor_probe_pwsh7.ps1'
```

支持 `-PrepareOnly`。固定 PlatformIO 6.2.0、espressif32 6.11.0、ESP-IDF 5.4.2；独立输入和输出目录为 `local-data/build/sensor-monitor/`。生成配置值及分区必须与冻结只读基线一致。manifest 保存完整输入哈希、源码提交、工作区状态和产物哈希。构建不连接串口、不上传；PlatformIO 的 upload/erase/program 目标由 guard 阻止。

## 启动与命令

UART 115200。先完成原 TF 等待 5 秒、SDMMC 初始化、容量、目录和参考文件只读检查，再建立新增总线：SPI2 Mode 3 / 1 MHz，CS10、MOSI11、SCK12、MISO13；I2C0 / 100 kHz，SDA16、SCL17，要求外部 3V3 上拉。启动扫描 0x40～0x4F，只接受唯一身份匹配的 INA226，预期历史地址 0x44。扫描不写配置；多个匹配或总线故障时不选择目标。

| 命令 | 行为 |
|---|---|
| `m` | 只读快照：身份、配置、校准、状态及有效数据；读取 INA mask 会清除其转换就绪标记，不等于改写校准 |
| `INIT_MODULES` | 本次 MCU 启动只允许一次初始化尝试；先验证两器件身份，通过后写 IIS3DWB 和 INA226 配置，再回读，不自动重试 |
| `i` / `b` / `l` / `help` | 原 TF 只读查询 |
| `WRITE_TEST <serial> <bytes> <run>` | 与前置 TF 诊断相同的显式、固定 1 MiB 新文件写入及完整回读，另需实机授权 |
| `VERIFY_TEST <run>` | 与前置 TF 诊断相同的只读完整文件校验 |

IIS3DWB WHO=0x7B。初始化先 CTRL1_XL=0 进入 power-down，再 INT1_CTRL=0（本轮不用中断）、CTRL3_C=0x44（BDU/自动递增）、CTRL6_C=0、CTRL1_XL=0xA0（±2g/三轴）。身份、传输、读回配置和数据就绪必须均正确，才按 0.061 mg/LSB 输出三轴快照。1 Hz 主机观察会丢弃绝大多数器件输出，不是满速采集验收。

INA226 manufacturer=0x5449，die=0x2260 或 0x2261。配置 0x4527、R100 按 0.1 Ω、Current_LSB=25 µA、CAL=2048。只在显式初始化时写一次 CONFIG/CAL；其后只读观察，CAL=0、配置不符、传输失败、无转换就绪或运算溢出时明确 INVALID，抑制电流/功率数字。寄存器逐项读取，不保证电压/电流/功率属于同一转换时刻。INA 的 VCC 与测量支路不同，接线保持 TF 直供。

初始化不是原子操作：中途失败时可能已有部分寄存器改写，程序记录阶段并拒绝本次启动再次初始化。保存现场后按恢复方案处理，不用自动反复补校准掩盖供电故障。上电只读也不能保证器件寄存器为出厂值；MCU 复位不会切断模块供电，原配置可能保留。

TF 写门、固定文件格式及无格式化/修复/删除策略见[前置诊断协议](../tf_write_probe/README.md)。本候选新增总线和启动顺序，前置 TF 实测不能自动转移为本候选通过。软件测试不覆盖驱动、电源、接触、DMA/栈、精度或实物接线。

技术依据：[ST DS12569 Rev8](https://www.st.com/resource/en/datasheet/iis3dwb.pdf)、[TI SBOS547C](https://www.ti.com/lit/ds/symlink/ina226.pdf)，以及本机固定 ESP-IDF 5.4.2 公共 SPI/I2C 头文件。
