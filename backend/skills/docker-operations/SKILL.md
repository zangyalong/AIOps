---
name: docker-operations
description: 调查 Docker 容器状态、资源、日志与进程，并通过 AIOMS 受控工具启动、停止或重启已授权容器。
version: 1.0.0
category: 容器运行时
triggers:
  - docker
  - 容器
  - container
  - docker ps
  - docker logs
  - docker stats
  - 镜像
tools:
  - investigate
  - start_service
  - stop_service
  - restart_service
---

# Docker 运维

## 先调查，再改变

1. 不知道目标容器时，先调用 `investigate(area="services", query="discover")` 发现服务器上的运行对象；需要目录登记关系时用 `query="catalog"`。
2. 查看运行中容器使用 `investigate(area="runtime", query="docker")`；需要包含停止容器时使用 `docker-all`；资源快照使用 `docker-resources`。
3. 对单个容器查看进程使用 `container-top:<准确容器名>`，查看最近日志使用 `container-logs:<准确容器名>`。
4. 端口故障还要调查 `runtime.query="sockets"`，把宿主机监听事实与容器端口映射分开判断。

这些选择器分别对应常见只读方法 `docker ps`、`docker ps -a`、`docker stats --no-stream`、`docker top` 和 `docker logs --tail 100`。在 AIOMS 对话中应使用 `investigate`，不要把原始 Docker CLI 当成写执行通道。

## 受控变更

- 只有用户明确要求改变运行状态，并且已经查明服务器、完整容器名与当前状态后，才调用 `start_service`、`stop_service` 或 `restart_service`，参数 `runtime_type="docker"`。
- 启动、停止、重启只操作服务目录中已经授权的准确容器；写工具会独立进入审批，不能和调查工具放在同一条模型消息中。
- 普通 `docker start/restart` 不会重新读取 Compose 的 `.env` 或重新计算端口映射。涉及 Compose 配置变化时使用 `docker-compose-operations` Skill 的 recreate 流程。

## 禁区

不要建议或尝试 `docker rm`、`docker rmi`、`docker system prune`、任意 `docker exec`、挂载宿主目录、连接 Docker socket或绕过 AIOMS 固定网关。工具返回失败时报告实际错误，不用不同命令绕过策略。
