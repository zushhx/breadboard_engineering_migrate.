# 采集池与网络内部内存余量对照 D

2026-10-20，在`codex/fix-release-capture-network-headroom`、准备前HEAD
`07bc89c765203310b68aead1a485be45c29466b7`上实施。当前事实由
[STATUS](../STATUS.md)维护；本文记录假设、改动、证据和通过范围，不代替实机验收。

## 假设与受控改动

C的AP四页采集在8.797651秒提前停止，采集错误0、FIFO214、队列10，录制期间
HTTP超时而停采后恢复。录前内部空闲31264字节、最大连续块14848字节；采集再
动态分配两个4096字节任务栈、TCB及队列。清理这些资源后HTTP恢复，与网络内部
内存余量不足的假设一致，但尚未证明唯一根因。完整C失败证据仍按原来源保留。

D只将采集池`kSlots`从40改32，保持每块448帧、FIFO19/CPU1、写卡所有者3/CPU0
及其归还块后1ms让出策略。SDK、PSRAM、引脚、时钟、分区、网络配置、文件取消
行为、会话/API与`I3REC001`线格式不改。未叠加尚在其他任务中准备的取消隔离修改。

实测目标Chunk大小3168字节：池126720→101376字节，减少25344字节（24.75KiB）；
两条32位指针队列另减少64字节，其他固定分配不变。这是分配量差值，不保证实际
运行free/largest恰好增加同样数值，网络仍可能利用新增空间分配缓冲。

名义原始缓冲约672→537.6ms；历史短测queue_high13时，32块余19块约319.2ms。
高水位包含已经不在freeQ中的生产者/写卡者缓冲，不等于dataQ长度。C已见最大单次
写155.414ms、同步96.530ms；两者并非同一次连续停顿的证据，历史13块也非最坏
占用保证。因此不能据此宣称TF余量充分；必须实际检查长时队列/FIFO及文件完整性。
借给文件管理的Workspace静态上限32768字节仍小于101376字节池，现有空闲/长度
门槛保持。Header/Block/INA/Footer的224/48/24/68字节静态布局断言保持。

## 被动可观测性与显示

- 在原有主写卡任务约1Hz进度日志中追加内部8bit内存free/largest；不在FIFO热环
  遍历堆，不新增HTTP轮询。`heap_caps_get_largest_free_block`会遍历堆，有测量开销。
- HTTP状态/趋势在失败或总耗时超过100ms时记录mutex等待、格式化、发送及关闭、
  总耗时（毫秒）和立即保存的errno；成功快速请求不打印。headers完成后开始计
  mutex等待；串口打印在TelemetryLock释放后、无model Guard时执行。阶段时间是
  墙钟时间，包含被抢占时间，不是任务CPU使用率。
- 网页“采集缓冲 / 40 块”改为“采集缓冲 / 块”，保持已有计数字段，不扩大API。
  README更新32块101376字节；状态/趋势静态JSON容量按实际常量校正为9216字节。

因此实验是一个资源配置变量加已声明的低频测量开销，不声称严格没有混杂因素。
若恢复HTTP可用性仍须结合阶段计时和heap证据判断，不能仅凭一个通过结果认定根因。

## 本轮软件检查与门槛

使用WSL的g++运行实际生产源码宿主检查，未连接设备、未复位/烧录或构建整套固件：

- `tools/test_controller_v2.py`通过：四会话/接管/10秒模型、双锁、实际USB分块/
  整份期限及23项文件路由检查。文件操作用临时主机文件系统，不是真FatFS/TF。
- `tools/test_telemetry_json.py`通过：生产格式器2024项断言，1000轮禁止C++分配，
  完整schema/字段极值/缓冲边界；不代表网络发送不分配或实机内存充足。
- `tools/test_http_response_policy.py`通过：实际HTTP策略头的发送/关闭顺序、错误、
  心跳租约/陈旧重复回调/运行中保护及回绕；WS持久路径是源码guard，非实机TCP验收。
- `git diff --check`通过。未写实现镜像式测试，不重复桌面/WPF无关检查。

原始软件日志分别在忽略目录`local-data/validation/release-v010/D-controller-host.txt`、
`D-telemetry-host.txt`、`D-http-policy-host.txt`。该检查不编译完整web_control/recorder
SDK依赖，最终干净冻结固件构建仍须执行；上述通过不能替代新字节的具体试烧批准。

