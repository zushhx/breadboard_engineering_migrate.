# 手机热点实时界面与录制控制软件复核

2026-10-16。范围及授权见[计划](../plans/SENSOR_WEB_CONTROL_20261016.md)，当前事实见[STATUS](../STATUS.md)。本阶段仅软件，不连接串口、不复位/烧录、不读写实物TF，不代表手机兼容性或联网记录通过。

后续按指定候选另获整轮授权后，c921bef实际在WiFi启动阶段失败并恢复203a60b，见[失败与恢复报告](SENSOR_WEB_FIRST_TRIAL_20261016.md)。本文件的软件阶段通过不扩展为实机通过。

## 实现和变更边界

从0872243独立复制sensor_tf_recorder至sensor_web_recorder，原候选文件不变。保留IDF5.4.2/PlatformIO6.2.0/espressif32@6.11.0、CPU160MHz、PSRAM关闭、分区、引脚、TF四位20MHz及五秒等待、SPI Mode3/8MHz、IIS量程/ODR、INA配置/换算、I3REC001格式。新增STA、DHCP、HTTP、5Hz只读快照和限速96点短窗口、单槽开始请求、停止标志。仍由app_main唯一访问FatFS；网络不直接调用模块或文件API，预览取已出队块，采集/INA任务不等待网络。启动前预留同样126720字节池，后续保留以避免联网后的碎片，不扩大缓冲。

源码自行审查发现PHY默认独立持久化RF校准，已将唯一允许的SDK差异固定为CONFIG_ESP_PHY_CALIBRATION_AND_DATA_STORAGE从y变n；每次启动做全射频校准。WiFi另设nvs_enable=0/WIFI_STORAGE_RAM，不初始化或擦除NVS。固定本机SDK依据为components/esp_phy/src/phy_init.c的esp_phy_load_cal_and_init分支及components/esp_wifi/include/esp_wifi.h；编译断言和构建差异白名单阻止误开持久化。不把此项校准与INA计量校准混同。启动耗时及供电影响待实测。

本地凭据只从local-data配置读取，转为UTF8八进制C字符串注入ASCII暂存目录，不拷入公开源码、网页或CI。CI空凭据不会联网；带凭据产物标为私人，不上传。生成网页资产替代PlatformIO/Windows初次EMBED_TXTFILES路径错误；按当前工具链uint32_t类型修正格式化参数后开发构建通过，初次失败不作为候选产物。正式候选须来自干净提交。

## 审查方式与处置

作者自行复核原候选与新main/recorder实际diff、SDK键值、初始化顺序、写门、停止/清理/回读及跨任务所有权；不是独立审查。重点处置：保留准备阶段收到的停止请求；清除下一轮旧stop/预览/INA值；queued可无写入取消；忙/校验中拒绝新开始；失败锁定不自动重试；成功只在关闭与读写摘要一致后发布。状态短临界区不含文件、网络或分配；HTTP输出缓冲固定，JSON长度/字段/整数/卡身份/范围校验，嵌入NUL拒绝。启动随机令牌通过自定义头校验，禁CORS，页面重启后要求刷新。相同热点获准客户端可操作，不宣称有独立账户隔离。

堆API检查到固定SDK的heap_caps_get_largest_free_block通过heap_caps_get_info遍历匹配堆；尚未定位旧largest=31744而后分配成功的原因。新候选对相同INTERNAL|8BIT能力记录前后值/申请成功，不更改SDK或声称疑点已解决；实机阶段结合实际申请核对。当前静态RAM不代替联网后的动态余量。

## 软件验证

原生C++直接执行网页控制Model，覆盖卡身份/容量、run/时长、单槽、串口不越过排队请求、取消、准备阶段停止、断网状态不变、排空/关闭/校验期间拒绝新开始、下一轮清旧值、失败锁定。凭据测试覆盖空CI配置、拒绝公开路径、C字面量注入与Unicode转义。全套结果和最终构建在下节绑定。

全套37项自动化测试通过，使用Windows固定Python及Zig0.13.0直接编译C++控制模型；日志位于local-data/validation/sensor-web/unit-tests.log。新增候选的upload/uploadfs/uploadfsota/erase/program入口在设备发现前均被guard拒绝。

Playwright1.62.1/Edge155.0.4283.45真实浏览器运行实际HTML，服务端为127.0.0.1软件模拟，13项场景通过：无自动录制/无效INA空值、非法时长、双击仅一次、状态与预览、390px手机无溢出、断连禁控/重连无重发、停止独立空请求、校验不冒充成功、最终结果/旧INA标注、刷新无重发、POST应答丢失不重试、失败锁定、重启旧会话禁控。无页面脚本错误，status最大并行数1。桌面1360px及手机390px截图已人工查看，无横向溢出或遮挡。截图醒目标为软件模拟，数据不是实机观测。证据位于local-data/validation/sensor-web-ui/，不作为联网负载验收；浏览器测试服务端不替代实机HTTP/JSON处理、FreeRTOS调度及内存压力验收。

