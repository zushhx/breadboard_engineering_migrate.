# 采集问题中文显示检查

2026-10-16，源码eb4aba6340ba26093cd8f1849714b8f335bf4792。修改仅为内嵌网页：按record_format.h实际十个位显示问题、组合错误和未知位；原始码收至诊断详情。零错误码不覆盖失败/拒绝原因；断线或boot变化显示未确认状态。未更改采集、TF、SDK、分区或网络协议。

25项浏览器模拟检查通过（Edge155.0.4283.45、Playwright1.62.1），含逐位、组合、未知位、零码失败/拒绝、待机、断线/重启及原控制/波形回归；页面异常0。原始结果及模拟截图保留local-data/validation/sensor-web-ui；截图已人工查看，未访问实机。

自行审查实际diff及枚举/调用路径，无发现；没有独立源码审查结论。project_check和diff空白检查通过。Code Review取得该源提交CI诊断：files-and-guards通过，其余构建当时进行中，不将其写成全套CI通过。

PowerShell7.6.6、espressif32@6.11.0、ESP-IDF5.4.2干净构建通过，静态RAM82604字节。产物988512字节，SHA-256 `400192859ee7e4ac3723120397af1e804bec8c0baea5545bac07b46f8c65dc16`；私有清单为local-data/build/sensor-web/2bdecf2aaa77fc65/20261016_210308_503769/build_manifest.json，产物带本地凭据不可上传。

当前主控仍保留1f69178；其fd9dd5d241301f68b56664c3fcf02cc8fc143a2bfcde5f57172570bb028aac54正常产物已重新核验摘要，接线/TF/恢复证据沿用UI_RAW_WINDOW_STORAGE_LIVE。新候选未烧录、未做实机验收。具体试烧授权后才备份当前0..0x102000前缀、仅写app0、单页面30秒定向检查及异常恢复，不删除/格式化卡、不做main合并或正式发布。prepare脚本默认300秒等旧整轮提案不是本轮范围。
