# Architecture Decision Records

本目录记录 TPBY 的重要架构决策。文档于 2026-09-27 根据完整 Git 历史、提交所引用的开发线程以及项目维护者访谈回溯建立。

## 历史证据边界

仓库只有四个提交。除初始提交外，三个实现提交是在功能完成后从同一最终工作树拆出的逻辑提交，并非开发过程的连续快照。因此 ADR 可以确认最终采用的技术和开发线程中明确记录的调整，但不能仅凭提交顺序证明详细的决策时间线。

文档使用以下证据分类：

- **事实**：由 Git、项目文档、提交所引用的开发线程或维护者确认直接支持。
- **推断**：可以由代码变化合理推测，但没有当时的直接说明。
- **开放项**：尚未决定，或缺少生产证据。

状态含义：

- **已接受（回溯记录）**：决策已被维护者确认，当前实现整体一致。
- **已接受（已实现）**：回溯时发现过实现偏差，决策已在后续修改中落实并验证。
- **已接受（待实现对齐）**：目标决策已确认，但当前实现仍存在明确偏差。

## 决策索引

| ADR | 标题 | 状态 |
| --- | --- | --- |
| [ADR-001](001-python-telethon-dual-account.md) | 使用 Python、Telethon 和双用户账号 | 已接受（回溯记录） |
| [ADR-002](002-sqlite-content-addressed-media.md) | 使用 SQLite 与按 SHA-256 寻址的媒体文件 | 已接受（回溯记录） |
| [ADR-003](003-hash-connected-routing.md) | 按媒体哈希连通关系和固定优先级路由 | 已接受（回溯记录） |
| [ADR-004](004-source-search-validation.md) | 严格搜索六位 code 并在本地二次校验 | 已接受（已实现） |
| [ADR-005](005-staged-group-replacement.md) | 使用 staged group 缓解跨系统非原子更新 | 已接受（回溯记录） |
| [ADR-006](006-telegram-human-control-plane.md) | 使用 Telegram 转发和删除作为人工控制面 | 已接受（回溯记录） |
| [ADR-007](007-idempotency-retry-failure.md) | 持久化幂等状态并对瞬时错误有限重试 | 已接受（已实现） |
| [ADR-008](008-channel-based-disaster-recovery.md) | 以 UP 和 BLACKLIST 作为灾难恢复来源 | 已接受（回溯记录） |
| [ADR-009](009-recent-date-bypass.md) | 最近 180 天日期 code 直接旁路到 DEAL1 | 已接受（已实现） |
| [ADR-010](010-single-host-container-deployment.md) | 使用加固的单机容器部署 | 已接受（回溯记录） |

## 主要历史提交

- [`0fdf085`](https://github.com/svlic/tpby/commit/0fdf085d9b5568cdea943275647f76d1c09270cf)：领域模型、持久化和路由服务。
- [`6ac39dc`](https://github.com/svlic/tpby/commit/6ac39dc496c78bc93857a7c532fde8c6d58dd5e4)：Telegram 集成、恢复和 CLI。
- [`0b75cbf`](https://github.com/svlic/tpby/commit/0b75cbf414f6cd2bb73c7cdc601a227e0ae4f6dd)：容器部署和运维文档。

实现差距和尚未解决的风险统一维护在[待修改功能与风险清单](../architecture-follow-ups.md)，避免在 ADR 中把计划误写成已完成事实。
