# 独立 TF 写入诊断候选

这是开发候选，不是已验收固件。当前状态见[项目状态](../../../docs/STATUS.md)，授权、验收与回退见[本次计划](../../../docs/plans/TF_WRITE_DIAGNOSTIC_20261015.md)。不替换冻结只读基线，不与旧 Arduino 业务共用源码。

## 构建

从工程根目录使用固定的 Windows PowerShell 7.6.6：

```powershell
& '.\.shell-tools\PowerShell-7.6.6\pwsh.exe' -NoProfile -File '.\.shell-tools\build_tf_write_probe_pwsh7.ps1'
```

`-PrepareOnly` 仅准备输入。构建脚本要求 PlatformIO 6.2.0，固定 ESP-IDF 5.4.2 / espressif32 6.11.0，输出进入 `local-data/build/tf-write/<输入哈希>/<时间>/`。生成 manifest 记录提交、工作区是否有改动、全部源码输入哈希、候选产物和生成配置。生成 SDK 配置值及分区表必须与冻结基线相同，否则不产生成功清单。脚本不连接串口，不调用上传目标。

默认 PlatformIO 上传、擦除和编程目标由本目录 guard 拦截。直接绕过脚本构建会显示 `unrecorded-direct-build`，不作为正式试烧候选。

## 串口协议（仅供获批实机操作时使用）

115200 baud。每条命令独立一行；拒绝额外参数、非 ASCII、过长输入和路径。启动仅初始化卡、挂载、读取目录和参考文件，写入门保持关闭。

| 命令 | 行为 |
|---|---|
| `i` / `b` / `l` / `help` | 卡元数据 / 启动扇区 / 目录与原参考文件 / 帮助，只读 |
| `WRITE_TEST <serial> <capacity> <run>` | 核对卡序列号与容量，创建新目录与文件，写固定 1 MiB，检查同步/关闭，再只读回读 |
| `VERIFY_TEST <run>` | 只读检查现有测试文件，可在主控复位或完整断电后执行 |

`serial` 和 `run` 均为 8 位十六进制，`capacity` 为十进制字节数。运行号转换为大写，文件固定为 `/TFT_<run>/DATA.BIN`。例如当前记录中的卡序列号是 `10044BA5`、容量为 `7948206080`；它们只是历史观测值，实际操作前需重新读取，命令本身不代表授权。

目录存在时立即拒绝，文件以 `FA_CREATE_NEW` 打开。没有格式化、修复、删除、覆盖原文件和自动重试命令。测试中遇到写入/回读失败后关闭写门并锁定本次启动的后续写入；只读复核成功不会解除已经产生的锁定。需要保留日志分析，不能用反复重启继续写入掩盖故障。

文件按偏移 `i` 生成字节：`((i * 37) ^ (i >> 8) ^ ((i >> 16) * 101) ^ 0xA5) & 255`。1,048,576 字节参考 SHA-256 为：

```text
7c79a64ca466f57076b4fa369dd7861cf4657c7b00ed8c1bad0c9c9336cc87b7
```

程序逐字节比较内容，并要求读取所得 SHA-256 与上述固定值一致。`PASS phase=write_sync_close_readback` 表示本次写入流程通过；`PASS phase=read_only_verify` 仅表示本次读取符合预期，不证明过去写入流程通过。具体 FatFS 返回值与最终阶段均打印；短写、同步失败、关闭失败等不计为通过。失败时仍尝试关闭已打开的文件，因此可能完成额外的元数据写入；随后锁定并保留现场。

## 同步依据与限制

代码核对依据是本机已安装的 ESP-IDF 5.4.2 `components/sdmmc/sdmmc_cmd.c`、`include/sdmmc_cmd.h`、`include/sd_protocol_defs.h` 和 `components/fatfs/diskio/diskio_sdmmc.c`。

SDK 的 `sdmmc_write_sectors()` 为同步调用并等待卡就绪，官方 FatFS 的 `CTRL_SYNC` 本身直接返回成功。新适配层在 `f_sync` 排出 FatFS 缓存后，通过公开 host transaction API 发送 CMD13，以最多约 3 秒的就绪等待和每条命令 250 ms 超时检查 READY_FOR_DATA、TRAN 状态和 R1 错误。不能把额外状态检查称为卡内部断电保护或强制刷新 NAND；持久性只能通过单独的实机重启/断电对照验证。

## 软件测试与未测项

`python -m unittest discover -s tests -v` 包含直接编译实际共享核心的 C++ 故障注入测试。Linux 使用 g++/clang++；本机 Windows 的测试编译器固定为 Zig 0.13.0，位于忽略目录 `local-data/tooling/ziglang-0.13.0/`，不影响 ESP32 工具链。缺少主机编译器时测试明确失败，不跳过。

模拟测试不覆盖实际 FatFS/SDMMC 驱动、DMA、真实卡内部行为、供电和实机栈余量。当前没有获批烧录、写卡、主控复位或断电测试，也没有集成异常断电、持续写入或真实传感器采集。旧版 `tools/sd_soak.py` 不适用于本候选。
