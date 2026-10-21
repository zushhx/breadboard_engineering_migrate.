# 文件取消隔离与下载桥接错误处理

2026-10-20，从ad540fd建立codex/fix-release-file-cancel-isolation。主控继续运行已批准的D，源码修改不写设备；D原镜像、配置备份与原始记录保留。两个子agent分别实现文件取消和桌面桥接，双向审查后主agent集成。

## 问题与改动

桌面自动清理先读files/state再POST cancel，期间原任务可能结束并被新任务替代；旧网页的pagehide取消也可能迟到。原服务端未先验证目标就置全局cancel。修复在同一文件mutex内验证可选expected_job及非空transfer后才置标志，字段类型/显式坏job先拒绝且不污染状态；transfer发布和清除也受该mutex保护。显式无范围手动全局取消保留旧接口兼容，不将该机制描述为账户或文件权限隔离。

新桌面及网页自动清理带原任务标识。capabilities增加可选布尔atomic_file_cancel=true；旧客户端忽略额外字段。桌面只有明确收到该能力且本job已被HTTP202及匹配ID接受后才自动取消。旧固件缺少能力、值异常或读取失败时不发自动清理，依赖原有60秒废弃传输超时，避免旧固件忽略expected_job又退化为全局取消。

D完整记录首次经旧desktop download桥路由导出返回零字节，保留失败、未发布完整文件；约8秒与7秒job门槛相近，但没有证明唯一原因。实际源码确认路由曾吞掉下载异常。新桥在首个读取有效后才发送HTTP200；头前失败返回真实状态或504，头后失败保留长度截断，不向二进制追加错误。末块在总数和SHA通过后才发送；本机日志只写阶段、job、code、计数及异常类型，不写凭据、启动令牌、transfer、文件名、后端错误原文或文件内容。保留7秒单job及原块大小，未盲改时限或宣称已解释此次实机截断。

桌面组件按批准计划升0.3.1；旧安装0.3.0及快捷方式尚未替换。SDK、引脚、时钟、PSRAM、32块采集池、记录线格式不在本轮变更范围。

## 实际软件检查与边界

- 真实files_v2.cpp/device_v2.cpp宿主111项通过，包含queued/running替代任务、真实f_read交错、匹配取消、坏字段和手动全局兼容。旧生产源负对照能够检出竞争；宿主使用临时文件系统，不是真FatFS。
- 桌面既有Core/Bridge/Management 79项及新增竞态/能力14项通过。
- 实际网页脚本模拟31项通过，含pagehide原任务范围及结果不确定提交后的清理意图。
- 真实DeviceBridge独立回归97项通过：请求拒绝、真实7秒超时、首/后续读取失败、替代任务、能力兼容、坏SHA、长度不足、空文件，以及超过1MiB且总耗时超过7秒的正常流。独立入口tools/test_download_bridge.ps1，不冒称原CoreTests已包含该97项。
- 两实现agent互审及主agent差异复核，未发现剩余阻断；不是外部Code Review服务源码批准。git diff --check通过。

原始证据位于local-data/release-v010/cancel-isolation/production-{host,desktop,ui}.log及local-data/validation/release-v010/download-bridge-20261020-134217/。新固件/桌面仍须干净构建绑定产物，再完成实机文件、兼容、取消和最终矩阵；软件通过不替代试烧确认或正式发布门槛。
