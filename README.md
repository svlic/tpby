# TPBY

基于两个 Telegram 用户账号的媒体分流服务。Reader 负责监听、搜索和下载，Writer 负责发送、重建和删除。业务状态保存在 SQLite，图片按感知哈希 pHash 去重，视频继续按文件内容 SHA-256 去重保存。

## 已实现业务

- 按独立六位数字规则提取 code，并对过去 180 天内的合法 `YYMMDD` 执行 CODE → DEAL1 原生转发旁路。
- 使用带引号的裸六位 code 精确搜索；所有结果都在本地再次校验“编号”字段和 code 边界。
- 识别 Telegram album，下载全部 source media，仅将真正带有 `(验证视频)` caption 的媒体作为 `deal_media`。
- 严格执行 `BLACKLIST > UP > 历史重复 > DEAL1/DEAL2`。
- Blacklist 整组扩散、metadata 文本（JSON 内容）、UP 传递归并、DEAL1 升级和 DEAL2 归并。
- DEAL Telegram 只展示每个 task 的 `deal_media`；UP 展示完整 source media，图片按 pHash、视频按 SHA-256 去重。
- 人工原生 Forward DEAL → UP / BLACKLIST。
- 识别人工删除的 UP 媒体并保留 hidden hash；程序重建产生的删除不会被误判。
- `--rebuild` 从 UP 实际媒体和 BLACKLIST 媒体/metadata 重建长期核心状态。

## 关键安全策略

同一 code 按业务约束不会重复出现。搜索结果经过本地 code 校验后按业务对象（单消息或 album）归组，正常结果必须为 1–2 个对象。以下情况不会猜测或发送内容，而是写入 `failures` 表：

- 二次验证后仍超过 2 个业务对象；
- 没有 `(验证视频)`；
- 存在多个 `(验证视频)`；
- Telegram 内部错误、短期限流或连接失败在有限次请求级重试后仍未恢复。

失败记录会阻止同一 CODE 消息被重复处理；确认数据后可删除对应 `failures` 行再重新投递消息。

## Docker 部署（推荐）

需要 Docker Engine 和 Docker Compose。使用预构建镜像 `ghcr.io/svlic/tpby:latest`，同一标签包含 `linux/amd64` 和 `linux/arm64`（ARM64 / AArch64，不含 ARMv7），Docker 会自动选择主机架构，无需本机构建。

先下载本仓库的 `compose.yaml` 和 `.env.example` 到同一目录（也可克隆仓库），再创建配置：

```bash
cp .env.example .env
# 编辑 .env，分别填写 Reader、Writer 的 API 凭据和六个频道 ID
docker compose pull
```

默认跟随 main 的 `latest` 镜像；生产环境可在 `.env` 中设置 `TPBY_IMAGE=ghcr.io/svlic/tpby:v0.1.0`（示例，需该标签已发布），或使用 `ghcr.io/svlic/tpby@sha256:...` 固定镜像 digest。升级前应备份数据库、媒体和 session。

### 镜像发布与访问权限

GitHub Actions 的 [Docker image 工作流](.github/workflows/docker.yml) 使用 QEMU 和 Buildx 构建两个架构并发布到 GHCR：

- 推送到 `main`：发布 `latest` 和 `sha-<短提交号>`。
- 推送 `v*` 标签：发布同名标签（如 `v0.1.0`）和 `sha-<短提交号>`，不会覆盖 `latest`。
- Pull Request：仅验证两个架构的构建，不登录或推送镜像。
- 手动运行：发布对应提交的 SHA 标签；在默认分支运行时也更新 `latest`。

工作流使用自带的 `GITHUB_TOKEN`，不需要额外配置发布密钥；仓库须允许 Actions 写入 Packages。首次发布后，维护者应在 GitHub Packages 中将该镜像包的可见性设为 **Public**，才能匿名拉取。若保留为私有包，部署机需先使用具有 `read:packages` 权限且有权访问该包的令牌执行 `docker login ghcr.io`。Fork 后镜像发布到 fork 所属仓库路径，需相应修改 `.env` 中的 `TPBY_IMAGE`。

### 首次登录与启动

首次运行需要交互登录两个 Telegram 用户账号：

```bash
docker compose run --rm tpby
```

依次完成 Reader、Writer 的手机号、验证码及可能的 2FA 验证。日志出现 `tpby is listening` 后说明两个 session 已保存，可按 `Ctrl+C` 退出一次性容器，然后启动后台服务：

```bash
docker compose up -d
docker compose logs -f tpby
```

更新镜像并重建服务容器（若固定版本，先修改 `.env` 中的 `TPBY_IMAGE`）：

```bash
docker compose pull
docker compose up -d
```

停止服务使用 `docker compose down`。

Compose 使用命名卷 `tpby-data` 和 `tpby-sessions`，删除或重建容器不会丢失数据库、媒体和登录 session。不要使用 `docker compose down -v`，除非明确需要删除全部本地状态。容器以非 root 用户运行、根文件系统只读，且不开放任何网络端口。

执行灾难恢复前必须先停止常驻服务并备份数据；`--rebuild` 会替换本地数据库状态，不是无损迁移：

```bash
docker compose stop tpby
docker compose run --rm tpby --rebuild
docker compose up -d
```

