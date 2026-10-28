<p align="center">
  <img src="frontend/public/favicon.svg" width="76" height="76" alt="AIOps 标志">
</p>

<h1 align="center">AIOps</h1>

<p align="center">
  用 AI Agent 调查真实运维证据，并在人类审批后执行受控变更。<br>
  一个聚焦可审计调查、人工审批与最小权限执行的开源 AIOps 项目。
</p>

## 项目定位

AIOps 不是只会回答运维知识的聊天机器人。它把自然语言请求转成一条可观察、可暂停的执行链：先从 Prometheus、Loki、Alertmanager、服务目录或目标服务器收集证据，再给出诊断；只有用户明确要求变更时，Agent 才能提交固定参数的操作请求，并由确定性策略、人工审批和目标机固定网关共同约束执行。

仓库同时公开核心实现、架构决策、同类项目调研和验收记录，便于贡献者理解系统从基础对话演进到受控运维闭环的设计取舍。

## 当前能力

下表描述的是当前代码能力。开发计划中标记为“待运行时验收”的内容，即使静态检查已经通过，也不代表已在所有真实环境稳定可用。

| 模块          | 当前实现                                                                                   |
| ------------- | ------------------------------------------------------------------------------------------ |
| Ops Agent     | 单一 LangGraph Ops Agent、统一调查协议、工具循环、SSE 进度、持久化会话与 Chat Run          |
| 可观测性      | Prometheus 指标、Loki 日志、Alertmanager 告警、Grafana 链接，以及同实体/时间窗证据关联     |
| 事件闭环      | Alertmanager webhook、fingerprint 去重、CEL 规则归组、事件生命周期、Redis/arq 只读自动诊断 |
| 基础设施目录  | 服务器与服务实例管理、SSH 连通性测试、systemd/Docker/Socket 资产发现、目标机部署包         |
| 记忆与 Skills | PostgreSQL 长期记忆、全局/服务器作用域、长会话滚动摘要、按需加载运维 Skill                 |
| 受控变更      | 服务启停/重启、Compose 单服务重建、预警规则、日志配置、精确文件授权与结构化配置补丁        |
| Web 控制台    | 对话、事件、预警规则、服务、服务器、Memory、Skills，以及审批卡片和差异预览                 |

当前写能力不接受任意宿主机 root Shell。服务控制、目标部署、日志配置和文件修改都使用已登记的结构化参数；无人值守 worker 从模型绑定和执行节点两层禁用写工具。

## 界面预览

### 运维助手

![AIOps 运维助手首页，提供 Agent 调查入口、历史对话和服务器上下文选择](docs/images/aiops-chat.png)

### 预警规则管理

![AIOps 预警规则页面，展示规则状态、严重级别和 PromQL](docs/images/aiops-alert-rules.png)

### 受控变更审批

![AIOps 运维助手中的调查结果与高风险变更审批卡片](docs/images/aiops-operation-approval.png)

## Quick Start

### 1. 准备环境

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Node.js `^20.19.0` 或 `>=22.12.0`，以及 npm
- PostgreSQL 16+（默认由下面的 Docker Compose 提供，无需另行安装）
- Docker Engine + Compose plugin，或 Docker Desktop
- DeepSeek API Key

Compose 和后端统一使用 `backend/.env`，按下一步配置即可。首次启动 PostgreSQL 容器会自动创建 `aiops` 用户和数据库，无需手工执行 `createdb`。宿主机已有 PostgreSQL 占用 `5432` 时，可将 `backend/.env` 中的 `POSTGRES_PORT` 改为 `5433`，`DATABASE_URL` 的端口也要一起修改。

已有本机数据库也可继续使用：后端保留原有连接配置，启动依赖时显式指定 `docker compose --env-file backend/.env up -d prometheus loki alertmanager grafana redis`，跳过 Compose 中的 PostgreSQL。`backend/.env` 仍需填写 `POSTGRES_PASSWORD`，以便 Compose 解析配置。

### 2. 配置后端

```bash
cd backend
cp .env.example .env
uv sync
```

