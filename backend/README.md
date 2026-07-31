# AIOMS Backend

AIOMS 的 FastAPI 后端，负责业务 API、LangGraph 多 Agent 工作流、工具调用、告警接入和后台自动诊断。

当前后端已经接入 PostgreSQL、Prometheus、Loki、Alertmanager、Grafana、Redis/arq 和受控 SSH 工具，不再是只有模拟数据的聊天 Demo。

## 当前能力

- 服务器管理与 SSH 连通性测试
- 多轮流式对话和会话历史；Agent 运行与浏览器 SSE 连接解耦，切页或关闭页面后继续生成
- 工具调用旁白、工具状态与最终回答实时展示，刷新后按同一轨迹重建
- Supervisor 调度四个专业 Agent
- Prometheus 指标、Loki 日志、Alertmanager 告警查询；指标工具可按服务器名/IP 自动解析 instance
- 服务发布、回滚、扩缩容和配置变更记录 CRUD，以及 `query_recent_changes` 只读工具
- 服务目录、部署实例与有向依赖拓扑；支持直接依赖、潜在影响范围和根因候选查询
- 指标、日志、告警、变更的同实体时间窗关联
- Alertmanager webhook、告警去重归组和事件生命周期
- Redis/arq 后台自动诊断事件
- 集中式工具目录：纯读诊断与有副作用写操作分开注册
- SSH 只读诊断，以及经过 LangGraph 人工审批和服务白名单双重限制的服务重启

## 运行架构

```text
FastAPI POST /chat → 创建后台 Chat Run
    ↓
LangGraph Supervisor
    ├── metrics_agent         → Prometheus / Grafana
    ├── logs_agent            → Loki / Grafana
    ├── incident_agent        → Alertmanager / 变更记录 / 服务拓扑 / 跨信号关联
    └── infrastructure_agent  → servers 表 / SSH 工具
                                 ├── 只读诊断：直接执行
                                 └── 服务重启：interrupt 人工审批

Alertmanager webhook → PostgreSQL → Redis/arq → Supervisor 自动诊断
                                               ├── incidents.diagnosis
                                               └── change_records 时间窗证据
```

四个专家是共享状态的独立 LangGraph 子图，不是包装成普通工具的函数。Supervisor 可以连续委派多个专家，最后由综合节点基于本轮真实工具证据生成一次用户回答并直接返回。

聊天请求先返回 `run_id`，前端再通过 `GET /chat/runs/{run_id}/events` 订阅 SSE。LangGraph
运行在独立后台任务中，SSE 断开只会移除订阅者，不会取消 Agent；重新打开同一对话时可以查询
活跃运行并重放当前进度，完成后完整消息统一落库。

## 环境要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL 16+
- DeepSeek API Key
- Redis：告警自动诊断队列需要
- Docker Desktop：本地 PLG 数据栈和 SSH 测试沙箱需要

PostgreSQL 当前不在 `docker-compose.yml` 中，项目默认连接宿主机上的 `aioms` 数据库。

## 首次配置

在仓库根目录执行：

```bash
cd backend
cp .env.example .env
uv sync
```

编辑 `backend/.env`，至少填写：

```dotenv
DATABASE_URL=postgresql+psycopg://你的用户名@localhost:5432/aioms
DEEPSEEK_API_KEY=你的密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

不要提交 `.env`、SSH 私钥或其他密钥。

创建本地数据库的示例：

```bash
createdb aioms
psql -d aioms -c "select current_database();"
```

应用启动时会执行 `Base.metadata.create_all()`，自动创建缺失的表，但不会修改已有表结构。开发期修改已有字段时仍需手工执行 `ALTER TABLE` 或删除本地表后重建。

## 配置项

配置由 `app/core/config.py` 中的 Pydantic Settings 加载，环境变量名使用大写形式。

| 变量                      | 默认值                       | 用途                       |
| ------------------------- | ---------------------------- | -------------------------- |
| `DATABASE_URL`          | 本机`aioms` 库             | PostgreSQL 异步连接        |
| `DEEPSEEK_API_KEY`      | 空                           | DeepSeek API 密钥          |
| `DEEPSEEK_BASE_URL`     | `https://api.deepseek.com` | OpenAI 兼容接口地址        |
| `DEEPSEEK_MODEL`        | `deepseek-v4-flash`        | 对话、路由和回答模型       |
| `PROMETHEUS_BASE_URL`   | `http://localhost:9090`    | 指标查询                   |
| `LOKI_BASE_URL`         | `http://localhost:3100`    | 日志查询                   |
| `LOKI_ORG_ID`           | 空                           | 多租户 Loki 的组织 ID      |
| `ALERTMANAGER_BASE_URL` | `http://localhost:9093`    | 当前告警查询               |
| `GRAFANA_BASE_URL`      | `http://localhost:3000`    | 生成 Explore 链接          |
| `REDIS_URL`             | `redis://localhost:6379`   | arq broker                 |
| `OBS_HTTP_TIMEOUT`      | `10`                       | 可观测性 HTTP 请求超时秒数 |
| `SSH_RESTART_SERVICE_ALLOWLIST` | 空                  | 允许重启的 systemd 服务名，逗号分隔；空值表示全部禁止 |

