---
name: docker-compose-operations
description: 处理 Docker Compose 的变量插值、端口映射和单服务受控重建，避免错误使用普通 docker start。
version: 1.0.0
category: 容器编排
triggers:
  - docker compose
  - docker-compose
  - compose
  - recreate
  - 重建容器
  - 端口映射
  - force-recreate
  - SERVER_PORT
tools:
  - investigate
  - recreate_compose_service
  - patch_server_config
---

# Docker Compose 单服务重建

## 判断何时需要 recreate

Compose 在创建容器时计算 `.env` 插值、`ports`、环境变量、挂载和镜像配置。容器创建完成后，`docker start` 只是启动旧容器，`docker restart` 也只重启旧配置。普通 docker start/restart 不会应用新变量，也不会重新读取 Compose 项目的 `.env`。

如果用户修改了 `${SERVER_PORT}:8080` 一类宿主机端口变量，要应用新映射，语义上需要执行 `docker compose up -d --no-deps --force-recreate <service>`。AIOMS 中不要运行这条原始命令，要调用高风险受控工具 `recreate_compose_service`。

## AIOMS 流程

1. 用 `investigate(area="services", query="discover")` 或 `runtime.query="docker-all"` 查明准确容器名、状态和 Compose 身份。
2. 修改配置前用 `investigate(area="change", query="config-patch-preview")` 生成结构化预览；dotenv 的 `selector` 是变量名。把预览返回的哈希和差异原样交给 `patch_server_config`。
3. 文件写成功且用户明确要求配置生效时，单独调用 `recreate_compose_service(server_name=..., container_name=...)`。工具会从已授权容器的 Compose 标签反查项目、配置文件、工作目录和 service，只重建目标 service。
4. 同一项目还有 PostgreSQL、Redis 等其他 service 不是拒绝单服务重建的理由；`--no-deps` 保证不会主动重建依赖。
5. recreate 成功后重新调查 `docker-all`、`sockets`、容器日志和反向代理配置，只报告真实运行结果。

## 常见故障解释

- `port is already allocated`：旧容器配置仍绑定冲突端口，或新端口也被占用；先用 `sockets` 核验，不要反复 `start_service`。
- 缺少 Compose 标签：目标可能不是 Compose 容器，或容器元数据不完整；报告该事实，不猜项目路径。
- 服务名重名或标签映射不唯一：停止执行并报告准确歧义，不扩大到整个项目重建。

不得让用户提供路径来绕过标签反查，也不得建议 Agent 手工执行任意 `docker compose` 命令。