## 最终构建、CI与证据

最终字节级复核发现Windows文本写入会将生成凭据头的LF转换为CRLF，而首版输入清单对转换前字符串取摘要。已固定三个生成头的LF字节、在编译前核对实际凭据头与输入摘要一致，并记录三个暂存生成头的真实哈希；只读试烧准备工具也拒绝缺失或不一致的生成哈希。最终c921bef候选已实际核对一致，早期18ab3cb/3a330a3产物保留为被替代的开发记录，不用于试烧。这是构建证据绑定修正，不涉及传感器/TF参数或计量补偿。

最终干净源码c921bef8f14e8c18ce06cd5574c241377562e971固定环境带本地凭据构建通过，固件958320字节，SHA-256 e1dae22f0714218c7961519be1b9f9af1c52062982e4619609b0019b37387a68。输入聚合a09e586f4e1199b17198cc411864a37ac516692c55c0fa90a0f07d826644581a，SDK精确白名单及冻结分区核对通过；静态RAM63876字节不代表动态余量。完整清单及私人产物保留local-data，[公开构建摘要](SENSOR_WEB_BUILD_20261016.json)只收必要元数据与证据哈希。

首版CI run38840225224的web构建编译成功，但配置门拒绝；增加诊断后run38840664332及Windows无凭据构建复现两个差异：关闭新名称时生成器还清除了CONFIG_ESP32_PHY_CALIBRATION_AND_DATA_STORAGE。固定SDK components/esp_phy/sdkconfig.rename第8行明确映射到CONFIG_ESP_PHY_CALIBRATION_AND_DATA_STORAGE，两者为同一逻辑选项。新候选显式关闭两键，编译断言检查两键，构建与准备工具共享精确的两键白名单；没有放宽到其他配置。修正后37项测试复跑通过，18ab3cb带凭据本地完整构建及Linux无凭据CI web构建通过。3a330a3/a890b098旧候选已被替代，不能用于下一轮试烧；开发与失败记录保留。

旧3a330a3首次干净构建在SDK esp_lcd_panel_rgb.c编译时触发GCC内部Segmentation fault，相同源码/配置/工具链输入重试通过，失败与成功分别保留102043_966043和102200_902343目录。未更换编译器、修改SDK源码或关闭检查；本次重试恢复不能定位内部崩溃根因。此前WSL存在但缺原生编译器，沿用固定Windows Zig及显式PowerShell7.6.6。提交hook首次受WindowsApps的Python启动别名影响未运行成功，后在该命令进程PATH排除别名并优先固定Python，hook实际检查通过；未跳过hook或降低规则。最终18ab3cb干净构建首次即通过，日志在103333_994864目录。

[草稿PR #6](https://github.com/zushhx/breadboard_engineering_migrate./pull/6)基于未合并的PR #5，CI新增独立无凭据web构建；本地带凭据构建不能由无凭据CI替代，CI更不能代替手机测试。最新各项结果以[对应HEAD检查页](https://github.com/zushhx/breadboard_engineering_migrate./pull/6/checks)为准，原始CI响应保留local-data/validation/sensor-web。Code Review的pull_requests_checks实际返回“Can't connect to GitHub. Try again.”，独立源码审查仍未取得。Git直连超时，使用Windows已有127.0.0.1:7897代理的单命令参数成功推送，未修改全局代理。

只读执行tools/prepare_sensor_web_trial.py，对当前构建输入、候选/恢复产物及分区哈希复核通过，输出local-data/validation/sensor-web/trial-packet-final.json；未创建实机批准。下一轮候选覆盖至0xFA000，备份必须覆盖0..0xFA000共1024000字节，并在写入前比对现有203a60b应用、实际boot/分区。只写app0，保持NVS/boot/分区；失败按受影响应用扇区备份恢复，旧批处理针对不同候选和SDK白名单，不原样复用。

## 未验证与恢复

未验证手机2.4GHz热点互访/电脑外网并存、DHCP地址、WiFi接入和断连、运行内存/栈、联网五分钟FIFO/TF负载、早停实物文件、复位/两次真断电与主机导出。本候选新增网络和RF启动过程，前版通过不能覆盖这些变化。电压/电流精度保持暂停且未验收；温升、长期、实际加工、写入中断电、全卡完整性和网络下载不在软件通过结论中。

软件可通过撤销独立任务提交恢复，正常候选目录未改变；实机恢复目标203a60b与对应哈希/接法见STATUS，原产物保留。未执行新恢复路径。