SSH 私钥目录固定为仓库根目录的 `ssh/`，数据库只保存私钥文件名。具体测试方法见 [`docs/local-ssh-sandbox.md`](../docs/local-ssh-sandbox.md)。

## 启动依赖

从仓库根目录启动本地可观测性数据栈和 Redis：

```bash
docker compose up -d
docker compose ps
```

它会启动：

- Prometheus `:9090`
- node_exporter `:9100`
- Loki `:3100`
- Promtail
- fake-app 测试日志源
- Alertmanager `:9093`
- Grafana `:3000`
- Redis `:6379`

这套 Compose 不包含 PostgreSQL、FastAPI 和 Vue 前端。

## 启动后端与 Worker

后端默认使用 8010 端口：

```bash
cd backend
uv run uvicorn app.main:app --reload --port 8010
```

自动诊断需要另开终端启动 arq worker：

```bash
cd backend
uv run arq app.worker.WorkerSettings
```

常用地址：

- 健康检查：[http://127.0.0.1:8010/health](http://127.0.0.1:8010/health)
- Swagger UI：[http://127.0.0.1:8010/docs](http://127.0.0.1:8010/docs)
- OpenAPI JSON：[http://127.0.0.1:8010/openapi.json](http://127.0.0.1:8010/openapi.json)

## 主要 API

| API                                    | 作用                       |
| -------------------------------------- | -------------------------- |
| `POST /chat`                         | 创建后台 Chat Run，返回 `run_id` |
| `GET /chat/runs/active/{conversation_id}` | 查询对话当前活跃运行 |
| `GET /chat/runs/{run_id}/events`     | 订阅或重连运行的 SSE 事件 |
| `POST /chat/resume`                  | 恢复被人工审批门挂起的运行 |
| `GET/POST /servers`                  | 查询、新增服务器           |
| `PATCH/DELETE /servers/{id}`         | 修改、删除服务器           |
| `POST /servers/{id}/test-connection` | 测试 SSH 连接              |
| `GET /conversations`                 | 会话列表                   |
| `GET/DELETE /conversations/{id}`     | 会话详情、删除会话         |
| `POST /alerts/webhook`               | 接收 Alertmanager webhook  |
| `GET /alerts`                        | 查询已落库告警             |
| `GET /incidents`                     | 查询关联后的事件           |
| `GET /incidents/{id}/changes`        | 查询事件前后同服务的变更   |
| `POST /incidents/{id}/ack`           | 认领事件                   |
| `POST /incidents/{id}/resolve`       | 人工解决事件               |
| `GET/POST /changes`                  | 查询、新增变更记录         |
| `GET/PATCH/DELETE /changes/{id}`     | 查看、修改、删除变更记录   |
| `GET/POST /services`                 | 查询、新增逻辑服务         |
| `GET/PATCH/DELETE /services/{id}`    | 查看、修改、删除服务       |
| `GET /services/{id}/topology`        | 查询服务关联拓扑           |
| `GET/POST /service-instances`        | 查询、新增服务部署实例     |
| `PATCH/DELETE /service-instances/{id}` | 修改、删除部署实例       |
| `GET/POST /service-dependencies`     | 查询、新增服务依赖边       |
| `PATCH/DELETE /service-dependencies/{id}` | 修改、删除依赖边     |

前端开发环境请求 `/api/...`，由 Vite 代理去掉 `/api` 前缀后转发到这里。

新接入的实体应在服务目录创建 `service_instances`，分别填写 `metric_instance`、`log_service` 并绑定
`server_id`。事件会优先按 `metric_instance` 精确解析逻辑服务，再用服务名和环境关联变更；旧的
`servers.monitor_labels` 仍作为兼容回退。关联只说明时间与实体重合，Agent 提示词和事件页面都会明确
它不是已确认的根因。

服务依赖边定义为 `upstream_service_id → downstream_service_id`，即“调用方依赖被调用方”。
`query_impacted_services` 反向遍历调用方推导潜在影响范围；`query_upstream_candidates` 沿依赖方向
寻找可能传播到当前服务的根因候选，两者都只提供结构性线索，仍需结合真实观测证据确认。

## 测试与静态检查

当前测试不访问 DeepSeek 或真实可观测性服务，使用内存假模型验证 Supervisor handoff、读写工具边界和审批安全边界；SSH 写工具测试使用假的连接对象核对精确命令：

```bash
cd backend
uv run python -m unittest discover -s tests -v
uv run python -m compileall -q app tests
```

Ruff 没有写入项目运行依赖，可按需通过 `uvx` 执行：

```bash
uvx ruff format --check app tests
uvx ruff check app tests
```

## 目录说明

```text
app/
├── api/           # FastAPI 路由，只做协议转换和依赖注入
├── agents/
│   ├── graphs/    # Supervisor、专业子图、审批与最终综合
│   └── tools/     # 指标、日志、告警、关联、SSH 工具及集中式读写目录
├── core/          # 配置
├── db/            # SQLAlchemy 引擎和 Session
├── models/        # 唯一 Base 下的 ORM 模型
├── repositories/  # 数据访问
├── schemas/       # Pydantic 输入输出模型
├── services/      # 业务逻辑和流式图驱动
├── main.py        # FastAPI 入口
└── worker.py      # arq 后台任务
tests/             # 不依赖外部服务的自动化测试
```

## 当前安全边界

- `catalog.py` 是唯一的工具分类入口；每个工具必须明确注册为读工具或写工具，两类不允许重叠。
- `run_diagnostic_command` 是读工具，开放主机资源、进程、网络、systemd、journal、Docker、crictl 和 Kubernetes 的受控只读命令；固定命令精确匹配，带资源名的命令严格校验参数，并统一限制执行时间和输出长度，不进入人工审批。
- `discover_server_services` 是分层只读发现工具：固定查询 systemd、Docker，并用 `ss -lntup` 的监听端口/PID 关联 `ps` 的运行时长、unit 和启动命令，第三层只保留运行至少 60 秒且不属于 systemd service / Docker scope 的进程。SSH 用户看不到其他用户 PID 时会明确提示结果可能不完整；机器发现的运行对象不等于业务服务。
- Docker 只读命令在 SSH 端会改写为 `sudo -n -- /usr/bin/docker ...`，目标服务器必须为 SSH 用户配置参数完全一致的 `NOPASSWD` sudoers 规则；应用不会退化成任意 `sudo docker`。
- `register_server_services` 是服务目录写工具：审批卡片列出准备登记的服务器、环境和运行时映射；批准后会重新扫描核对对象仍然存在，再幂等写入逻辑服务与部署实例。
- `restart_service` 是高风险写工具，先经过 LangGraph `interrupt` 人工审批，执行时还必须命中 `SSH_RESTART_SERVICE_ALLOWLIST`；默认空白名单会拒绝全部重启。
- 工具执行节点会再次校验审批决议，不能只靠模型是否正确路由；后台 worker 设置 `allow_write_tools=False`，从模型绑定和执行节点两层禁止写工具。
- 本地 SSH 沙箱当前使用 `known_hosts=None`，只适用于一次性本地容器，不能直接用于生产服务器。
- 当前仍缺少 API 鉴权、RBAC、正式密钥管理和不可篡改审计日志；`restart_service` 只用于受控开发环境验证，不应直接暴露到生产网络。
- Chat Run 和事件重放目前保存在单个 FastAPI 进程内：浏览器断开不影响生成，但后端重启会中断运行；多进程或生产部署需要改用 Redis/持久化任务状态。

开发计划和未完成项见 [`docs/dev-plan.md`](../docs/dev-plan.md)。
