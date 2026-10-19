# USB遥测内存与HTTP资源修复

2026-10-19，从6f58b540的A/B证据状态建立codex/fix-release-bounded-network-responses。用户要求继续原整套验收计划，并明确授权立即采用子agent协作；USB、HTTP及证据整理划分修改范围，主agent负责集成和最终验收。

## 已证实与待验证

B匹配ELF的只读GDB回溯已证实USB statusJson构造大std::string副本时分配失败；现场及A/B实际结果见[AP报告](AP_FIFO_OVERFLOW_20261019.md)。历史HTTP connection ID不证明同时存活数量，普通连接占用及心跳排队压力是待实机验证的资源假设，不能称无线失联唯一根因。

## 修复及审查

实际USB GET状态和趋势用现有互斥保护的有界缓冲直接发送metadata/body/end，保留协议、2048字节协议块及128字节驱动写入上限。兼容dispatch接口保留，小请求及cJSON仍可分配内存，不称全路径无堆分配。格式化超界发送500，传输失败不发成功结束帧。

另一agent审查指出：原每次写入成功重置stall计数，间歇慢USB可长期持有共享缓冲锁并阻塞HTTP任务。修复对整份USB遥测响应设置1秒绝对发送期限，覆盖metadata/body/end且不随部分进展续期；正常9k字节按已有USB应用路径约0.18秒估算，余量尚需实机确认。HTTP和USB并发及慢读退出纳入候选定向验收。

普通HTTP响应声明Connection: close，在完整body或最终空chunk发送后shutdown，由SDK随后EOF清理socket；合法WS升级和帧不走此路径。没有扩大socket预算或启用LRU。交叉审查发现固定SDK的queue_work走本地UDP，满mailbox可在发送返回成功后丢弃，所以没有为每份普通响应排队关闭；心跳使用generation及12秒queued租约，旧callback无效且不清新token，running工作不被替换。最多一份有效心跳工作，不称底层绝无过期消息。

有效API请求先读完body，正常shutdown的FIN可发送已排队TCP数据；未消费的大错误请求或HTTP管线仍可能触发RST，不能把宿主检查称TCP交付保证。候选必须实际验证网页/JSON/下载完整性和摘要。SDK、引脚、时钟、PSRAM、分区、采样和写卡调度不在本轮变更范围。代码由实现者自查、另一agent交叉审查和主agent集成审查；这不等于外部Code Review源码审查通过。

## 已执行检查与边界

- 实际USB源及路由宿主：9000字节状态、8192字节趋势，禁止C++分配、metadata/CRC/id/end/分块、格式化及发送失败；间歇进展仍受总期限限制，下一正常请求恢复。控制模型、锁和23项文件/路由检查通过。
- 实际遥测格式器：2024检查及1000轮禁止C++分配通过。USB宿主的streamTelemetry使用替身，生产格式器单独测；完整web_control编译/链接由固件构建验证，锁与实际SDK时序仍须实机。
- 工程checkout检查通过，未访问设备。检查期间主控B仍停GDB，无复位、写寄存器或烧录。
- 实际HTTP策略头宿主检查通过：完整发送后关闭、发送/关闭失败、chunk末块、generation/过期/旧callback/运行中/排队失败/并发callback及32位时间回绕。WS检查是源码结构guard，未执行真实SDK连接负载。

本报告不记新候选实机通过。最终源码、镜像摘要、恢复条件及试烧入口准备后，仍须取得该具体候选试烧确认。main和Release门槛保留。
