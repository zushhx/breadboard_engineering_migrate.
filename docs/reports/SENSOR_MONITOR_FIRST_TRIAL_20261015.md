# 模块首次试烧失败与受控恢复

日期：2026-10-15，原始批次目录 `local-data/hardware/sensor-monitor/20261015_210220/`。当前状态见 [STATUS](../STATUS.md)，范围见[接入计划](../plans/SENSOR_MONITOR_BRINGUP_20261015.md)，逐文件大小/哈希及机器汇总见[证据清单](SENSOR_MONITOR_HARDWARE_EVIDENCE_20261015.json)。

## 版本、条件与授权

- 操作提交 `2e77d560cb74fd89561f0b96233822a71c7ab5be`，干净工作区；CI [run 38738944266](https://github.com/zushhx/breadboard_engineering_migrate./actions/runs/38738944266) 完成且结论 success。
- 候选源码 `7994b9d57854a2a0b702a8eed083c478b5d75590`，固件 362912 字节，SHA-256 `db7815d6545ab795d5a6e1b75d5e4a66f3109cd2e1ebf95073ea87bb7b0a2601`。构建清单的 `hardware_tested=false` 是构建时的记录，此后试烧结果以本报告为准。
- 用户回复“已按表接好，可以准备实机检查”，再对 `call_a48fce6288114d8c980e763df4e46b03` 回复“确认，整轮执行”；批准记录保存在本地 `local-data/approvals/sensor-monitor-20261015.json`。
- Windows PowerShell 7.6.6，主机 Python/串口工具为现有 PlatformIO 环境；实际 esptool 4.11.0，其脚本哈希在批准文件清单中。ESP-IDF 5.4.2、CPU 160 MHz、PSRAM 关闭、原分区、四位 SDMMC 请求最高 20 MHz / 等待 5 秒、TF 3V3 直供。SPI2 Mode3 / 1 MHz、CS10/MOSI11/SCK12/MISO13；INA I2C SDA16/SCL17/100 kHz。

## 观察和停止位置

先通过 COM5 核对 MAC `44:1b:f6:81:91:10`、8 MiB Flash，保存 458752 字节前缀快照（0x0～0x70000），核对原 bootloader、分区及原 TF app 字节后，仅在 app0 的 0x10000～0x68FFF 扇区写入候选。esptool 写入哈希校验通过，启动横幅的源码和输入哈希与候选一致。

| 检查 | 实际观察 | 有效范围 |
|---|---|---|
| TF | 卡 SU08G，序列 10044BA5，7948206080 字节；挂载/目录通过，候选启动及显式查询合计 4 次参考文件 PC_MATCH=1 | 仅候选下基础读取；未进行新文件写入或冷启动回归 |
| INA226 | 两次在 0x44 读到 maker=5449、die=2260，传输成功 | 只证明身份通信；尚未写 CONFIG/CAL、未验证电流 |
| INA 初始状态 | CONFIG=4127、CAL=0000，程序明确 INVALID，未打印有效电流/功率 | 初始化前状态，不据此认定校准回归或传感器不耗电 |
| IIS3DWB | 启动快照及初始化前复核均 who=00，预期 7B；所读 CTRL/STATUS 也为 00 | 身份失败；ESP_OK 是主机 SPI 事务完成，不是外设身份通过 |
| 初始化门 | 输出 `INIT failed identity; no configuration writes` | 无 SPI/I2C 配置写入，无自动重试 |

随后批次报失败并进入已批准恢复，没有执行 60 秒观察、1 MiB 新文件、集成 MCU 复位、两次完整断电或移动响应检查。没有为凑次数继续试验。

驱动输出过 I2C pull-up 提示，但同轮 INA 身份读取成功；没有测量上拉电阻或波形，不能仅据该提示认定上拉缺失。

## 恢复结果

恢复前再次通过 flash_id 确认 MAC，再把原快照 0x10000～0x70000 的 393216 字节写回 app 区域，保留 NVS、OTA、分区和 bootloader。esptool 写入哈希通过。恢复文件 SHA-256 为 `98d8a94c78800efc2b1c56f5934a3f80b8b9469c46aec82b2c018d519fed06ef`。

恢复后的启动横幅为已验收 TF 源码 `31dda6f23cab21f9a07330a829bae747211334b0`，固件原校验值 `728e50380e55a08c653142721f064dd5ef9028fe28bb7ed197ef561f10d08ec3`；容量、目录、启动两次及显式查询两次参考文件哈希均通过。本轮候选＋恢复总计 8 次参考文件 PC_MATCH=1，未写 TF。恢复未进行额外完整 Flash 重新读回，也不代表新模块恢复工作或技术原因已找到。

快照 SHA-256 `ceaa40d70e37c62884638f86a6682e5744bbb9ccd1f30ce885f60eac5584e464`，含私人 NVS，只留本地；普通 Git 仅保存校验清单和必要观察。

## 故障分析与下一步

用户按要求报告 INA IN−、绿板 VDD、绿板 VDDIO 各对 GND 为 3.26 / 3.26 / 3.26 V，未记录仪表型号/精度。这只覆盖这些接点的静态读数；蓝板排线后的 VDD/VDDIO、真实共地、瞬态与信号接触尚未独立核对。

已自行核对旧 Arduino 读法和本轮 IDF 读法：Mode3、MSB first、读地址 bit7=1、命令后数据字节及项目 IO10/11/12/13 对应关系一致，暂未发现明确软件错误。软件时序/驱动配置仍是未排除变量，不能凭历史检查或“全零”判定用户接错或传感器损坏。

下一步先看当前信号端点、绿板 SDO 与 OSDO 区别、配套 12 芯排线插法及共地，必要时再测排线后蓝板供电；得到证据后每轮只改变有明确假设的变量。未经新结论不盲目换 SDK、改 TF 电源或反复试烧。具体恢复已经完成，首次接入验收未完成。
