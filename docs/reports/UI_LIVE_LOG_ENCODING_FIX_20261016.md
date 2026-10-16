# UI联机工具控制台编码故障

2026-10-16，操作提交ad17dd8，用户授权的f090279/428d0933…1b93a5ff候选已写入并由esptool确认摘要。随后Windows平台Python默认GBK标准输出无法打印串口解码中的U+FFFD替代字符，tf.Session.emit抛出UnicodeEncodeError；异常报告再次打印同字符，导致自动恢复分支未进入。没有发出REC_START，没有30秒采集文件；该轮不能记为实机通过，也没有证据认定固件故障。

最小现场在`local-data/hardware/ui-live/20261016_193433/`：1MiB前缀备份、backup.json、hardware.log及result.json。按本轮已授权恢复范围，独立重新验证批准记录和备份/旧应用/bootloader/分区后，恢复备份中的原应用扇区。恢复写入摘要、9c906cc身份、TF容量/参考文件两次摘要及HTTP空闲复核通过，证据在同目录host-error-recovery/。恢复后主控仍供电、TF及原接线不变。

修复分支codex/fix-ui-live-log-encoding只改本次联机工具：入口固定UTF-8并允许替换；专用Session先写UTF-8原始日志，再尽力输出终端，Unicode/OSError不再中断恢复。新增测试强制控制台抛UnicodeEncodeError，确认日志完整且emit不向恢复路径抛错。未改共享Session、固件、采集配置或指定候选。

根因限本次主机工具编码和异常处理缺口。修复按用户故障快捷流程完成定向检查、自行审查后通过PR合回UI任务分支；不合入main、不发布。原失败证据保留，后续重试单独保存结果；首次试烧未验证传感器采集或功能。
