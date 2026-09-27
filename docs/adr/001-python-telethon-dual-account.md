# ADR-001：使用 Python、Telethon 和双用户账号

- 状态：已接受（回溯记录）
- 决策时间：约 2026-09-27
- 相关提交：`0fdf085`、`6ac39dc`

## 背景

服务需要搜索 Telegram 历史、接收新消息和删除事件、下载受保护的 SOURCE 媒体，并向多个业务频道发送、转发或删除消息。SOURCE 频道禁止原生 forward。

Reader 和 Writer 的划分是业务分工，而不是为水平扩展设计：Reader 负责读取、搜索、监听和下载；Writer 负责发送、重建和删除。可见未来仍按单实例和一对账号运行。

## 决策

使用 Python 3.11+ 和 Telethon 1.x，通过两个独立 MTProto 用户账号运行：

- Reader 可访问六个频道，并负责 SOURCE 媒体下载。
- Writer 不需要访问 SOURCE，负责目标频道写操作。
- 禁止 forward 的 SOURCE 媒体由 Reader 下载实际文件，再由 Writer 重新上传。
- 核心业务放在 domain、repository 和 service 层；Telethon 代码作为外部适配层。
- 业务层通过 `Publisher` 协议与 Telegram 发布实现解耦。

## 结果

- 用户账号能力支持历史搜索、原生转发来源识别和删除事件。
- 双账号权限与业务职责一致，但部署和 session 管理更复杂。
- 核心路由可用 fake publisher 做单元测试。
- 系统并未设计为多实例共享状态。

## 证据与边界

- **事实：** 技术栈和双客户端实现在 `pyproject.toml` 与 `src/tpby/telegram.py` 中。
- **事实：** 维护者确认双账号源于业务分工，且 SOURCE 禁止 forward。
- **事实：** 开发线程记录选择 Telethon 是为了用户账号历史搜索、删除事件、原生转发和双账号能力。
- **开放项：** 尚无真实 Telegram 环境的端到端验证记录。
