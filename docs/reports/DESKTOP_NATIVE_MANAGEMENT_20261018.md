# 控制台内管理主控网络与TF交付

2026-10-18。用户明确变更原“TF仅网页”限制并确认实施；USB问题由用户解决，本轮不改端口或驱动。范围和回退见[方案](../plans/DESKTOP_NATIVE_MANAGEMENT_20261018.md)，当前使用入口只在[STATUS](../STATUS.md)维护。

## 版本与实现

构建源码d6efd0ab0286a1fed8f556ace57dba9b1317757e，桌面0.3.0，EXE147968字节，SHA-256 `a05dd3783e2b4dc9a2442f997577ba47d7a61fe67db9b545553859fc0e567936`。WPF/.NET Framework4.8、固定csc及程序集哈希不变。普通构建入口显式加入ManagementClient/ManagementUi和协议测试；安装入口新增受限版本目录名，拒绝覆盖已有目录。固件/SDK/引脚/分区/TF底层/采集逻辑没有修改或烧录。

网络表单直接调用现有v2网络接口，密码仅在内存中使用，不写设置/日志；Wi-Fi密码留空沿用原名称的设备密码，热点密码默认遮盖并可明确查看。保存只是接收配置，随后读取才确认实际状态和网址。网络扫描请求容许12秒，普通网络请求仍5秒（HttpClient外层上限15秒），避免扫描用时较长时误报失败；USB无DTR/RTS操作保持。

TF原生列表16项分页，双击/Enter进入目录，提供新目录/重命名/卡内文件和目录复制/电脑多文件上传及剪贴板粘贴/单文件下载/测量后确认永久删除/取消。电脑文件夹上传仍使用原网页。命令不自动重放，按boot/job/transfer绑定结果；切换设备清空旧选择和复制源，重启后重新读取。设备继续互斥采集与文件任务。下载写独立电脑临时文件，验证长度、偏移及SHA-256后原子发布或替换用户明确选择的目标；取消上传可能保留设备临时文件。

## 检查与实际范围

- `desktop-controller/build.ps1`干净构建通过，清单标记source_worktree_dirty=false。79项核心/管理协议通过，包括中文、UTF-8字节边界、分页、分块上传/下载、空文件、校验失败保留已有目标、不重放拒绝、任务变化不取消他人任务、上传取消、5.5秒模拟网络扫描。均为本机替身，不写主控。
- 19项离线WPF检查/九张截图通过，人工查看网络表单和1000×700 TF列表；新增按钮、切页、密码控件、任务中禁切连接/重叠操作、最小窗口可用性通过。
- `tools/project_check.py`checkout模式及提交hook通过；管理工作树缺少原工作区ignored资产，未重复运行已知不适用的`--local`或复制私人资产。最终构建清单内全部输入在安装后再次比对哈希一致。
- 现有v2正在采集时只读取状态和网络状态；首次独占串口探针被拒绝访问，未强行打开。15:38复核passed/errors0后旧程序退出，新程序从工程外启动，COM7实际连接通过。独立新管理客户端只读探针读取网络状态、根目录16项/有后页，探针内boot不变。安装版GUI连接、原生网络配置读取、原生TF列表读取分别操作并确认成功；未保存网络配置、未开始采集、未写/删TF。最终状态passed/errors0。
- 真实Wi-Fi连接切换、AP实际客户端接入、本版GUI上传/重命名/复制/删除、断电及长时验收未做。前轮固件验收不能扩大为本版GUI这些路径通过。

## 审查、证据及恢复

自行审查实际diff、共享传输、初始化、并发/取消和数据路径；处理了切换设备/重启后的陈旧选择、系统文件剪贴板覆盖TF复制意图、错误摘要保护旧目标、变更重放及扫描超时。没有独立源码审查结论。可用Code Review诊断工具返回“Can't connect to GitHub. Try again.”，不视为审查通过。[草稿PR #20](https://github.com/zushhx/breadboard_engineering_migrate./pull/20)针对当前任务分支，main未合并。

原始证据保留本任务工作树`local-data/desktop-controller/release-d6efd0a/`：build-manifest.json、test-results.txt、preview/preview-result.txt及九张图、device-readonly.json、management-readonly.json、installed-ui-readonly.json、desktop-install.json、primary-entry.json。目录和网络原始数据不提交代码仓库。独立probe增加的loopback诊断入口仅属于工具，不改变已交付EXE或构建输入。

旧桌面v2程序保持原目录；原快捷方式逐字节备份并校验后更新主入口，重复v3快捷方式移入备份避免桌面双入口。回退时先结束新版关联采集并等待保存，关闭新版，启动旧v2或恢复previous-entry内v2快捷方式。没有改固件或主控配置，因此本轮桌面回退不需要刷机或恢复TF。
