# 振动记录时间命名候选

2026-10-18，用户确认[具体方案](../plans/RECORD_TIME_NAMES_20261018.md)。当前部署状态只在[STATUS](../STATUS.md)维护。本轮仅准备候选，未连接设备、复位、烧录或写卡。

## 行为与影响

新网页点击开始或确认接管时发送控制端本地日期时间（秒精度，2000–2099年）。主控以收到请求后的单调时间补偿排队及旧记录保存等待，在准备阶段选择`YY-MM-DD-HH.mm`名；模块准备跨分钟时首次写卡前重新选择。它依赖控制端时间正确，不是NTP校准或传感器时间基准。

示例`/26-10-18-15.46/26-10-18-15.46.BIN`，重名时依次`-02`、`-03`，目录与文件同名。已有目录或同名文件均占用该编号，其他查询错误直接拒绝，最多9999个候选；mkdir失败不继续open，写文件仍FA_CREATE_NEW，不覆盖历史文件。实际路径经record_name/record_path状态字段显示，原桌面SaveFileDialog和网页download默认使用实际文件名，无需更新桌面EXE。历史文件不自动改名。

run编号仍用于会话、接管及趋势身份；I3REC001布局、Header/Footer、采样参数、模块准备/启动FIFO/写入/回读校验顺序、SDK、引脚、CPU/PSRAM及分区不改。新增有界状态元数据与路径缓冲，物理介质和调度影响仍需实机验证。旧客户端未提供local_time时保留REC_/DATA.BIN，避免对没有日期的数据虚构时间；显式提供非法时间则拒绝。历史REC_VERIFY保持，新时间名提供严格校验格式的只读REC_VERIFY_NAME入口，不能注入路径。

## 产物

- 源码：6ec57c38b1c73159ac987a34541ae0e5da681eab，构建开始工作区干净。
- 固件：1121952字节，SHA-256 `86a1c917f1267005d4de103fffdfb0162ad93691f7f37d4cb9be286a66407789`。
- 输入SHA-256：`653e7a0aaa30e68b65d66ae7a319c9400f601a1b95867896bfdc9b4381d80821`。
- 本地清单：`local-data/build/sensor-web/653e7a0aaa30e68b/20261018_160139_262370/build_manifest.json`，日志和artifacts在同目录。ESP-IDF5.4.2、PlatformIO6.2.0、espressif32@6.11.0。普通构建入口不访问设备。
- 与65bb1f8构建清单比对：SDK生成配置、分区及生成wifi_credentials.h哈希全部一致；空内置凭据不变，未改变主控NVS配置。构建静态RAM86428字节，不代表实际运行最低可用堆或栈验收。

## 检查与审查

实际record_name.h函数经临时宿主文件系统检查：日期/闰日/跨分钟日年、超过日期范围拒绝、同分钟三份序号、首份内容不被覆盖、查询错误立即停止、9999边界、路径缓冲限制、旧路径、排队和接管时间传播通过。首次边界测试缓冲小于固定后缀导致编译器format-truncation报错，调整测试目录缓冲边界用例后通过，未降低-Werror。

6项WebControl单元检查通过，包含命名原生测试、原控制状态和连续统计。既有controller_v2模型/双锁/实际USB写入512字节驱动替身和23项实际文件路由服务宿主检查通过。28项v2浏览器检查含本地时间提交、闰日/越界年、接管时间、实际路径显示和下载默认序号；25项原检测页回归通过。浏览器为本机Edge155.0.4283.62/Playwright1.62.1，均使用loopback替身，不访问设备。原始宿主输出在`local-data/validation/record-time-names/`，浏览器证据在`local-data/validation/controller-v2-ui/`和`sensor-web-ui/`。

project_check checkout模式和提交hook通过，原始冻结输入、默认上传guard未改。最终diff自行审查了路径分配/无覆盖、短临界区、时间传播、状态JSON边界、共享采样和下载调用。Code Review诊断工具对本PR返回“Can't connect to GitHub. Try again.”，无独立源码审查通过结论。[PR #21](https://github.com/zushhx/breadboard_engineering_migrate./pull/21)保留草稿，未合main或其他稳定分支。

实机时间命名、同分钟两份、主控真实回读、复位及两次真断电均未测；不能以软件检查或构建代替验收。后续具体试烧需绑定上述候选与确认范围。

## 恢复位置

当前可用65bb1f80722193e92464ff3363de337784b24c13固件SHA-256 `71d54a1f1702ef75e7597db6da6d5c9661a53234bd24f52dd0b7584b2ff3faaf`已重新核对宿主产物，未覆盖。产物清单在`local-data/build/sensor-web/0dd68d1806c0b923/20261017_215904_173955/build_manifest.json`。原app/NVS前缀备份在`local-data/hardware/controller-v2/20261017_213827/controller_prefix.bin`，SHA-256 `48e0da0f4a735707b6fe12daf46a0859fa9796d9f40a19c68bd9aafcbe6fa4b1`；后续刷入前需保存当时最新app和NVS，不能用旧NVS覆盖用户此后改过的配网。

若后续授权试烧，拟仅写app0、做两份3秒新记录并完整回读，保留旧TF文件和新证据目录，异常恢复65bb1f8及该轮适用备份。未取得该候选试烧授权前不执行这些步骤。桌面0.3.0无需替换，源码可按提交回退，时间名数据不依赖候选固件才能由TF文件管理读出。
