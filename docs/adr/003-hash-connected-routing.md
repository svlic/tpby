# ADR-003：按媒体哈希连通关系和固定优先级路由

- 状态：已接受（回溯记录）
- 决策时间：约 2026-09-27
- 相关提交：`0fdf085`

## 背景

一个业务 task 包含完整 SOURCE 媒体，其中恰好一个媒体标记为 `deal_media`。业务判断必须使用完整媒体集合，但 DEAL 频道只展示 `deal_media`，UP 展示未被隐藏的完整媒体。

不同 task 只要共享媒体 hash，就可能属于同一个传递连通的 logical group。

## 决策

严格按以下顺序处理新 task：

1. 任一 hash 命中 BLACKLIST：task 进入 BLACKLIST，并将当前 task 的全部 hash 扩散进 blacklist。
2. 任一 hash 命中活动 UP：将新 task 以及所有传递连通的 UP、DEAL1、DEAL2 归并为 UP。
3. 没有 UP，但存在历史重复 hash：与所有传递连通的 DEAL1、DEAL2 归并为 DEAL2。
4. 没有历史重复：创建独立 DEAL1。

连通关系通过反复扩展 group 的全部 hash 计算，直到没有新 group。一个新 task 同时连接两个原本独立的 UP 时，应把两个 UP 合并。媒体排列顺序不是业务数据。

## 结果

- BLACKLIST、UP 和历史重复具有明确且可测试的优先级。
- logical group 表达传递关系，而不是只记录直接匹配。
- 领域层保留全部 SOURCE 媒体，使 DEAL 后续提升到 UP 时可以恢复完整内容。
- 归并可能使 group 持续变大，查询和重新上传成本随连通分量增长。

## 证据与边界

- **事实：** 优先级和传递归并来自原始业务要求，并由 `RoutingService` 实现。
- **事实：** 测试覆盖跨 DEAL 组归并、UP 吸收 DEAL 和 blacklist 扩散。
- **事实：** 维护者确认连接两个 UP 时应合并，媒体顺序不属于业务数据。
- **开放项：** 尚无大连通分量在生产数据上的性能记录。
