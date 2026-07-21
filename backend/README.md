# AIOMS Backend

AIOMS 的 FastAPI 后端，负责业务 API、LangGraph 多 Agent 工作流、工具调用、告警接入和后台自动诊断。

当前后端已经接入 PostgreSQL、Prometheus、Loki、Alertmanager、Grafana、Redis/arq 和 SSH 白名单命令，不再是只有模拟数据的聊天 Demo。

## 当前能力

- 服务器管理与 SSH 连通性测试
- 多轮流式对话和会话历史
- 工具调用旁白、工具状态与最终回答实时展示，刷新后按同一轨迹重建
- Supervisor 调度四个专业 Agent
- Prometheus 指标、Loki 日志、Alertmanager 告警查询；指标工具可按服务器名/IP 自动解析 instance
- 指标、日志、告警的同实体时间窗关联
- Alertmanager webhook、告警去重归组和事件生命周期
- Redis/arq 后台自动诊断事件
- SSH 白名单命令与 LangGraph 人工审批门

## 运行架构

```text
FastAPI /chat
    ↓
LangGraph Supervisor
    ├── metrics_agent         → Prometheus / Grafana
    ├── logs_agent            → Loki / Grafana
    ├── incident_agent        → Alertmanager / 三支柱关联
    └── infrastructure_agent  → servers 表 / SSH 白名单命令
                                      ↓
                               interrupt 人工审批

Alertmanager webhook → PostgreSQL → Redis/arq → Supervisor 自动诊断 → incidents.diagnosis
```

四个专家是共享状态的独立 LangGraph 子图，不是包装成普通工具的函数。Supervisor 可以连续委派多个专家，最后由综合节点基于本轮真实工具证据生成一次用户回答并直接返回。

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
| `POST /chat`                         | 发起一轮 SSE 流式对话      |
| `POST /chat/resume`                  | 恢复被人工审批门挂起的对话 |
| `GET/POST /servers`                  | 查询、新增服务器           |
| `PATCH/DELETE /servers/{id}`         | 修改、删除服务器           |
| `POST /servers/{id}/test-connection` | 测试 SSH 连接              |
| `GET /conversations`                 | 会话列表                   |
| `GET/DELETE /conversations/{id}`     | 会话详情、删除会话         |
| `POST /alerts/webhook`               | 接收 Alertmanager webhook  |
| `GET /alerts`                        | 查询已落库告警             |
| `GET /incidents`                     | 查询关联后的事件           |
| `POST /incidents/{id}/ack`           | 认领事件                   |
| `POST /incidents/{id}/resolve`       | 人工解决事件               |

前端开发环境请求 `/api/...`，由 Vite 代理去掉 `/api` 前缀后转发到这里。

## 测试与静态检查

当前测试不访问 DeepSeek、SSH 或真实可观测性服务，使用内存假模型验证 Supervisor handoff 和审批安全边界：

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
│   └── tools/     # 指标、日志、告警、关联、SSH 等工具
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

- SSH 只允许 `uptime`、`df -h`、`free -m` 三条精确白名单命令。
- 聊天中的 SSH 调用必须经过 LangGraph `interrupt` 人工审批。
- 后台 worker 设置 `allow_write_tools=False`，即使模型越权提出 SSH 也会被工具层拒绝。
- 本地 SSH 沙箱当前使用 `known_hosts=None`，只适用于一次性本地容器，不能直接用于生产服务器。
- 当前仍缺少 API 鉴权、RBAC、正式密钥管理和不可篡改审计日志，不应直接暴露到生产网络。

开发计划和未完成项见 [`docs/dev-plan.md`](../docs/dev-plan.md)。
