# 主控 AP 四页 FIFO 溢出对照

2026-10-19用户纠正目标模式为主控AP、电脑或手机直连，并确认电脑可保持AP约6分钟、结束后恢复互联网。主控fb2461c仅切换网络为STA关闭/AP开启，未刷新固件。第一次Windows连接请求时网络尚不可用，重新扫描后连接成功；首轮AP上传期间Windows请求切换至其他网络，传输中断，不记通过。配置和Windows事件保存在local-data/hardware/release-v010/ap-connect-20261019T034256Z及ap-speed-session-20261019T034516Z，私人日志不公开。

## 文件短对照

电脑实际连入主控AP，直连192.168.4.1。262144字节上传，固定100ms等待为28.547秒/9182.89B/s；立即查询为16.094秒/16288.31B/s；两次原HTTP流式下载2.656/2.672秒，98698.80/98107.78B/s。四次长度与SHA-256一致，boot不变，无遗留任务，串口监听正常退出。数据摘要2312394bd99545d9de131c24efb781e765ac1aec243f2ed9347597a793a415e9。证据local-data/hardware/release-v010/ap-speed-small-20261019_114951。小文件结果不是持续最大速度，也不是实际WPF已验收；与STA不同大小、不同链路的结果不作严格提速比例。

## 采集失败与证据

已保存STA失败现场及失败文件，读取并verify_flash核验当时AP配置NVS（20480字节，SHA-256 0be1ee3e301f658c5611dad6c777642e675bc79132a000043dc64afb7248946f），通过工具的前后MCU复位解除失败锁，未写应用或NVS，不计冷启动。首次检查在启动尚未就绪时没有取得help身份响应，标记未完成；随后不再复位，独立续测核实fb源、MAC、卡与旧参考摘要后开始，未放宽身份门槛。

实际AP四页300秒尝试74227083，0.135714秒停止，3054样本，errors=258（Overflow 2 + Rate 256）、FIFO高水位512、队列高水位1、INA无效0；写入/同步最大10555/10191us。原始位置local-data/hardware/release-v010/ap-four-pages-baseline-20261019T035347Z，文件/26-10-19-11.54/26-10-19-11.54.BIN，设备摘要ebf9ee96387cb81882c4f916e0139ed52c6bdd3cebfcf74b088497bd989ae335。保存后失败锁定，无异常复位或abort证据，未自动重录；电脑按约恢复互联网，主控保持AP。此失败与STA队列40/40耗尽不同，不能声称原写卡调度候选ce6fc55已解决AP FIFO问题，该候选未刷入，暂停单独试烧。

## 单变量对照安排

实际配置中FIFO为优先级8/CPU1；TCP/IP优先级18、可在两核运行；Wi-Fi驱动在CPU0。AP突发请求期间FIFO溢出而写卡队列仅1，优先验证FIFO被高优先级网络处理挤占的假设，不称唯一根因。候选A相对运行fb固件只将FIFO优先级8改19，CPU亲和性、SDK配置、采样、TF、队列和格式保持，写卡所有者仍用原策略。A后视证据再用候选B叠加已准备的写卡有界调度（捕获/排空优先级至少3，每归还块让出1ms，结束恢复），以A/B分离判断FIFO保护与写卡服务作用。此为试烧前对照安排；具体批准与实测证据见下方追加，不把构建当实机通过。

## 已冻结的具体 A/B 候选

| 候选 | 源码 | 镜像字节 | SHA-256 |
|---|---|---:|---|
| A：FIFO调度单变量 | 2055dce1e00b782ad7e4100dbecb15418ecb78a3 | 1122608 | f621c18723fb2e2647aa785213229f59159f655ccb6f7f16554eae163d611533 |
| B：A上叠加有界写卡调度 | aec26390ec7bc77b22cdeb7fe458210cc877007d | 1122816 | 0db02fd86b661fc4c857ab4f026964a23dde11a9e3dbdb9d38c5719cde453db1 |

输入清单分别033c60396d7a0b6a5e0eefeca1e781a4298bb61a5c820271e350bb90158724a9和0de061b429d10c8ae73e431a76cc1a37570786d49b2caa82c8098a539c6c12f8；独立干净输入构建均成功，SDK配置摘要均c8b91636…8d5，分区/空凭据头相同，静态RAM均87452字节。两套冻结stage独立，均已完成源码复制再编译，主机各限2个编译作业；未访问设备。源码自行复核：A对运行fb只有FIFO任务优先级及解释注释变化，B对A只有12行有界写卡调度；没有独立源码审查通过结论。

可用Code Review入口仍为PR的CI诊断工具，不能替代源码审查；本轮没有将旧PR CI或插件安装状态写成A/B源码审查通过。最终整套相对main的审查与最终PR CI仍待实际候选验收后执行。

