# ADR-006：使用 Telegram 转发和删除作为人工控制面

- 状态：已接受（回溯记录）
- 决策时间：约 2026-09-27
- 相关提交：`0fdf085`、`6ac39dc`

## 背景

运营人员需要把 DEAL 提升为 UP、加入 BLACKLIST，或从 UP 隐藏单个媒体。项目不需要单独的管理后台、操作人审计或撤销流程。

## 决策

- 将 DEAL1/DEAL2 消息原生转发到 UP，表示把对应完整 logical group 提升为 UP。
- 将 DEAL1/DEAL2 消息原生转发到 BLACKLIST，表示将该组全部 hash 加入 blacklist。
- 使用 forward metadata 与本地 Telegram message index 定位源 group。
- 人工删除 UP 消息时，将对应 hash 写入 `hidden_media`。
- hidden hash 在后续归并中传递，并只保证在当前数据库生命周期内有效。
- 不提供 UP/BLACKLIST 到 DEAL 的反向迁移。
- 服务停机期间遗漏 UP 删除事件是可接受限制，不在启动时扫描补偿。

## 结果

- Telegram 本身同时承担展示界面和轻量人工控制面。
- 不需要额外管理 UI 和认证系统。
- 正确行为依赖 Telegram forward metadata、删除事件和本地 message index。
- 数据库丢失或事件遗漏时，hidden 状态可以丢失，媒体未来可能重新出现。

## 证据与边界

- **事实：** 转发、删除和 hidden 规则来自原始业务要求并已实现。
- **事实：** 维护者确认无需操作审计、撤销和反向迁移。
- **事实：** hidden 只在当前数据库生命周期内有效，停机遗漏删除事件可接受。
- **开放项：** 尚无真实 Telegram album 多事件行为的集成验证。
