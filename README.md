# TPBY

基于两个 Telegram 用户账号的媒体分流服务。Reader 负责监听、搜索和下载，Writer 负责发送、重建和删除。业务状态保存在 SQLite，媒体以文件内容 SHA-256 为名称去重保存。

## 已实现业务

- 按独立六位数字规则提取 code，并对过去六个月内的合法 `YYMMDD` 执行 CODE → DEAL1 原生转发旁路。
- 优先使用带引号的 `"编号：{code}"` 精确搜索，必要时尝试带引号的裸 code；所有结果都再次执行本地 code 边界校验。
- 识别 Telegram album，下载全部 source media，仅将真正带有 `(验证视频)` caption 的媒体作为 `deal_media`。
- 严格执行 `BLACKLIST > UP > 历史重复 > DEAL1/DEAL2`。
- Blacklist 整组扩散、metadata 文本（JSON 内容）、UP 传递归并、DEAL1 升级和 DEAL2 归并。
- DEAL Telegram 只展示每个 task 的 `deal_media`；UP 展示完整 source media，按 SHA-256 去重。
- 人工原生 Forward DEAL → UP / BLACKLIST。
- 识别人工删除的 UP 媒体并保留 hidden hash；程序重建产生的删除不会被误判。
- `--rebuild` 从 UP 实际媒体和 BLACKLIST 媒体/metadata 重建长期核心状态。

## 关键安全策略

同一 code 按业务约束不会重复出现。搜索结果经过本地 code 校验后按业务对象（单消息或 album）归组，正常结果必须为 1–2 个对象。以下情况不会猜测或发送内容，而是写入 `failures` 表：

- 二次验证后仍超过 2 个业务对象；
- 没有 `(验证视频)`；
- 存在多个 `(验证视频)`；
- 下载或 Telegram 操作失败。

失败记录会阻止同一 CODE 消息被重复处理；确认数据后可删除对应 `failures` 行再重新投递消息。

## Docker 部署（推荐）

需要 Docker Engine 和 Docker Compose。先创建配置：

```bash
cp .env.example .env
# 编辑 .env，填写 API 凭据和六个频道 ID
docker compose build
```

首次运行需要交互登录两个 Telegram 用户账号：

```bash
docker compose run --rm tpby
```

依次完成 Reader、Writer 的手机号、验证码及可能的 2FA 验证。日志出现 `tpby is listening` 后说明两个 session 已保存，可按 `Ctrl+C` 退出一次性容器，然后启动后台服务：

```bash
docker compose up -d
docker compose logs -f tpby
```

停止或更新：

```bash
docker compose down
docker compose build --pull
docker compose up -d
```

Compose 使用命名卷 `tpby-data` 和 `tpby-sessions`，删除或重建容器不会丢失数据库、媒体和登录 session。不要使用 `docker compose down -v`，除非明确需要删除全部本地状态。容器以非 root 用户运行、根文件系统只读，且不开放任何网络端口。

执行灾难恢复前必须先停止常驻服务，避免同时使用数据库和 Telegram session：

```bash
docker compose stop tpby
docker compose run --rm tpby --rebuild
docker compose up -d
```

## 本机安装

需要 Python 3.11+。

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env
```

在 <https://my.telegram.org> 创建应用并填写 `TPBY_API_ID` / `TPBY_API_HASH`，再填写六个频道 ID。频道 ID 推荐使用 `-100...` 格式。

账号权限：

- Reader：可访问并读取全部六个频道，能下载 source 媒体和接收删除事件。
- Writer：可访问 code、blacklist、up、deal1、deal2，并拥有发送和删除消息权限；不需要访问 source。

## 本机首次登录与启动

配置文件不会由程序自动读取，使用 shell 导出后启动：

```bash
set -a
source .env
set +a
.venv/bin/tpby
```

首次运行会依次要求 Reader 和 Writer 的手机号、验证码（以及可能的 2FA 密码），随后在 `sessions/` 保存会话。生产环境建议先在交互终端完成首次登录，再交由 systemd 等进程管理器运行。

## 数据目录

- `data/tpby.sqlite3`：任务、hash、logical group、Telegram 消息索引和 hidden 状态。
- `data/media/`：按 SHA-256 去重后的 source 媒体。
- `sessions/`：两个 Telegram 用户会话，必须作为敏感文件保护和备份。

处于 DEAL 的完整媒体应保留，以支持未来提升到 UP。进入 UP / BLACKLIST 后当前版本不会主动清理文件，优先保证可恢复性；可在确认备份后按数据库引用另行清理。

## 灾难恢复

`--rebuild` 会先完整扫描并下载 UP 与 BLACKLIST，再**替换本地数据库中的全部状态**：

```bash
.venv/bin/tpby --rebuild
```

UP 以 caption 中完整 code list 相同的 albums 归为一个 logical group；BLACKLIST 合并实际媒体 SHA-256 和本程序生成的 metadata `.txt`。按需求，DEAL 历史和已删除媒体的 hidden 状态不恢复。执行前应备份数据库。

## 测试

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

测试覆盖 code/date 规则、DEAL1、DEAL2 传递归并、Blacklist 扩散、UP 吸收 DEAL、hidden 状态和人工迁移的完整 hash 集合。

## 当前一致性边界

SQLite 变更和 Telegram RPC 无法组成原子事务。实现遵循“先创建完整的新 UP/DEAL2，再删除旧消息”，避免失败时丢失旧频道内容；极端 RPC 半完成的自动修复按需求暂缓。所有业务操作在进程内串行执行，避免监听事件与程序自身重建互相竞争。
