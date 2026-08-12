---
name: docker-compose-operations
description: 调查 Docker Compose 的项目归属、变量插值、端口映射、依赖和容器重建问题。
---

# Docker Compose 排障

## 建立有效配置视图

先确定容器是否真的由 Compose 管理，再从容器标签核对 project、service、working directory 和配置文件。不要只凭目录名或容器名前缀判断项目归属。

理解最终配置时，依次对照：

- Compose YAML 中的原始声明；
- 项目 `.env` 和 shell 环境提供的插值；
- `docker compose config` 展开的有效配置；
- `docker inspect` 中既有容器的真实端口、环境变量和挂载。

这四层不一致时，以运行中容器事实解释当前行为，以展开后的有效配置解释下一次创建会得到什么。

## 重建后的核验

容器创建完成后，按顺序检查目标 service 的容器状态、宿主机端口、容器内部监听、最近日志、健康检查和反向代理上游。依赖容器存在不代表必须一起重建；先判断目标 service 的配置变化是否影响依赖契约。

常见现象：

- `port is already allocated`：先定位哪个进程或容器占用宿主机端口，再确认冲突来自旧容器配置还是新配置。
- `.env` 已修改但映射没变：既有容器仍保留创建时配置，需要比较有效配置与容器 inspect。
- 缺少 Compose 标签：目标可能不是 Compose 创建的，或元数据不完整，应把归属不确定作为结论。
- 单个 service 重建后启动失败：查看该 service 的日志和依赖可达性，不先扩大为整个项目重建。

涉及实际重建时，以固定运维核心与安全边界规定的受控流程为准；本 Skill 不授予执行能力。
