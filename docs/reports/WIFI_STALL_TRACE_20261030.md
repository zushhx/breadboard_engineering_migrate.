# 2026-10-30 Wi-Fi 停顿取证

## 版本、操作与恢复

用户本日明确批准候选3b0646d84e2dbfa95ad60bf5690598828653a38e。镜像1148480字节，SHA-256 `6c1673b363c214eb0ef5804f7f3fd190ba0e6cabaeb1218cd05d36753534f17f`，manifest为`local-data/build/sensor-web/b0a271f38ee7c494/20261029_010340_003654/build_manifest.json`。固定工具链构建、prepare、6项CI及此前独立差异审查完成后，仅写app0。SDK、分区、采集参数和网卡设置保持。

原始操作目录：`local-data/hardware/rate240-f/20261030_091630`；抓包与系统取证：`local-data/hardware/wifi-stall-preflight-20261030-retry`。刷前1216512字节备份SHA `143ea09f445920587b74fc0c7b34f5748e6daf312cc43729b7660ec4b2f90302`；NVS SHA `5d2866b78a93e0a0bcf74c4542ddc0b69e6472af6f9e591f625a981014dba54b`。原始备份、系统事件含本机信息，仅本地保留。

RAM失败后首次恢复因HTTP/AP失联中止。用户重连后，于01:25:34.281405 UTC确认同boot、phase idle、files_busy false及来源身份，随后恢复F421da25/46aa9488…bc944并芯片校验、启动身份及旧参考复核。恢复后NVS摘要与刷前一致，`recovery-state.json`记录restored_f及pc_profile_restored均true。失败原始错误保留，恢复补充结果不覆盖原错误。

## 本轮结论及时间线

RAM SYNTH目标240000字节/秒、30秒，接收在约5.9秒失败；TF写入、MIRROR及真实采集均未开始。测速payload通过Wi-Fi TCP19000；USB串口承担控制和日志。后续真实记录阶段设计为原始文件写TF并经Wi-Fi镜像给专用接收程序，GUI实时监测另需验收。

以下均为2026-10-30北京时间，来源分别为Windows事件、PktMon及主机events.jsonl，未强行将MCU微秒时钟映射到PC墙钟：

| 时间 | 证据 |
| --- | --- |
| 09:19:58.790 | 收到RAM READY |
| 09:20:04.494018 | WLAN-AutoConfig/System 4003：受限连接自动恢复，type4、trigger5、error0 |
| 09:20:04.541966 | 抓包最后可见PC ACK，窗口65535；最后可见数据后约70µs |
| 09:20:04.585047 | Netwtw14 8000：Miniport Halt flow/driver disable |
| 09:20:04.608822 | WLAN8003：驱动断开Breadboard热点 |
| 09:20:04.704457 | 接收程序报ConnectionResetError10054 |
| 09:20:04.812877 | Netwtw14 7029：Driver initialization |

有直接证据表明本轮发生电脑无线适配器停用/重新初始化并中断连接；自动恢复的上游触发原因待查。NCSI在09:19:37曾标记Internet/PassivePacketHops，现有记录不足以将trigger5解释成单纯“热点无互联网”。试验脚本在RAM循环中没有调用网卡重启或网络切换；返回原网络发生在错误处理之后。

固件seq577：requested2400、accepted2368、send704632µs、total704971µs，errno0；堆采样成本305µs，前后internal free45656/44380、largest31744/31744；worker栈最低1292字节，生命周期heap minimum汇总28060。前后采样未观察到大块内存耗尽，调用内部瞬态尚未直接观测。该环只保留最后8条慢调用，禁止扩大为所有调用完整轨迹。

PktMon2517包，未报告ETW事件丢失；解析可见TCP序号缺口59处、56240字节，故包层可见性仍有限。未见客户端零窗口（最小64559），末尾无可见FIN/RST；系统断连之后的无线情况尚无独立空口捕获。

10月28日09:02:56和18:22:37的两次TF并发失败时窗，已保留日志中未查到System4003；对应WLAN断连在开始esptool恢复之后。应继续区分这些历史TF并发停顿与本轮PC自动恢复。

## 下一步与审查

保持F、电脑驱动23.150.0.4及网卡设置，先进行200秒低负载AP观察，每2秒只读状态，检查同boot、空闲、无文件任务及连接保持。成功仅覆盖此观察条件；失败保存事件，结束恢复原电脑网络。当前USB重新枚举尚待用户接线，初次观察未开始，记录在`local-data/hardware/wifi-f-idle-20261030`；新等待入口使用`wifi-f-idle-20261030-retry`。该观察不启动采集、写卡或烧录。

