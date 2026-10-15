# 工程工具

- `project_check.py`：核对冻结输入、迁移内容、当前文档链接、Git 文件范围及上传门槛。`--local` 追加本地备份与全局规则校验；`--output` 仅允许写入 local-data。
- `build_tf_baseline.py`：从冻结输入准备独立工作区并构建；`--prepare-only` 不运行编译器。输出在 local-data，不覆盖正常基线，也不上传设备。
- `operation_gate.py`：在硬件工具发现设备前核对批准记录、干净提交、动作、主控和输入哈希。它是防误操作辅助，不是不可绕过的身份认证。

硬件批准记录只能在用户确认具体操作之后创建，放在 `local-data/approvals/`，包含 `source_commit`、`operation`、`controller_mac`、`user_confirmation`、带时区的 `expires_at`（最多 24 小时）及 `files`（路径到 SHA-256 的映射）。不得为了让检查通过而自行编造批准。flash 需列出备份、分区表、应用、OTA 选择文件及适用的 bootloader；restore 需列出原始完整备份；backup/reset 也会影响运行状态，单独记录动作授权。

本地 Git hook 安装后辅助阻止在 main 直接提交和推送，执行文件检查；clone 后需重新安装，不能作为服务器保护或人工批准的替代。
