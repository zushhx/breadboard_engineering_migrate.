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
