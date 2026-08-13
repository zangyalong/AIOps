# systemd / Docker 统一服务控制写网关

本目录为非 root `aiops` 用户安装一套固定的 root 包装器。AIOps 的三个服务控制 Tool 保持
`start_service`、`stop_service`、`restart_service`，根据服务目录中的运行时身份调用：

```text
start systemd nginx.service
stop docker orders-api
restart systemd mysqld.service
```

Docker Compose 用 `.env` 插值的配置变化不能通过普通 `docker restart`
生效。独立的 `recreate_compose_service` 高风险工具会调用：

```text
aiops-compose-recreate sub2api
```

网关不接受 Compose 路径、project、service 或附加 flags；它只从已授权容器的
`com.docker.compose.*` 标签反查原始项目，并限定目标 service 必须是单副本，再执行
`up -d --no-deps --force-recreate --no-build --pull never`。Compose 项目可以包含
数据库、Redis 等其他 service，`--no-deps` 会确保这些依赖不会随目标一起重建。
配置及父目录若可被 AIOps SSH 用户直接写入，网关会拒绝，避免绕过文件变更审批。

策略包装器只接受用户在服务目录明确开启或关闭控制授权后的白名单同步：

```text
add systemd nginx.service
remove docker orders-api
```

白名单 `/etc/aiops/service-control.allowlist` 每行保存一个带类型的固定身份：

```text
systemd:nginx.service
docker:orders-api
```

包装器会校验动作、运行类型、systemd 单元或 Docker 容器名、root 文件所有权和白名单；执行前
还会核对 unit 已加载或容器名称完全一致，执行后核对实际状态。systemd 停止只有
`inactive` 算成功，`failed` 会明确报错；Docker 停止只有 `exited` 算成功。

sudoers 只授权三个 root 所有的固定包装器，不开放任意 `systemctl`、Docker CLI 或 Shell。
`aiops-service-policy` 使用文件锁和同目录临时文件原子更新白名单，不执行任何启停操作；实际
控制仍必须经过独立的 Tool 人工审批。

初始白名单由目标服务器 `deploy.conf` 配置：

```ini
SERVICE_CONTROL_ALLOWLIST=systemd:bt.service,systemd:nginx.service,docker:orders-api
```

兼容旧的裸 `.service` 配置并自动规范为 `systemd:` 条目。留空表示拒绝全部服务控制。服务目录
页面开启授权时会通过策略网关增量同步；自动部署环境配置时也会从数据库授权状态重建完整白名单。
