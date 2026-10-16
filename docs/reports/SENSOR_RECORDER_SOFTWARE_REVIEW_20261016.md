# IIS3DWB 连续记录软件复核

日期：2026-10-16。范围见[计划](../plans/SENSOR_TF_RECORDER_20261015.md)，当前事实见[STATUS](../STATUS.md)。用户在方案展示后要求“继续”，本次进入软件开发，不包含具体新候选试烧授权。

## 实现与来源

任务分支 `codex/sensor-tf-recorder` 从8bcd78c派生，独立工程复制7994b9d模块诊断源码后受控修改；原候选源码、输入与封存产物不覆盖。启动TF只读路径、原SDMMC四位/20 MHz/引脚/5秒等待保持原实现；新main仅增加REC命令分派、文案及调用新记录器。SDK/CPU/PSRAM/分区保持原配置；生成SDK键值和分区哈希由构建入口强制对照冻结基线。

新增：SPI8 MHz、FIFO单帧事务读取、CPU1采集任务、CPU0 INA任务、app_main唯一FatFS写入者、40×448帧有界池、失败锁定、完整回读、块CRC/文件SHA、固定字段文件协议、只读解析器与实机门槛。源码按ST DS12569 Rev8寄存器说明自有实现，未复制浮动第三方驱动。INA只在本次MCU首次显式REC_START写CONFIG/CAL，后续只核对，不自动修复。

文件头记录源码/构建输入、卡与主控、标称ODR/量程、INA换算参数和精度未验收标记。原始tag保留；只按类型核对，不把tag计数当全局样本序号。CRC在接收后生成，无法独立检出SPI线上已经发生的XYZ位错误，此限制写入操作文档。

## 已完成的软件检查

- 作者自行核对新旧main/modules实际差异、配置、总线归属、初始化及停止顺序；逐项检查单写入者、跨核所有权、done通知、队列满、文件短写/同步/关闭、错误锁定和读回。不是独立审查。
- Windows固定Python运行 `python -m unittest discover -s tests -v` 共31项通过。新增解析测试从实际C++ packed结构生成文件，跨语言校验字段/CRC、有符号值和INA换算；覆盖截断、损坏、序号、计数、错误尾部、尾部外多余字节和拒绝后不导出；同一C++夹具执行实际命令解析，检查容量溢出、范围、分隔符和非法路径。
- 新增guard验证隐式upload/erase/program拒绝；批处理单元测试确认无批准或scope错误在Session/设备发现前停止，终端通过条件必须同时包含完整读回、数量、哈希及关闭写门。
- 首次尝试WSL测试因未装原生C++编译器失败，未修改旧检查去跳过；随后复用项目已固定的Windows Zig0.13.0完成全部测试。原始两次输出本地保留，不将失败记录覆盖成成功。
- 第一轮开发构建（工作区非干净，非试烧产物）固定IDF5.4.2通过，SDK键值与分区匹配；代码后续审查修订后须以干净提交重新构建具体候选。首轮日志位于local-data/context-audit/20261016/recorder_initial_build.log。

本轮修订包括：严格REC_START分隔符及容量溢出拒绝；整轮INA全无效必须失败；头部补齐R100/Current_LSB/Power_LSB/实际地址；记录最终同步耗时与尾部写入前统计的边界。最终产物与CI在后续具体记录追加，不用开发构建哈希申请试烧。

## 最终软件候选及主机导出检查

从干净提交 `203a60b563708ec96d1e7a883faff059af4a9a7c` 构建通过，输入聚合 `98594a067b30c8bf84e7272b5df57dc34d532b37db86253248f415cd00771383`；固件387616字节，SHA-256 `f84a235cdde8ca5bf89a5c1bf1c78fc218a8a98d72ecd35d7c4a1777cb81b1ba`。生成SDK键值差异为空、分区哈希一致，静态RAM35648字节；动态池分配仍待运行检查。见[完整构建清单](SENSOR_RECORDER_BUILD_20261016.json)。构建清单的hardware_tested=false保持原始含义。

补充MCU-only复位后先只读核对保留CONFIG/CAL，再允许下一次显式REC_START；异常则停止，不重写初始化掩盖。加入对应门槛测试后总计32项通过。`sensor_recorder_batch.py --prepare-only`已核对原候选、正常恢复产物、新候选及输入哈希，不访问设备。

可选绘图环境单独安装于ignored local-data/tooling，锁定matplotlib3.10.7及依赖版本，不改PlatformIO环境。平台默认及应用捆绑Python原先均没有matplotlib，已补齐独立依赖。实际C++模拟文件通过完整校验、CSV导出及PNG生成，人工查看图像坐标/图例和输出；模拟文件仅2点用于协议与边界，绝非真实振动证据。文件SHA为 `1ef925271ab7cefa87f0d83c269842752d946e50dae8b60eff8454f5bdbe7931`，输出位于local-data/context-audit/20261016/simulated-export。CI与Code Review工具结果待PR准备后核验，未取得独立源码审查通过证据。

已建立[草稿PR #5](https://github.com/zushhx/breadboard_engineering_migrate./pull/5)，五项CI以其[检查页](https://github.com/zushhx/breadboard_engineering_migrate./pull/5/checks)对应HEAD结果为准。Code Review的pull_requests_checks调用返回“Can't connect to GitHub”，另用GitHub连接器读取CI，不能将后者当作独立源码审查。

本地证据SHA-256：32项测试日志 `544fd0289622d4bd81f59be746691ce1dfbbe40b07169a9320abd554bb3340f7`；干净构建日志 `0184aa50d6c4c25d597cc8aaa81d1472441f7d2eba19d533e600700f469d5652`；模拟PNG `ccb3ae56ae037840d9841a1b16b5526a209f18c91a16bbc355c9d05ad9b83299`。原始输出保留本地，不上传构建运行时或原始设备备份。

## 实机边界

31项测试不覆盖FreeRTOS调度、FIFO真实语义、8 MHz信号质量、TF长尾、可用内部堆/栈或满速无丢样。约127 KiB动态池只是预算，构建静态RAM占用不能代替运行分配。无INT服务和逐帧SPI事务能否达标属于主要待测项，不自动降采样/改PSRAM以通过。工作点±5%计数/时间门槛仅为功能检查，非采样时基校准；电压、电流计量精度仍未验收。

新批处理对受影响应用扇区做完整前缀备份，核对当前7994b9d/boot/分区，只写app0；故障保存文件和日志，按具体授权恢复备份并复核TF。当前未执行任何硬件操作，亦未声称新恢复流程已经实测。两次完整断电、MCU复位、5分钟记录和真实文件导出均待具体候选批准后实施。
