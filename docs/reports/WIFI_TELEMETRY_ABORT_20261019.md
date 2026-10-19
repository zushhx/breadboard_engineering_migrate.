# 2026-10-19 STA四页面状态序列化内存申请失败

## 现场与版本

用户确认主控已接入2.4GHz手机热点后，USB查询确认connected/sta_enabled/apply_ok为true、AP关闭、applying为false；记录前passed/ready/errors0/files_busy=false，剩余6204542976字节。源码4e6d5333c57942a88138d99582521159f3dbd6b3，应用SHA-256 38ef1b535084a7a290d8ea5bf40ae4aeb501ce07905e38db502fc7fce61c18f7；MAC及卡身份、两份既有参考摘要通过预检。电脑实际从STA地址打开四个独立页面/控制会话，没有走USB代理进行无线负载。

原始目录local-data/hardware/release-v010/sta-four-pages-20261019_110332，包含network-before-private.json、browser.log/events、hardware.log、失败result.json及decoded-backtrace.txt。私有网络设置和原始串口日志不公开打包。

四页准备完成后启动300秒，run=8071F57B，MCU start_us=3756008348。最后进度written_samples=114240、queue=1、errors=0，随后HTTP发送报错11，并在约3761241ms打印abort/core0；约5.23秒即发生故障，未完成300秒。采集前internal_free=34548、largest=14848，不是故障瞬间内存数值；本轮没有最终FIFO高水位、队列高水位或完整记录通过结论。文件可能未关闭，不能标为完整。

## 已证实与未决

匹配ELF SHA-256 bba64eb6fa3de21460bdef82b9772fd0a4aa3a841c1ec85a99b1e49d11400c80，前缀与panic日志一致。addr2line解码调用链为HTTP status -> control::statusJson():147 -> std::string(char const*, length) -> allocator/operator new -> __wrap___cxa_allocate_exception -> abort。触发点是状态响应字符串内存申请失败；禁用异常的SDK实现最终终止。不能将这次abort写成FIFO overflow，也不能据此声称历史FIFO问题根因已找到。

设备停在GDB stub。后续仅通过既有无DTR/RTS路径发送读取停止原因“?”和寄存器“g”，校验RSP摘要；响应保存在gdb-readonly/responses.json。没有发送继续、寄存器/内存写入、复位或刷机。配置快照、源码/ELF/产物及现场已保留；恢复未执行。USB状态查询失败是主控已停机后的结果，不是另一次验收通过或独立根因。

具体失败申请大小、故障瞬间总空闲和最大连续块尚未测出；网络缓冲压力、碎片化及其他分配的贡献仍须实机对照。主控停机期间不继续功能验收。最终main合并与正式发布停止，既有USB通过只覆盖原4e6d533候选及记录条件。

## 修复假设与检查

从干净3afe8f2新建codex/fix-release-wifi-status-memory，保留4e6d533及旧6ec57c3镜像、最新试烧前应用/NVS和本轮故障现场。优先去除HTTP状态/趋势序列化的动态std::string；趋势原来随点数增长的字符串拼接也具有同类内存风险，纳入同一响应路径修复。

HTTP状态和趋势共用有界9216字节缓冲、由原serialization互斥锁保护至同步send结束；复制模型和趋势数据时短暂持有Guard，发送期间不持有模型临界区。采集/监测/主任务停止判断不取得serialization锁。USB保留最终有界std::string副本，先完整格式化再复制，避免追加字段时扩容。相对原8192字节状态缓冲只增加1024字节固定区，不为两种响应各建一份大缓冲。SDK、CPU、引脚、分区、PSRAM、采样/任务优先级和I3REC001不改。

生产共用格式器新增宿主回归：空快照及UTF-8信息、原JSON字段集合、272行极值XYZ、150行最大uint32趋势、整数边界、恰好容纳/不足缓冲、越界计数拒绝；禁止C++堆分配重复1000轮，2024项原生断言通过，分配尝试为0。原始输出local-data/validation/release-v010/telemetry-json-host-final.txt。宿主不是SDK网络栈或实机低内存证明，USB路径也并非完全无动态分配。

本轮同时纠正源目录说明中200ms查询的旧描述，实际页面为500ms串行查询、趋势1秒；该文件属于构建输入，随新候选重新绑定。新候选还须构建、审查并准备具体摘要和恢复条件，取得候选试烧确认后才写设备。验收将先复测STA四页及相关内存/栈/错误，再安排完整矩阵；旧USB通过不得自动覆盖新字节固件。更完整的根因闭环和预防结论在实机对照后补充。
