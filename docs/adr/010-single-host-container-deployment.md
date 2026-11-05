# ADR-010：使用加固的单机容器部署

- 状态：已接受（回溯记录）
- 决策时间：约 2026-09-27
- 相关提交：`0b75cbf`

## 背景

服务需要长期运行、保留数据库、媒体和两个敏感 Telegram session。维护者要求项目能够在 Docker 中运行，目标环境为单机。

## 决策

- 使用单服务 Docker Compose，不设计多副本部署。
- 数据与 session 分别保存在 `tpby-data` 和 `tpby-sessions` 命名卷。
- 容器以固定非 root UID/GID 10001 运行。
- 根文件系统只读，`/tmp` 使用受限 tmpfs。
- 启用 `no-new-privileges` 并丢弃全部 Linux capabilities。
- 不开放网络端口；服务仅主动连接 Telegram。
- 首次账号登录通过交互式一次性容器完成，之后以 `restart: unless-stopped` 常驻运行。
- rebuild 前必须停止常驻服务，避免并发使用 SQLite 和 Telegram session。

## 结果

- 容器重建不会删除业务数据和登录 session。
- 安全基线缩小容器被利用后的权限范围。
- 单机和命名卷简化部署，但不提供主机故障高可用。
- 当前依赖没有 lockfile，基础镜像也没有固定 digest，镜像重建结果可能随时间变化。

## 证据与边界

- **事实：** Dockerfile、Compose 与 README 共同定义上述部署方式。
- **事实：** 开发线程记录了镜像构建、Compose 配置、非 root 权限和只读容器 CLI 验证。
- **事实：** 维护者确认目标是单机部署。
- **开放项：** 没有生产环境健康检查、资源上限和完整恢复演练记录。

## 2026-11-05 补充：多架构镜像发布与拉取部署

- **原因与范围：** 为 AMD64 和 ARM64 单机提供预构建镜像，避免每台部署机安装构建依赖；不改变单实例、账号职责、持久化目录或容器安全基线。
- 使用 GitHub Actions、QEMU 和 Buildx 构建 `linux/amd64`、`linux/arm64`，以同一多架构标签发布到 GHCR。ARMv7 不在支持范围。
- main 推送发布 `latest` 与提交 SHA 标签；`v*` 标签推送发布同名标签与 SHA 标签。PR 只构建、不发布；手动运行发布 SHA 标签，默认分支还更新 `latest`。
- 发布使用仅在构建 job 授予 Packages 写权限的 `GITHUB_TOKEN`。首次发布后需人工配置包的公开可见性，私有包部署需自行登录 GHCR；工作流不修改可见性或部署真实服务。
- Compose 默认镜像改为 `ghcr.io/svlic/tpby:latest`，允许通过 `TPBY_IMAGE` 选择版本标签、digest 或本地镜像。Docker 根据主机自动选择架构。
- 此规则取代 README 中默认 `docker compose build` 的部署方式：默认改为 `docker compose pull` 后启动；保留直接通过 Dockerfile 构建本地镜像的方式。原有命名卷与路径保持不变，无需 rebuild。
- **验证边界：** 工作流和 Compose 可在本地做静态检查；首次 GitHub Actions 执行、GHCR 发布和双架构构建结果仍需在提交并触发工作流后确认，不将配置文件存在视为镜像已发布。
- **仍然存在：** Python 依赖版本范围与基础镜像未锁定（F-010 / R-014）；使用 digest 可固定已发布产物，但不代表重新构建可复现。
