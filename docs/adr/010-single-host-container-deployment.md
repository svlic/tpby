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
