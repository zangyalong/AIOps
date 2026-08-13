---
name: docker-operations
description: 按容器、端口、资源、进程和日志的证据顺序调查 Docker 运行故障。
---

# Docker 容器排障

## 证据顺序

1. 用运行中和全部容器列表确认准确容器名、状态、退出码、镜像和端口映射。
2. 用 inspect 核对容器的创建配置、健康状态、重启策略、挂载、环境变量和 Compose 标签。
3. 用一次性资源快照判断 CPU、内存和进程数是否异常，再查看容器内实际进程。
4. 读取最近一段日志，并结合容器启动时间区分当前启动失败和历史噪音。
5. 遇到网络问题时，分别核对宿主机监听、端口映射、容器内部监听和上游访问路径。

常用只读命令包括：

```bash
docker ps
docker ps -a
docker inspect <container>
docker stats --no-stream <container>
docker top <container>
docker logs --tail 100 <container>
```

在 AIOps 对话里，应把这些命令映射为当前可用的只读调查能力，不假定存在任意 Docker Shell。

## 形成结论

- `Exited` 只是状态，必须结合退出码和本次启动日志判断原因。
- `healthy` 只代表镜像定义的健康检查通过，不等价于外部链路可用。
- 映射端口存在不代表容器内进程正在监听；两侧都要验证。
- 高资源占用要结合持续时间和业务负载，单次快照只能作为线索。
- 容器由 Compose 管理时，配置变化是否需要重建应按 Compose 有效配置继续调查。

涉及启停、重启或其他状态变化时，以固定运维核心与安全边界为准；本 Skill 只描述如何调查。