### 可选：本地构建

在仓库目录执行以下命令构建当前主机架构的镜像，并通过同一份 Compose 启动。后续命令也需保留 `TPBY_IMAGE=tpby:local`，且不要执行 `docker compose pull`：

```bash
docker build --pull -t tpby:local .
TPBY_IMAGE=tpby:local docker compose run --rm --pull never tpby
TPBY_IMAGE=tpby:local docker compose up -d --pull never
```

## 本机安装

需要 Python 3.11+。

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env
```

分别为两个账号在 <https://my.telegram.org> 创建应用并填写 `TPBY_READER_API_ID` / `TPBY_READER_API_HASH` 和 `TPBY_WRITER_API_ID` / `TPBY_WRITER_API_HASH`，再填写六个频道 ID。频道 ID 推荐使用 `-100...` 格式。

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
- `data/media/`：图片按 pHash、视频按 SHA-256 去重后的 source 媒体。
- `sessions/`：两个 Telegram 用户会话，必须作为敏感文件保护和备份。

处于 DEAL 的完整媒体应保留，以支持未来提升到 UP。进入 UP / BLACKLIST 后当前版本不会主动清理文件，优先保证可恢复性；可在确认备份后按数据库引用另行清理。

## pHash 规则与旧版升级

图片先应用 EXIF 方向，再计算 64 位 pHash，标识为 `image-` 加 16 位十六进制值。视频保持原有的逐字节 SHA-256（64 位十六进制字符串），不抽帧、不解码，也不需要 FFmpeg。非图片文件仍使用 SHA-256。

分组、黑名单、hidden 和文件复用按 **对应哈希完全相等** 判重，图片未引入汉明距离阈值。pHash 不是密码学哈希，可能将不同图片识别成同一媒体，复用首次保存的文件；压缩或裁剪也不保证一定命中。视频只有文件内容相同才命中。

旧数据库可以继续打开，视频的历史、黑名单和文件缓存保持兼容。旧图片的 SHA-256 记录不会自动转换，也不会与新图片 pHash 匹配；仅凭摘要无法转换，必须有原图片才能重新计算。

新 BLACKLIST metadata 带有 `hash_algorithm: "image-phash-video-sha256-v1"`，同时保存图片 pHash 和视频 SHA-256；旧版 metadata 的 SHA-256 仍会读取，以保留视频黑名单。旧图片黑名单必须从原图片重新计算，不能只改算法标签。`--rebuild` 会从 UP / BLACKLIST 实际图片计算 pHash，但仅存在于旧 metadata 中的图片摘要不能恢复为 pHash。重建前应备份数据库；该流程不恢复 DEAL 历史、hidden 或队列，不能作为无损迁移。SQLite 内部保留 `sha256` 列名，但它现在也存放带 `image-` 前缀的图片 pHash。

## 并发与流控

CODE 更新先写入 SQLite 持久化队列，再由准备 worker 并行搜索、下载和计算 hash；进程退出时尚未完成的 job 会在下次启动继续。可能修改 logical group 的路由决策、上传、激活和删除仍串行执行，避免两个 hash 连通任务基于同一旧状态同时发布。

Reader 和 Writer 分别使用独立的自适应 I/O 门控：从并发 1 开始，连续成功后逐步增加，遇到 Telegram `FloodWait` 后立即降低并发，并按服务端要求的时间统一冷却。单个 CODE 内的媒体可以并行下载，但不会超过 Reader 上限；hash 计算由独立上限控制。默认上限可通过以下变量调整：

- `TPBY_SOURCE_PREPARE_CONCURRENCY=4`
- `TPBY_READER_IO_CONCURRENCY=4`
- `TPBY_MEDIA_HASH_CONCURRENCY=2`
- `TPBY_WRITER_IO_CONCURRENCY=2`

这些值是安全上限而非启动并发，也不是 Telegram QPS；不建议仅为追求速度继续增大。Writer 的状态变更目前仍保持串行，即使其 I/O 上限更高。

## 灾难恢复

`--rebuild` 会先完整扫描并下载 UP 与 BLACKLIST，再**替换本地数据库中的全部状态**：

```bash
.venv/bin/tpby --rebuild
```

UP 以 caption 中完整 code list 相同的 albums 归为一个 logical group；BLACKLIST 合并实际媒体的对应哈希和本程序生成的 metadata `.txt`。按需求，DEAL 历史和已删除媒体的 hidden 状态不恢复。执行前应备份数据库。

## 架构文档

- [Architecture Decision Records](docs/adr/README.md)
- [待修改功能与风险清单](docs/architecture-follow-ups.md)

## 测试

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

测试覆盖 code/date 规则、DEAL1、DEAL2 传递归并、Blacklist 扩散、UP 吸收 DEAL、hidden 状态和人工迁移的完整 hash 集合。

## 当前一致性边界

SQLite 变更和 Telegram RPC 无法组成原子事务。实现遵循“先创建完整的新 UP/DEAL2，再删除旧消息”，避免失败时丢失旧频道内容；已确认的分块上传若后续失败会尽力回收，但进程在 RPC 成功后、SQLite 提交前退出造成的极端半完成仍缺少自动 reconciliation。搜索、下载和 hash 可以受控并行，所有 logical group 状态变更仍在进程内串行执行。
