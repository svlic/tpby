# TPBY 开发约定

本文件适用于整个仓库。项目是单机运行的 Python / Telethon 双账号 Telegram 媒体分流服务，业务状态保存在 SQLite，媒体保存在本地文件系统。

## 开始修改前

- 阅读 `README.md`、涉及模块的代码与测试，以及相关的 `docs/adr/` 文档。
- 用 `git status --short` 确认已有改动；不要覆盖或回退用户及其他代理的修改。
- 区分用户明确要求、当前实现、历史决策和未实现计划。发现冲突时说明差异，不要把历史 ADR 当作代码现状，也不要未经授权改变业务规则。
- 优先做最小完整修改，沿用现有模块边界，不顺带实现 `docs/architecture-follow-ups.md` 中的其他功能。

## 模块职责

- `src/tpby/domain.py`：code/date 规则、媒体和任务模型、logical group 展示规则。
- `src/tpby/repository.py`：SQLite schema、事务、持久化队列、哈希关联和消息索引。
- `src/tpby/service.py`：路由优先级、传递归并、人工提升/拉黑、hidden 和分组替换。
- `src/tpby/telegram.py`：Reader/Writer、搜索验证、媒体下载与哈希、流控、事件处理和恢复。
- `src/tpby/config.py`、`cli.py`：环境变量与命令行入口。
- `tests/`：pytest / pytest-asyncio 测试。

## 必须保持的业务边界

- Reader 搜索、下载和接收事件；Writer 发送、重建和删除。不要混用两个账号的职责。
- 路由优先级为 `BLACKLIST > UP > 历史重复 > DEAL1/DEAL2`，按完整 SOURCE 媒体集合计算传递连通关系。
- DEAL 只展示每个 task 的 `deal_media`；UP 展示未隐藏的完整媒体，并去重。
- **图片使用 pHash，视频保留逐字节 SHA-256。** 当前图片按 pHash 完全相等判重；未经明确要求，不引入汉明距离阈值，也不改为视频抽帧哈希。
- 更改哈希或媒体身份规则时，必须检查文件命名/复用、历史匹配、分组、blacklist、hidden、Telegram 消息索引与 rebuild，不能只替换计算函数。
- SOURCE 搜索结果必须在本地再次验证编号、code 边界、业务对象数量和唯一验证视频；遇到歧义记录 failure，不猜测。
- 最近日期 code 的旁路窗口是固定 180 天，默认时区为 `Asia/Shanghai`，不是六个日历月。
- 搜索、下载和哈希可以受控并行；路由决策、发布、分组激活/停用与旧消息删除通过 mutation lock 串行执行。
- 分组替换先发布完整新内容、保存索引并激活，再删除旧消息。保留程序删除与人工删除的区分，避免误写 hidden。

## 文档同步是完成条件

**涉及架构、业务语义、持久化、兼容性、恢复协议、并发边界或部署方式的变更，必须在同一次任务中同步相关 ADR。只更新代码或 README 不算完成。**

- 媒体身份、哈希和存储：检查 ADR-002；匹配与连通规则同时检查 ADR-003。
- 搜索校验、日期旁路：分别检查 ADR-004、ADR-009。
- 分组替换、人工控制、幂等与重试：分别检查 ADR-005、ADR-006、ADR-007。
- metadata 格式、兼容性和 rebuild：检查 ADR-008。
- 账号职责、运行栈和部署：检查 ADR-001、ADR-010。
- 保留历史决策的背景与证据。决策变化应注明变更日期、原因、适用范围和取代的旧规则；需要独立决策记录时新增 ADR 并链接原 ADR，不把新决策伪装成历史事实。
- ADR 新增、标题或状态变化时，同步 `docs/adr/README.md` 索引。
- 操作方式、依赖、配置、用户可见行为或升级步骤变化时，同步 `README.md`；配置与部署还需检查 `.env.example`、`Dockerfile` 和 `compose.yaml`。
- 变更使已知风险或后续计划发生变化时，同步 `docs/architecture-follow-ups.md`；不要把未实现或未验证的内容标记为已完成。
- 完成前逐项核对代码、README、相关 ADR、ADR 索引和风险清单是否一致。若没有文档影响，明确说明理由，不为凑数修改无关文档。

## 安装与验证

需要 Python 3.11+。本机开发使用虚拟环境：

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
git diff --check
```

- 局部改动先运行相关测试；涉及共享模型、哈希、路由或持久化时运行完整测试集。
- 优先扩展现有测试。回归测试应检查用户可见结果，选择能区分新旧行为的输入；不要只验证没有异常。
- 媒体哈希变更至少验证图片判重、视频 SHA-256 不变，以及相关持久化/恢复兼容性。
- 纯文档修改核对事实、链接和 diff 即可，无需运行无关测试。
- 自动化测试不等于真实 Telegram 端到端验证。报告执行过的检查和未验证的边界，不声称未经验证的结果。

## 数据与操作安全

- `.env`、`sessions/`、Telegram API 凭据、手机号和会话文件都是敏感信息；不得提交或写入日志与测试夹具。
- 不擅自删除数据库、媒体、会话或 Docker volumes，不运行 `docker compose down -v`。
- `--rebuild` 会替换本地数据库状态，且不完整恢复 DEAL 历史、hidden、failure、bypass 或队列。执行前必须停止常驻服务、备份数据并获得用户授权；不要为了验证代码而操作真实频道或生产数据。
- SHA-256 摘要不能直接转换为图片 pHash；不得通过改标签冒充迁移，不得静默丢弃无法恢复的旧记录。
- 未经用户要求，不提交、推送、部署或修改真实 Telegram 频道。最终说明改动是仅在本地，还是已提交、推送或部署。