若低负载也断连，优先采集Windows无线ETW以定位4003之前的状态变化；若保持连接，再按已批准候选安排相同负载复测并同时收集ETW。暂不修改驱动、IPv6、注册表或多个节能选项。Microsoft推荐沿无线状态变化向前定位，并核对AP侧证据后再改变客户端配置：[官方排障文档](https://learn.microsoft.com/en-us/troubleshoot/windows-client/networking/wireless-network-connectivity-issues-troubleshooting)。trigger5的确切含义尚未从官方资料核实。

本轮原始数据及脚本为主agent自行复核；新独立复核agent因额度限制失败，未计为独立审查通过。设备保持已恢复F，未合并或发布。

## F低负载观察完成

首次等待USB后2秒身份查询没有回复，脚本按身份门槛退出（报identity mismatch，原始串口只有SEND help；没有读到其他固件身份）。随后独立只读help确认F和MAC；本地观察入口增加最多3次help及启动等待、无回复与身份冲突分别报错。重试使用新目录，旧证据保留。

最终证据`local-data/hardware/wifi-f-idle-20261030-retry2`：2026-10-30 07:14:54.219300–07:18:15.136385 UTC（北京时间15:14:54–15:18:15），实际200.906秒，99份状态，同一个boot，全部idle/ready、errors0、files_busy false。该精确时窗System及WLAN Operational内WLAN/Netwtw/NDIS事件查询为空。结束后返回HQU，state passed和pc_profile_restored为true。查询周期约2秒，结论覆盖低负载状态观测及保留的系统日志；未测连续240000字节/秒及TF并发。

下一步保持3b0646d候选及原参数，复测RAM30/前置通过后TF30，并增加Windows无线ETW，以定位高负载失败前的状态变化。F低负载与3b高负载的固件及负载同时不同，本次对比仅证明F在该200秒窗口保持连接，尚未隔离单一原因。

## 15:57同候选复测与扫描线索

操作提交1c8430686d3f108aecce198249b83bbab07cb80d，候选仍为原3b0646d/SHA6c1673b3…3534f17f。证据：`local-data/hardware/rate240-f/20261030_155256`、`local-data/hardware/rate240-offline/20261030_155545`、`local-data/hardware/wifi-etw-preflight-20261030`。新增有界128MB非持久Windows WLAN-AutoConfig/NWiFi/NCSI ETW，TCP抓包保持128字节快照；记录增加属于本轮观测条件差异。

RAM seq65的send399790µs、total400166µs、接受1856/2400、errno0；前后internal free31272/49876、largest22528/31744，采样325µs；栈最低1180字节、生命周期heap minimum汇总15256。RAM目标30秒未完成，TF及真实采集均未开始。

精确UTC时间：07:57:02.3939831 InternalScan client0；02.3940318请求扫描/flush BSS；02.3942966 Begin Scan；03.4381727返回25个网络。NWiFi相邻记录OID0xE01017A返回0xC00000BB，具体请求含义待核实。此区间与失败重叠，但ETW未提供明确的scan-complete事件，列表返回仅为后续观测点。失败前后System/WLAN常规日志无WLAN/Netwtw/NDIS事件。

PC抓包295包，解析TCP序号缺口0、客户端最小窗口64559、零窗口0。最后1856字节（1440+416）出现在07:57:02.936272/02.9362729，PC于02.9363558累计ACK；主控03.049706发送FIN，约113.35ms在ACK之后。主机接收器03.236356报truncated stream。捕获的ACK仅证明主机发送路径可见，设备接收/处理未观测。扫描增加时延有官方机制依据：[Microsoft WlanScan](https://learn.microsoft.com/en-us/windows/win32/api/wlanapi/nf-wlanapi-wlanscan)，本轮关联待单变量对照。

07:57:07.430788确认同boot、idle、files_busy false，随后自动恢复F并芯片校验、旧参考复核；NVS恢复后SHA仍5d2866b7…ba54b，电脑返回HQU。两路跟踪已正常停止；原始ETL、pcapng与解码均本地保留。

后续优先保持固件与参数，在连接后关闭Wi-Fi网络列表/网络设置窗口的条件下复测，继续记录扫描事件；当前记录client0不足以确认扫描发起应用。该操作只排除可见UI变量，后台扫描可能继续存在。暂未改变驱动、注册表或全局网络策略。

独立子agent已只读解析原始pcapng，确认295包、序号缺口/重叠0、PC零窗口0；最大服务器数据到达空档460.863ms，位于07:57:02.473522至02.934385，落在Begin Scan与网络数量事件之间。PC接收队列高水位9600、待写0，已完成64帧153600字节。该复核支持时间相关性，仍未量出空口离信道时长，也未对齐MCU与PC时钟。下一轮关闭可见网络窗口后，以ETW实测扫描有无及频率；若仍有扫描，不能将该轮称作无扫描对照。

## 用户30%余量要求

该要求持续有效，当前固定240000B/s候选仅用于定位失败。

用户明确要求有效传输速度至少达到数据产生速度1.3倍。只读子agent按生产格式核算：完整BIN字节为340+7*N+48*ceil(N/448)+72*INA条数，E已通过记录37966410字节/200.003721秒=189828.518B/s，1.3倍246777.074B/s；标称26667组/秒及INA10Hz预算需247320.032B/s。建议后续门槛250000B/s有效payload，TCP/IP/Wi-Fi头与重传开销另计。当前240000B/s只比E记录高26.43%，降速与保持现原始记录格式及30%余量存在约束冲突。依据为record_format.h、recorder.cpp、RELEASE_V010_EVIDENCE_20261018.json及F_RANGE_HOTSPOT_TRIAL_EVIDENCE_20261024.json。若覆盖软件允许28001组/秒上限，则预算约259645B/s；该上限属于软件容差，尚无对应硬件最大速率实测。

## 20:57 RAM通过、TF并发失败

同3b0646d镜像，操作提交b113fdfbb09ae57a16204906b310b3d615029bef，证据`local-data/hardware/rate240-f/20261030_205059`及`local-data/hardware/wifi-scan-ui-closed-20261030`。用户明确“我暂时关不了”，已写window-condition.json，目录名反映原计划，实际窗口保持打开；本轮为重复基线取证。

RAM30完成，随后pattern run7815C5E2约1.26秒结束失败，TF写入297600字节。seq52发送403249µs/total403720µs，接受2544/3200，前后internal free15892/18660、largest7680/9216；input244800、sent127200、dropped88800、queue25600和当前帧3200守恒。worker栈最低1288，生命周期堆minimum汇总916，仅供压力线索。

独立agent对齐同一MCU时钟：第一次慢写294101557–294154475µs（52.918ms）早于seq52开始367.998ms结束；seq52区间294522473–294925722µs，只有第二次慢写294709502–294738800µs（29.298ms）重叠，占7.27%。seq45慢send103.209ms与两条慢写均不重叠。IO记录阈值20ms，较短调用及调度影响仍未完整分解，单次TF慢写解释整个400ms证据不足。

ETW两次Begin Scan分别为12:57:07.3155915和12:57:38.6349526 UTC，分别落在RAM与TF阶段；RAM虽出现扫描仍通过。TCP解析RAM13470包、序号缺口58处70160字节；TF252包、缺口1处1456字节，两个会话均未见PC零窗口。TF尾部可见12:57:38.897327累计ACK、38.901298额外16字节、39.207591主控FIN，随后PC累计ACK；ACK可见性与缺口限制仍保留。RAM最大相邻可见服务器数据空档216.294ms；TF最大682.985ms包含READY到源启动的等待，不能用于网络停顿归因。

源码和本机IDF5.4.2只读复核：MIRROR相比RAM额外申请25600字节内部堆且有生产者任务、突发节拍、最多3200字节帧变化。Workspace已借用采集池。lwIP api_msg.c在ERR_MEM后可等sent_tcp/poll_tcp；超时已有部分写入可返回ERR_OK及部分长度。ERR_MEM同时覆盖sndbuf、段队列及分配不足，errno0的部分返回尚未区分这些原因。发送期限可能在回调再次进入时才检查，403ms超过250ms配置与此机制相容。必要时未来新候选增加固定缓冲的失败分配取证，先核实全局回调槽所有权及副作用。

本轮已同boot确认TF终止后恢复并校验F，参考文件通过，电脑回HQU；两路抓包停止。后续准备媒体传输模式辅助器：opcode3、4字节BOOL，默认只读；连接到指定AP后才允许临时开启，记录原值并回读，退出显式撤销自身请求再关闭handle。当前仅软件准备，不改变设备或驱动配置。微软接口语义见[WLAN_INTF_OPCODE](https://learn.microsoft.com/en-us/windows/win32/api/wlanapi/ne-wlanapi-wlan_intf_opcode)；现代驱动的连接质量提示及显式扫描限制仍须用ETW实测，改善后也继续区分扫描、省电与内存机制。
