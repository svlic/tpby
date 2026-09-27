# ADR-008：以 UP 和 BLACKLIST 作为灾难恢复来源

- 状态：已接受（回溯记录）
- 决策时间：约 2026-09-27
- 相关提交：`0fdf085`、`6ac39dc`、`0b75cbf`

## 背景

本地 SQLite 可能丢失。业务要求恢复长期有效的 UP 和 BLACKLIST 状态，但不要求完整恢复 DEAL 历史、failure、bypass 或人工删除形成的 hidden 状态。

## 决策

- 将 UP 和 BLACKLIST 频道视为灾难恢复的权威来源。
- `--rebuild` 先扫描并下载频道内容，再替换本地数据库全部状态。
- UP 以 caption 中完整 code list 相同的 albums 恢复为一个 logical group。
- 暂时假定不会存在两个 code list 完全相同但彼此独立的 UP group。
- BLACKLIST 从实际媒体和 metadata 文档共同恢复 hash。
- metadata 使用 `.txt` 文件承载 JSON，包含 `version`、`codes` 和 `hashes`。
- 遇到未知 metadata 版本时，只要结构可读，就尽量读取 `hashes` 并忽略未知字段。
- rebuild 不恢复 DEAL、hidden、failure 和 bypass，并且必须在常驻服务停止后运行。

## 结果

- 即使本地数据库完全丢失，也能恢复长期核心状态。
- Telegram caption 和 metadata 成为恢复协议的一部分，修改格式必须考虑向后兼容。
- hidden 丢失是明确接受的恢复边界。
- rebuild 是破坏性操作；本地数据库清空后若重建写入失败，当前实现没有自动回滚到旧数据库。

## 证据与边界

- **事实：** 扫描、metadata 解析和 destructive reset 已实现并有运维说明。
- **事实：** 维护者确认 UP/BLACKLIST 是权威恢复来源，并选择对未知字段宽容读取。
- **事实：** `.json` 文件名曾在开发中改为需求指定的 `.txt`，内容仍为 JSON。
- **开放项：** 尚未通过真实频道执行完整灾难恢复演练。
- **开放项：** 当前没有临时数据库构建后原子替换的保护。