试烧入口tools/release_v010_ap_fifo_trial.py和tools/release_v010_ap_writer_trial.py。各候选按自己的Git提交逐一核对冻结输入，避免把工作树B误作候选A；原字节镜像/SDK/分区/空凭据/备份扇区检查均通过，prepare-only不访问硬件。5项操作门槛回归通过。B在任何硬件访问前，必须取得A实际两份短录通过的本机结果、校验源码和镜像摘要，再将该结果加入批准文件摘要；缺失、失败或不匹配均拒绝。B仅接受A已通过或Queue(8)失败的停采状态，若A仍FIFO溢出则不进入B，先保存现场定位。

每份候选先重新备份并verify_flash核验0..0x123000及当轮最新AP配置NVS，仅写app0，做两份3秒完整导出/结构/CRC/尾部/SHA检查，再按实际结果继续AP四页。初检失败仅恢复该轮原应用，NVS只在必要时按同轮条件恢复。A的恢复镜像是已有短录证据但无线有缺陷的fb；B的恢复镜像A只有实际已取得的验收范围，不称完整稳定。此处不授权发布未实测字节。此段描述试烧前门槛；用户后续已具体确认A/B，执行证据如下。

AP失败记录已通过USB完整导出22198字节、电脑摘要与设备ebf9ee96…e335一致；解析明确拒绝错误258，作为失败证据保留，boot不变，不记通过。位置local-data/hardware/release-v010/ap-failed-export-20261019T035952Z。

## A/B实际试烧及四页尝试证据（2026-10-19）

用户原话确认 A/B 按上述顺序试烧。A/B均按顺序仅写app0并校验，未写NVS。A刷前1191936字节备份SHA-256 f0cd7b51bf95988cfc4afcfb877892a88f0aa9c6fb521b90706504044378a61e；B刷前同范围备份a563a6f882555ce4e83c41f35261a577bfd278c3230a6f27426eeaa03ca0a6ef，两轮NVS摘要均为上述0be1ee3e…946f，均与芯片核验。A初检local-data/hardware/release-v010/ap-fifo-smoke/20261019_120729/result.json、B初检ap-writer-smoke/20261019_163828/result.json均passed；各两份3秒记录完整导出和电脑校验通过，cold_starts=0，不扩大为整套验收。

A四页结果local-data/hardware/release-v010/ap-four-pages-A-20261019T083145Z/result.json明确passed=false。069B9A67请求300秒，实际28.981679秒/771186样本，页面失联触发安全停采；errors0、INA无效0、FIFO220、队列9。随后comparison-export-20261019T083514Z/result.json记录完整导出5502178字节，SHA-256 9562b8c5a1ad4cc5aff183c3151b0f8797a3dd07332d606680ea3f95fe061782，结构/2013块CRC/尾部一致，USB导出107.188秒；record_integrity_passed=true且capture_300s_accepted=false。尾部min_heap=764字节，未作同条件长时泄漏结论。PC网络在测试脚本finally恢复；不能把页面断连等同于PC提前切回互联网。

B四页local-data/hardware/release-v010/ap-four-pages-B-20261019T084525Z/result.json明确passed=false；browser.log记录状态超时、WS连接失败和25秒启动观察超时。UART hardware.log记录15B7477A实际启动后约9秒提前停采、242902样本、errors0、INA无效0、FIFO188、队列4，写卡优先级1→3→1；设备回读报告PASS，文件/26-10-19-16.45/26-10-19-16.45.BIN、设备SHA-256 c5084a78d164764ad6505490c35cb6093d49740c9c93ed90006e6bcb0dc43a65。因尚未电脑完整导出，精确尾部时长和电脑CRC/摘要不记通过。HTTP发送错误11及WS发送失败保留在原UART日志。

随后USB状态查询失败，B-gdb-readonly-20261019T085229Z保存只读?、g及有界m读取和解码；匹配B冻结ELF显示panic_abort→operator new→std::string构造→control::statusJson(web_control.cpp:146)→devicev2::dispatch→usb_v2::serve/receiveTask。已证实USB状态JSON大块副本分配失败；初始panic发生在UART监听退出后，不能声称保存了完整panic实时UART。该次取证未复位/继续/写寄存器。comparison-export-20261019T085002Z为未完成导出尝试，不能计B文件电脑校验通过。

观察器出现六个历史HTTP connection ID，加四个WS；这不是六个HTTP同时存活的证明。普通HTTP连接占用及心跳工作积压仍属待验证假设。A/B仅表明缩短区间采样未报FIFO/队列错误，网络/会话长时门槛仍失败；不据此合main或发布。后续C软件修复尚未构建/刷入，具体新产物须重新取得试烧确认。