另一个agent应复核最终diff后再冻结D；新产物绑定和试烧仍按具体候选门槛进行。
D专属四页验收脚本须使用`queue_high<32`并绑定新产物，不能仍沿用<40；历史40块
脚本及历史结果不改写。长时AP/USB/STA、全部导出、网络与桌面路径和真断电门槛
尚须由最终候选完成，未满足时不合main、不发布。

## 冻结候选与操作准备

源码9cc8c174f628ed0654403b248c72389a426f2ef6，输入SHA-256
0de0bc9e9b19dacb91a7777410cf1169edf5f467efc016ff1aa20e268306aedf。
local-data/build/sensor-web/0de0bc9e9b19dacb/20261020_125714_001195/build_manifest.json
记录干净输入构建成功、无嵌入凭据、hardware_tested=false。应用1124352字节，
SHA-256 f2ca20ab566115f5a38d7e6dc198d57d68f1e4a1e48a1d6d84261d59b7fc1aa9。
SDK配置c8b9163660bf87d2f38639536994c92b721a8efaf266a2c2986696ebbf2678d5、
分区1d9cca96de0fe07ad7fc0648b9878ddecd9ce565e38b589ad20fea698ed4c80c与C一致。
静态RAM仍87460字节；运行时pool减少与静态段大小不同，不混写。

另一agent对实际最终diff交叉审查未发现阻断，主agent集成复核；非外部Code Review
服务源码批准。新门槛tools/release_v010_network_headroom_trial.py绑定本候选，
prepare-only通过且hardware_accessed=false，13项定向fake-session回归通过。
真实操作必须先确认当前C健康空闲/AP配置/源码、boot/run未变及旧参考，再备份并
verify当前1191936字节及当轮NVS，只写app0；失败只恢复保留的C原应用，NVS仅
变化且同轮备份摘要符合条件时恢复。C只具备历史短录通过范围，有无线长录缺陷。

D专属AP观察脚本已独立使用queue_high<32，历史C脚本不改。新D具体试烧确认尚未
取得，未烧录。原始构建日志network-headroom-build-D.txt和prepare清单
network-headroom-D-prepare.json保存在local-data/validation/release-v010。

## D实际结果与当前阻断（2026-10-20）

上述准备后的用户明确试烧确认已取得，D仅写app0并完成两份3秒完整校验。
AP四页原请求300秒，在约199秒遇到电脑WLAN驱动自动恢复而断连；主控随后安全
保存208.192525秒、5548074样本，无UART panic/reset。用户明确接受AP约200秒，
仅修改时长目标，原请求、电脑中断证据及INA无效为零门槛均保留。

完整记录39581242字节已导出，SHA-256
d624c1c1667e7bf482b367607475267926537c057357f7adc5c52105e7860105
与设备一致，结构、逐块CRC和尾部计数通过。但INA参考2082份中6份无效，
属于记录质量失败，不能按计量精度暂缓豁免，故D的AP验收未通过。errors及
saturation均零；FIFO高水位224、队列14，max_write/max_sync为154732/182052µs，
min_heap7660字节，sensor/INA栈余量2076/2744字节。这些计数尚未证明无效根因。

离线证据为`local-data/hardware/release-v010/D-diagnostic-export-20261020T053048Z/offline-decoded-not-accepted.json`；
原始完整`.partial`及失败结果保留。设备仍运行D，未刷后续文件路径修复；main合并和
正式发布门槛未满足。下一步先定位这6份无效样本，不重复采集来替代失败证据。

## 后续E结果（2026-10-20）

E源码4aa27b2c90902d03e262c23cad5eb65f812bc524已试烧，保留D的32块池，另含
INA完成后等待100ms及文件路径修复。AP四页62F30A95的200.003721秒记录和完整
37966410字节HTTP导出通过，SHA-256为
113e81189e2df28b08e5ef63b37e4a653132a3d5df5af28dcb7c222424bf6d06；
结构、逐块CRC、尾部总数及errors/INA-invalid/saturation零门槛通过。FIFO239、
队列16、max_write147418µs、max_sync181018µs；INA1363份，实际6.814873Hz，
比10Hz低约31.85%。尾部最小堆1440字节，重复同条件空闲泄漏仍未测，不能据此
宣称内存预算充分或唯一根因闭环。此结果不覆盖其他候选或整套发布矩阵。

证据：`local-data/hardware/release-v010/E-diagnostic-export-20261020T083951Z/result.json`。
主控现保持E，D失败证据不改写；main合并与正式发布仍等待其余矩阵。
