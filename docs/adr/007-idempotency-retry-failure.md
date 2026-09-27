# ADR-007：持久化幂等状态并对瞬时错误有限重试

- 状态：已接受（已实现）
- 决策时间：约 2026-09-27
- 相关提交：`0fdf085`、`6ac39dc`

## 背景

Telegram 事件可能重复送达，网络操作也可能暂时失败。业务歧义不能靠盲目重试解决，但 timeout、限流和网络错误不应第一次发生就永久阻止消息。

## 决策

- 以 `(code_chat_id, code_message_id)` 作为输入消息幂等键。
- 成功 task、最终 failure 和日期旁路 bypass 都持久化，并阻止同一消息重复处理。
- 找不到 source、对象超过两个、编号校验失败或验证视频数量不为一，属于确定性业务错误，不自动重试。
- Telegram timeout、短期限流、下载失败和上传网络错误属于瞬时错误，应执行有限次数、等待后自动重试。
- 重试耗尽后写入 failure，作为需要人工重新触发的 dead-letter 状态。
- 具体重试次数和退避参数留给实现确定，不属于本 ADR 的稳定业务语义。

## 实现

Reader 和 Writer 都显式配置 Telethon 请求级重试：每个 request 最多重试 3 次，连接重试 5 次，连接重试间隔 2 秒，10 秒以内的 flood wait 由 Telethon 对同一个 request 等待后重试，发送请求的 random ID 因而保持不变。更长的 `FloodWait` 由 Reader/Writer 各自的应用层门控统一等待：门控将并发减半、阻止同账号新 I/O，并在等待结束后重试该高层操作。

CODE 输入在开始 Telegram I/O 前先写入 SQLite `code_jobs`。准备 worker 异常退出或进程重启时，`running` job 会恢复为 `pending`；成功 task、确定性 failure 或 bypass 持久化后才删除 job。启动时在事件 handler 注册后显式执行 catch-up，遗漏或重复更新仍由消息幂等键约束。

`AmbiguousSourceError` 作为确定性业务拒绝单独处理，不进入任何重试循环。普通瞬时 Telegram I/O 错误由客户端请求层承担；超过 10 秒的 `FloodWait` 只重试门控中的单次搜索、下载、上传分块或删除操作，不会从 source 搜索开始盲目重放已经产生部分副作用的整个 task。

## 结果

- 幂等记录降低重复发送风险。
- 瞬时故障可以自动恢复，确定性错误仍保持 fail-closed。
- 必须谨慎处理已经产生部分 Telegram 副作用的重试，避免重复上传。
- failure 仍需要明确的人工重放入口；当前只能直接修改数据库并重新投递。

## 证据与边界

- **事实：** task、failure、bypass 表和 processed 检查已经实现。
- **事实：** README 规定 failure 阻止自动重复处理。
- **事实：** 维护者确认需要对瞬时错误有限重试，耗尽后进入 failure。
- **事实：** 当前实现显式设置有限请求/连接重试、持久化 CODE job 和自适应 FloodWait 门控，并有自动化测试覆盖。
- **开放项：** 人工重放 failure 的正式入口尚未实现。
