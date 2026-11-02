# 分配失败取证候选

日期：2026-11-02。候选源码 `6ef066135591c52d8ae204121a731c63d6ecf3c3`；实机验证待具体试烧批准。故障原因仍待取证。

## 改动与证据范围

仅在 R240 诊断服务首次启动注册 ESP-IDF 单槽分配失败回调。会话开始前开窗，socket 关闭后关窗并输出；固定 8 条滚动记录含 MCU 时间、请求字节、caps 及 heap API 函数名地址，并记录总数、覆盖数和饱和标记。原发送期限、队列、SDK、分区、TF 与采集路径保持原参数。该地址提供 heap API 入口线索，调用方栈仍未采集；全局窗口内其他任务的失败也会记录。零事件的适用范围为活动窗口内经过该 SDK 钩子的路径。

本地 SDK 5.4.2 的 `components/heap/heap_caps.c` 使用单个静态 callback 槽，无 getter，NULL 注册被拒绝。项目及 SDK 生产组件搜索未发现其他注册调用；测试目录存在测试注册。回调无动态分配、串口、堆查询或字符串解引用，专用 SAFE 临界区保护跨核/ISR。

## 构建与核验

- 干净提交构建成功，171.47 秒；默认空凭据。输入 SHA-256 `ef795ca05fa5887f90dcf9645ea65d559b3790a02bde90c188fc44b8e64cdf8d`。
- 应用 1149504 字节，SHA-256 `4d92400a3a9b44adfa95f864c0ee8b115a25de640bedc67376e0f946493de2b5`。
- 清单：`local-data/build/sensor-web/ef795ca05fa5887f/20261102_163818_308516/build_manifest.json`。生成 SDK SHA `c8b9163660bf87d2f38639536994c92b721a8efaf266a2c2986696ebbf2678d5` 与前轮 3b 及恢复 F 一致；分区、空凭据校验通过。
- `rate240_f_trial.py --phase prepare` 验证源码输入、产物、恢复镜像及操作范围通过；证据 `local-data/validation/wifi-tcp-nodelay/allocation-prepare-20261102.json`，hardware_accessed=false，备份范围 0..0x129000（1216512 字节）。
- 84 项 `test_rate240_*.py` 宿主测试通过。生产记录逻辑抽取覆盖禁用、滚动、重置、饱和、注册失败；监听测试验证开关窗口顺序及启动失败回收。证据 `allocation-tests-20261102.log`。
- 独立 agent 静态审查最终实现、SDK 及测试，未发现阻塞问题；主 agent 运行测试、构建并复核实际 diff。checkout 提交钩子通过；全量 `--local` 原有 355 项历史文件/全局规则源问题仍保留，见 `allocation-project-check-20261102.json`，整体检查结果为失败。

目标 ELF：allocationFailed 位于 `.iram0.text`（0x4037557c，133 字节），直接调用的 xPortInIsrContext、xPortEnterCriticalTimeout、esp_timer_get_time、vPortExitCritical 均位于 IRAM；计时依赖 systimer_hal_get_counter_value、systimer_ticks_to_us 亦在 IRAM。allocationTrace 位于内部 `.dram0.data`，224 字节；锁 8 字节；注册标志位于 `.dram0.bss`，1 字节。符号、段及回调反汇编保存在上述 validation 目录的 `allocation-elf-*`、`allocation-callback-disassembly-20261102.txt`。

PlatformIO 静态 RAM 87940 字节，相对 3b 的 87716 增加 224 字节；对象自身合计 233 字节，链接对齐使 `.dram0.heap_start` 从 0x3fcb23a8 后移到 0x3fcb2588，差 480 字节（含 IRAM 对齐占用 256 字节）。前轮低堆条件下这属于实质观察干扰；后续把新候选结果绑定该开销，避免仅据新失败频率判断旧候选原因。真实双核/ISR 行为、运行栈余量、记录开销与吞吐仍待实机验收。

## 试烧与恢复

当前准备范围：原主控 MAC44:1b:f6:81:91:10，仅应用区试烧。USB 用于控制/日志，数据测试走 Wi-Fi TCP；先 RAM30，再合成 TF30，按失败即取证并安全恢复的既有流程推进，后续 200 秒和真实采集按阶段结果决定。240000 B/s 保留故障对照；后续有效载荷目标按完整数据产生速率至少 1.3 倍，以 250000 B/s 为目标。

恢复 F 源码 421da251210e54a7fcd1eea12d10104c117a0f83，1133392 字节，SHA-256 46aa94883f68c783ee2050ef54139e1e91eba035036ee5dda21e6bbfa49bc944；原镜像哈希已重核。批准后硬件操作前重新检查身份和空闲状态、保存本轮备份；失败先确认同 boot 及 TF 结束、无文件任务，再恢复应用并核验 NVS/参考文件、电脑连接。软件准备期间未访问设备。