编辑 `backend/.env`，至少填写 `DEEPSEEK_API_KEY`、`POSTGRES_PASSWORD`，并将 `DATABASE_URL` 中的占位密码替换成相同的数据库密码，端口与 `POSTGRES_PORT` 一致。示例：`postgresql+psycopg://aiops:replace_with_your_password@127.0.0.1:5432/aiops`。若密码包含 URL 特殊字符，写入 `DATABASE_URL` 前需做百分号编码。模型地址与名称以当前 DeepSeek 官方文档为准；全新数据库会在后端启动时创建缺失的表，已有开发库的字段变更见 [后端迁移说明](backend/README.md#首次配置)。

### 3. 启动本地依赖

回到仓库根目录：

```bash
docker compose --env-file backend/.env up -d
docker compose --env-file backend/.env ps
```

这会启动 PostgreSQL、Prometheus、Loki、Alertmanager、Grafana 和 Redis，不包含 FastAPI 或 Vue。等 `docker compose --env-file backend/.env ps` 中的 PostgreSQL 显示 `healthy` 后再启动后端。新 clone 默认只有 Prometheus 自身指标；如需真实主机指标，按 [`docker/prometheus-targets/README.md`](docker/prometheus-targets/README.md) 复制示例并填写本机忽略的 targets 文件。Loki 日志由纳管服务器 Alloy 或其他外部采集端写入。

PostgreSQL 数据保存在 `postgres_data` 命名卷中，`docker compose --env-file backend/.env down` 会保留数据；`docker compose --env-file backend/.env down -v` 会删除卷和数据库数据。初始化变量只对空数据目录生效，已有数据卷的密码不会随 `.env` 修改而更新。现有本机数据库的数据也不会自动迁入容器。

> [!CAUTION]
> 这套 Compose 使用无认证的本地服务和匿名 Grafana Editor，只适合开发/受控测试。端口默认只绑定 `127.0.0.1`，不要改成 `0.0.0.0` 后直接暴露。

### 4. 启动后端与 Worker

```bash
cd backend
uv run uvicorn app.main:app --reload --port 8010
```

如需事件后台自动诊断，再开一个终端：

```bash
cd backend
uv run arq app.worker.WorkerSettings
```

验证入口：

- 健康检查：[http://127.0.0.1:8010/health](http://127.0.0.1:8010/health)
- Swagger UI：[http://127.0.0.1:8010/docs](http://127.0.0.1:8010/docs)

### 5. 启动前端

```bash
cd frontend
npm ci
npm run dev
```

打开 [http://127.0.0.1:5174](http://127.0.0.1:5174)。目标服务器接入、受控权限安装和首次测试请继续阅读 [目标服务器说明](scripts/target-server/README.md) 与 [本地 SSH 沙箱](docs/local-ssh-sandbox.md)。

## 一次典型的调查与审批

以“调查 `example-01` 上的服务异常，确认后重启”为例：

1. API 创建持久化 Chat Run，前端通过 SSE 订阅真实进度。
2. Ops Agent 调用统一 `investigate`，后端按证据领域路由到可观测性数据源、运行时探针或宿主机只读沙箱。
3. Agent 基于工具结果补充调查并给出结论；内部执行器不能被模型随意选择。
4. 用户明确要求重启后，Agent 只能提交结构化 `restart_service`，不能拼接 Shell。
5. Operation Broker 解析服务目录中的真实运行时身份，完成参数预检、风险判断并冻结请求。
6. LangGraph 在审批节点暂停；高风险操作还需要详情二次确认。
7. 批准后再次核对目标与授权，再调用目标机 root 持有的固定网关；拒绝、过期或状态漂移都失败关闭。
8. 执行结果进入 `operation_records`，Agent 随后用只读调查核验实际状态。

## 架构

```mermaid
flowchart TB
    USER["用户"] --> UI["Vue 3 控制台"]
    UI --> API["FastAPI API"]
    API --> PG["PostgreSQL<br/>业务数据 / Chat Run / Checkpoint / 审计"]
    API --> GRAPH["LangGraph 统一 Ops Agent"]

    GRAPH --> INVESTIGATE["investigate<br/>统一调查协议"]
    INVESTIGATE --> ROUTER["确定性证据路由"]
    ROUTER --> OBS["Prometheus / Loki / Alertmanager"]
    ROUTER --> RUNTIME["systemd / Docker / Socket 探针"]
    ROUTER --> ROOTREAD["宿主机 root 只读沙箱"]

    GRAPH --> CHANGE["结构化变更工具"]
    CHANGE --> BROKER["Operation Broker<br/>预检 / 策略 / 冻结"]
    BROKER -->|"需要审批"| APPROVAL["LangGraph interrupt<br/>人工审批"]
    BROKER -->|"拒绝"| DENY["失败关闭"]
    APPROVAL --> EXECUTOR["固定操作执行器"]
    EXECUTOR --> GATEWAY["目标机 root 固定网关<br/>精确白名单 / 最小 sudoers"]

    ALERT["Alertmanager webhook"] --> API
    API --> REDIS["Redis / arq"]
    REDIS --> WORKER["无人值守诊断 Worker<br/>只允许调查"]
    WORKER --> GRAPH
```

## 安全模型

- 工具参数、长期记忆候选和结构化写入都要经过确定性校验；AI Markdown 在前端由 DOMPurify 清理。
- 调查默认可以自动执行，但必须写入操作审计；写操作按风险进入人工审批，高风险需要二次确认，critical 始终拒绝执行。
- 通用 Shell 只允许进入无网络、宿主根只读、敏感路径明确遮蔽的 root-read 沙箱。
- 目标机写操作只能通过 root 持有的固定包装器与精确 sudoers；普通 SSH 用户不能修改网关。
- 文件修改不隐式触发校验、reload、重启或部署；每个后续动作必须独立提出并重新审批。
- `.env`、SSH 私钥、真实 Prometheus targets 和目标机部署配置都由 Git 忽略规则隔离。

| 操作类型      | 默认策略                                         |
| ------------- | ------------------------------------------------ |
| 只读调查      | 自动允许，必须审计                               |
| 中风险变更    | 人工审批；只有显式配置时才可对非生产环境自动放行 |
| 高风险变更    | 人工审批 + 二次确认                              |
| Critical 变更 | 只给建议，禁止执行                               |
| 无人值守任务  | 只读，禁止写操作                                 |

完整边界与漏洞报告方式见 [`SECURITY.md`](SECURITY.md) 和 [Agent 操作执行架构](docs/operation-execution-architecture.md)。

## 当前限制

- 本地单用户模式，没有正式登录鉴权、RBAC、多租户隔离；审批身份来自服务端配置。
- 当前所有 SSH 连接路径都使用 `known_hosts=None`，尚未校验目标主机指纹；私钥仍由本地文件管理。
- 操作审计可查询，但不是不可篡改存储。
- 浏览器断开不会取消任务，但正在执行工具或生成回答的 Run 不能跨后端重启恢复；只有尚未执行且 checkpoint/审计完整匹配的等待审批断点可以恢复。
- 长期记忆使用关键词评分，不是向量检索、知识图谱或完整 RAG。
- 告警关联是确定性 CEL 规则雏形，不是 ML 关联或跨服务因果分析；当前也没有 traces 数据源。
- 数据库使用 `create_all()` 和手写迁移脚本，尚未引入正式迁移工具。
- 本地 Compose 仍有多个 `latest` 镜像，尚未达到可复现的生产部署标准。
- 尚无正式通知、排班、升级策略、状态页、复盘流程或 Agent eval / 端到端测试。
- 目标服务器脚本涉及特权安装，必须先在一次性沙箱或可重建测试机验收。

## 技术栈

| 层         | 技术                                               |
| ---------- | -------------------------------------------------- |
| Agent      | LangChain、LangGraph、DeepSeek OpenAI 兼容 API     |
| 后端       | Python 3.12+、FastAPI、SQLAlchemy、Pydantic v2、uv |
| 前端       | Vue 3、Vue Router、Vite、TypeScript                |
| 数据与任务 | PostgreSQL、Redis、arq                             |
| 可观测性   | Prometheus、Loki、Alertmanager、Grafana            |
| 目标机接入 | SSH、root 只读 Docker 沙箱、固定参数写网关         |

## 项目结构

```text
AIOps/
├── backend/                # FastAPI、LangGraph、业务服务、迁移脚本和测试
├── frontend/               # Vue 3 控制台
├── docker/                 # 本地可观测性配置与 Git 忽略的真实 targets
├── scripts/target-server/  # 目标机只读沙箱、资产发现、exporter 与固定写网关
├── ssh/                    # 只保留说明；真实私钥不进入 Git
├── docs/                   # 架构、调研、开发记录与 QA
├── CONTRIBUTING.md         # 贡献流程与验证要求
├── SECURITY.md             # 安全状态与漏洞报告方式
├── LICENSE                 # MIT License
└── docker-compose.yml      # 本地可观测性栈 + Redis，不含应用与 PostgreSQL
```

## 文档

- [文档总索引](docs/README.md)
- [后端运行、配置、API 与完整安全边界](backend/README.md)
- [前端页面、设计约定与运行时验收](frontend/README.md)
- [目标服务器部署与权限边界](scripts/target-server/README.md)
- [Agent 操作执行架构](docs/operation-execution-architecture.md)
- [Agent Memory 设计](docs/agent-memory-design.md)
- [Agent 工具 Schema 设计](docs/agent-tool-schema.md)
- [功能调研与竞品对比](docs/aiops-feature-survey.md)
- [开发计划与人工验收状态](docs/dev-plan.md)

## 开发检查

后端：

```bash
cd backend
uv run python -m unittest discover -s tests -v
uv run python -m compileall -q app tests
uv run ruff format --check app tests
uv run ruff check app tests
```

前端：

```bash
cd frontend
npm run build
```

自动化检查覆盖核心服务、Agent 工具边界与前端类型/构建；真实 PLG、SSH、审批链和浏览器交互仍需要对应环境的人工验收。

## 贡献

提交改动前请阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md)，安全问题请按 [`SECURITY.md`](SECURITY.md) 私密报告。

## 许可证

本项目基于 [MIT License](LICENSE) 开源。
