# AIOps Backend

AIOps 的 FastAPI 后端，负责业务 API、LangGraph Ops Agent 工作流、工具调用、告警接入和后台自动诊断。

当前后端已经接入 PostgreSQL、Prometheus、Loki、Alertmanager、Grafana、Redis/arq 和受控 SSH 工具，覆盖持久化对话、证据调查、事件诊断与审批受控变更。

## 当前能力

- 服务器管理与 SSH 连通性测试
- 多轮流式对话和会话历史；`chat_runs` 持久化每轮状态，消息正文只存在 `messages`
- PostgreSQL LangGraph checkpoint：等待人工审批的图状态可跨后端重启恢复
- 跨会话长期记忆：从成功对话提炼稳定偏好、环境约定、决策和经验，按当前问题检索注入
- 工具调用旁白、工具状态与最终回答实时展示，刷新后按同一轨迹重建
- 单一 Ops Agent 通过统一调查入口获取证据，通过结构化变更工具进入人工审批
- Prometheus 指标、Loki 日志、Alertmanager 告警查询；指标工具可按服务器名/IP 自动解析 instance
- 预警规则通过前端维护并存入数据库，后端自动生成 Prometheus 运行时规则并热重载
- 服务目录与部署实例；支持服务运行状态、指标和日志查询
- 指标、日志、告警的同实体时间窗关联
- Alertmanager webhook、告警去重归组和事件生命周期
- Redis/arq 后台自动诊断事件
- 集中式工具目录：纯读诊断与有副作用写操作分开注册
- SSH 只读诊断，以及经过 LangGraph 人工审批和双端白名单限制的 systemd 服务启停/重启

## 运行架构

```text
FastAPI POST /chat → 创建后台 Chat Run
    ↓
LangGraph Ops Agent
    ├── investigate → 调查路由
    │                   ├── Prometheus / Loki / Alertmanager
    │                   ├── 服务目录 / 运行时状态探针
    │                   └── 宿主机 root 只读沙箱
    └── 结构化变更 → Operation Broker → interrupt 人工审批 → 固定写网关

Alertmanager webhook → PostgreSQL → Redis/arq → 同一 Ops Agent（禁用写工具）
                                               └── incidents.diagnosis
```

Agent 对外只认识“调查”和“变更”。`investigate` 是统一运维调查入口，后端根据证据领域选择可观测性查询、运行时探针或宿主机文件沙箱；`read_skill` 只按已安装名称读取教程型 Skill。所有写能力仍是固定参数工具，不能退化成任意 root Shell。

聊天请求先创建数据库 `chat_runs` 记录并写入带 `run_id` 的用户消息，再返回 `run_id`；前端通过
`GET /chat/runs/{run_id}/events` 订阅 SSE。LangGraph
运行在独立后台任务中，SSE 断开只会移除订阅者，不会取消 Agent；重新打开同一对话时可以查询
活跃运行并重放当前进度。Tool 调用、Tool 结果和最终回答按 Run 逐步落库；失败 Run 保留现场，
但再次喂给模型的历史只包含完整配对的 tool calls / tool results。

## 环境要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL 16+
- DeepSeek API Key
- Redis：告警自动诊断队列需要
- Docker Engine + Compose plugin，或 Docker Desktop：本地 PLG 数据栈和 SSH 测试沙箱需要

PostgreSQL 当前不在 `docker-compose.yml` 中，项目默认连接宿主机上的 `aiops` 数据库。

## 首次配置

在仓库根目录执行：

```bash
cd backend
cp .env.example .env
uv sync
```

编辑 `backend/.env`，至少填写：

```dotenv
DATABASE_URL=postgresql+psycopg://你的用户名@localhost:5432/aiops
DEEPSEEK_API_KEY=你的密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

不要提交 `.env`、SSH 私钥或其他密钥。

创建本地数据库的示例：

```bash
createdb aiops
psql -d aiops -c "select current_database();"
```

应用启动时会执行 `Base.metadata.create_all()`，自动创建缺失的表，但不会修改已有表结构。开发期修改已有字段时仍需手工执行 `ALTER TABLE` 或删除本地表后重建。

本次从旧版本升级需要给已有 `messages` 表增加 `run_id`：

```bash
cd backend
uv run python scripts/migrate_chat_runs.py
```

更早版本的 `servers` 表如果还没有 `monitor_labels` 兼容字段，运行：

```bash
cd backend
uv run python scripts/migrate_server_monitor_labels.py
```

已经运行过基础 Memory 版本的开发库，还需要增加全局/服务器作用域、Chat Run 服务器绑定和
会话滚动摘要字段：

```bash
cd backend
uv run python scripts/migrate_memory_scopes.py
```

这些脚本都使用幂等 DDL，可以重复执行。全新数据库不需要手动迁移，启动时会直接创建完整表结构。

LangGraph checkpoint 使用同一个 PostgreSQL 数据库。后端重启后会恢复仍在等待人工确认、且
checkpoint 与 `operation_records` 完整匹配的审批断点；执行中的 Run 和操作结果未知，仍失败关闭并
要求人工核验，不自动重放写操作。终态 Run 的 checkpoint 会删除，完整聊天记录继续保留在业务表中。

## 配置项

配置由 `app/core/config.py` 中的 Pydantic Settings 加载，环境变量名使用大写形式。

| 变量                              | 默认值                       | 用途                                                  |
| --------------------------------- | ---------------------------- | ----------------------------------------------------- |
| `DATABASE_URL`                  | 本机`aiops` 库             | PostgreSQL 异步连接                                   |
| `DEEPSEEK_API_KEY`              | 空                           | DeepSeek API 密钥                                     |
| `DEEPSEEK_BASE_URL`             | `https://api.deepseek.com` | OpenAI 兼容接口地址                                   |
| `DEEPSEEK_MODEL`                | `deepseek-v4-flash`        | 对话、路由和回答模型                                  |
| `MEMORY_ENABLED`               | `true`                     | 是否提炼并检索跨会话长期记忆                          |
| `MEMORY_RETRIEVAL_LIMIT`       | `6`                        | 每轮最多注入的长期记忆条数                            |
| `MEMORY_CANDIDATE_LIMIT`       | `200`                      | 关键词检索前最多读取的活跃候选数                      |
| `MEMORY_SHORT_TERM_MAX_CHARS`  | `120000`                   | 每轮送给模型的近期完整历史字符预算                    |
| `MEMORY_CONVERSATION_SUMMARY_ENABLED` | `true` | 是否在完成 Chat Run 后滚动压缩过长会话 |
| `MEMORY_CONVERSATION_SUMMARY_TRIGGER_CHARS` | `80000` | 未摘要历史触发压缩的字符数 |
| `MEMORY_CONVERSATION_RECENT_CHARS` | `40000` | 压缩后希望保留的近期原文字符预算 |
| `MEMORY_CONVERSATION_SUMMARY_INPUT_CHARS` | `60000` | 单次送给摘要器的旧对话字符上限 |
| `MEMORY_CONVERSATION_SUMMARY_MAX_CHARS` | `8000` | 滚动摘要最大字符数 |
| `PROMETHEUS_BASE_URL`           | `http://localhost:9090`    | 指标查询                                              |
| `LOKI_BASE_URL`                 | `http://localhost:3100`    | 日志查询                                              |
| `LOKI_ORG_ID`                   | 空                           | 多租户 Loki 的组织 ID                                 |
| `ALERTMANAGER_BASE_URL`         | `http://localhost:9093`    | 当前告警查询                                          |
| `GRAFANA_BASE_URL`              | `http://localhost:3000`    | 生成 Explore 链接                                     |
| `REDIS_URL`                     | `redis://localhost:6379`   | arq broker                                            |
| `OBS_HTTP_TIMEOUT`              | `10`                       | 可观测性 HTTP 请求超时秒数                            |
| `SSH_SERVICE_CONTROL_ALLOWLIST` | 空                           | 生成目标部署包时写入的初始 root 控制白名单；使用 `systemd:unit.service` / `docker:container`，逗号分隔 |
| `SSH_KEY_DIR`                   | 仓库根目录`ssh/`           | 生成和读取目标服务器 SSH 私钥                         |
| `TARGET_SERVER_SCRIPTS_DIR`     | `scripts/target-server/`   | 生成目标服务器一键安装包的固定脚本目录                |

SSH 私钥默认放在仓库根目录的 `ssh/`，数据库只保存私钥文件名。具体测试方法见 [`docs/local-ssh-sandbox.md`](../docs/local-ssh-sandbox.md)。
只读查询和审批后的固定网关写操作统一使用 `ssh_user` / `ssh_key_filename` 对应的非 root
AIOps 身份；目标服务器 sudoers 只授权 root 持有的白名单包装器，不开放任意 `systemctl`、
Shell、Docker 或 root 登录。服务器编辑页还会把
非敏感部署参数保存在 `servers.deployment_config`；数据库 exporter 密码只用于当次下载或部署
请求，不会落库。

`POST /servers/{server_id}/deployment-bundle` 会先删除当前服务器固定命名的旧公私钥并重新生成密钥对，
再在后端临时目录生成不含私钥的安装包并直接下载；若打包失败会恢复原密钥对。响应完成后清理
临时配置和数据库凭据。后端运行机器需要有 Bash、`ssh-keygen`、`tar` 和
`sha256sum`（macOS 可使用 `shasum`），并且 `SSH_KEY_DIR` 对后端进程可写。

首次安装包会把 root 所有的固定部署程序安装到 `/opt/aiops-target-server`。此后页面按钮或高风险
写 Tool 可调用 `POST /servers/{server_id}/deploy`：后端只上传经过 Pydantic 校验的配置与本次
请求凭据，再通过 `aiops-target-deploy apply-config` 固定网关应用。Tool 不接收数据库密码，
只复用目标服务器已有凭据；首次启用数据库密码认证需从服务器页面提交。

真实目标服务器需要安装只读 Docker / 业务资产发现网关和 sudoers，文件、安装位置、参数及
验证方法见 [`scripts/target-server/README.md`](../scripts/target-server/README.md)。

## 启动依赖

从仓库根目录启动本地可观测性数据栈和 Redis：

```bash
docker compose up -d
docker compose ps
```

它会启动：

- Prometheus `:9090`
- Loki `:3100`
- Alertmanager `:9093`
- Grafana `:3000`
- Redis `:6379`

这套 Compose 不包含 PostgreSQL、FastAPI 和 Vue 前端。新 clone 默认只有 Prometheus 自身指标；
按 [`docker/prometheus-targets/README.md`](../docker/prometheus-targets/README.md) 复制示例后，真实指标目标才会从本机 Git 忽略的
`managed-targets.yml` 读取。日志由纳管服务器 Alloy 或其他外部采集端写入 Loki。

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

| API                                         | 作用                              |
| ------------------------------------------- | --------------------------------- |
| `POST /chat`                              | 创建后台 Chat Run，返回`run_id` |
| `GET /chat/runs/active/{conversation_id}` | 查询对话当前活跃运行              |
| `GET /chat/runs/{run_id}/events`          | 订阅或重连运行的 SSE 事件         |
| `POST /chat/resume`                       | 恢复被人工审批门挂起的运行        |
| `GET /memories`                           | 按 `scope_type/server_id` 查看全局或服务器长期记忆 |
| `POST /memories`                          | 手动新增经安全校验的长期记忆       |
| `DELETE /memories/{id}`                   | 遗忘一条长期记忆                  |
| `DELETE /memories`                        | 按作用域清空当前用户活跃长期记忆   |
| `GET/POST /servers`                       | 查询、新增服务器                  |
| `PATCH/DELETE /servers/{id}`              | 修改、删除服务器                  |
| `POST /servers/{id}/test-connection`      | 测试 SSH 连接                     |
| `POST /servers/{id}/deployment-bundle`    | 下载首次目标服务器安装包          |
| `POST /servers/{id}/deploy`               | 自动应用已保存的目标服务器配置    |
| `GET /conversations`                      | 会话列表                          |
| `GET/DELETE /conversations/{id}`          | 会话详情、删除会话                |
| `POST /alerts/webhook`                    | 接收 Alertmanager webhook         |
| `GET /alerts`                             | 查询已落库告警                    |
| `GET/POST /alert-rules`                   | 查询、新增预警规则                |
| `PATCH/DELETE /alert-rules/{id}`          | 修改、删除预警规则                |
| `POST /alert-rules/apply`                 | 从数据库重新生成并应用规则        |
| `GET /incidents`                          | 按状态、页码查询事件中心记录       |
| `DELETE /incidents/{id}`                  | 删除一条已解决的历史事件           |
| `POST /incidents/{id}/ack`                | 认领事件                          |
| `POST /incidents/{id}/resolve`            | 人工解决事件                      |
| `GET/POST /services`                      | 查询、新增逻辑服务                |
| `GET/PATCH/DELETE /services/{id}`         | 查看、修改、删除服务              |
| `GET/POST /service-instances`             | 查询、新增服务部署实例            |
| `PATCH/DELETE /service-instances/{id}`    | 修改、删除部署实例                |

前端开发环境请求 `/api/...`，由 Vite 代理去掉 `/api` 前缀后转发到这里。

长期记忆的类别、安全边界、checkpoint 恢复语义和选型依据见
[`docs/agent-memory-design.md`](../docs/agent-memory-design.md)。

新接入的实体应在服务目录创建 `service_instances`，分别填写 `metric_instance`、`log_service` 并绑定
`server_id`。关联查询会优先使用规范化服务实例，旧的 `servers.monitor_labels` 仍作为兼容回退。

## 测试与静态检查

当前测试不访问 DeepSeek 或真实可观测性服务，使用内存假模型验证统一调查循环、读写工具边界和审批安全边界；SSH 写工具测试使用假的连接对象核对精确命令：

```bash
cd backend
uv run python -m unittest discover -s tests -v
uv run python -m compileall -q app tests
```

Ruff 已作为项目开发依赖写入锁文件，以下命令始终使用项目锁定的版本：

```bash
uv run ruff format --check app tests
uv run ruff check app tests
```

已有开发数据库升级到统一调查审计结构：

```bash
uv run python scripts/migrate_operation_records.py
```

## 目录说明

```text
app/
├── api/           # FastAPI 路由，只做协议转换和依赖注入
├── agents/
│   ├── graphs/    # 统一 Ops Agent、工具循环与审批恢复
│   └── tools/     # 统一调查门面、内部读执行器及结构化写工具目录
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

- `catalog.py` 是唯一的模型工具分类入口；模型可见的读能力是统一调查 `investigate` 与教程读取 `read_skill`，结构化写工具逐项登记风险和审批展示，两类不允许重叠。
- Skill 使用 Pi 风格的渐进加载：system prompt 只拼入名称、描述和路径索引，模型判断匹配时调用 `read_skill(name)` 获取正文，用户也可用 `/skill:名称` 强制展开。Skill 不包含确定性触发词或关联工具，也不能承载安全边界和强制业务流程。
- 每次图执行都有独立 `execution_id`：交互调查同时关联 `chat_run_id`，worker 调查不需要伪造聊天记录也会写入同一张操作审计表；写操作缺少真实 Chat Run 时继续 fail-closed。
- `investigate` 按 `servers/services/runtime/host/metrics/logs/alerts/incident/visualization/change` 证据领域路由。`run_diagnostic_command` 等旧读工具仍作为后端内部执行器存在，不再由模型直接选择或向用户展示。
- `runtime` 调查使用后端定义的结构化选择器查询主机资源、systemd、journal、Docker、socket 等实时状态；参数和输出继续受超时、长度及目标格式校验，但这些内部命令白名单不进入 Prompt。
- `host` 调查进入 root-read 的宿主机语境：`/`、PATH 和程序都来自目标服务器，因而 `python --version` 等结果是宿主机事实；宿主根仍为只读，网络、控制 socket、敏感路径和运行时数据路径继续隔离。
- `discover_server_services` 是业务资产盘点工具：固定查询 systemd、Docker、TCP/UDP 和 Unix Socket，只对监听结果中的纯数字 PID 补充运行时长、unit 和启动命令，不再读取整机进程表；第三层只保留运行至少 60 秒且不属于 systemd service / Docker scope 的进程。机器发现的运行对象不等于业务服务。
- Docker、监听 socket 和按 PID 只读查询在 SSH 端会改写为 `sudo -n -- /usr/local/sbin/aiops-*-read ...`；目标服务器的 root 所有包装脚本会再次校验子命令、容器名和 PID，sudoers 只授权这两个网关，不开放任意 `sudo docker/ss/ps`。
- `register_server_services` 是服务目录写工具：审批卡片列出准备登记的服务器、环境和运行时映射；批准后会重新扫描核对对象仍然存在，再幂等写入逻辑服务与部署实例。
- `preview_server_log_config` 是只读 Tool，只从服务目录生成 systemd/Docker 日志映射、摘要和警告，不连接目标服务器；`apply_server_log_config` 是高风险写 Tool，两步确认后重新计算并核对摘要，使用同一非 root AIOps 身份上传到固定暂存路径，再调用固定 `apply-alloy` 网关校验、安装并重载 Alloy。
- `start_service` 是中风险写工具，审批卡片确认一次；`stop_service`、`restart_service` 是高风险写工具，审批卡片后还必须在详情弹窗二次确认，后端也会校验二次确认标记。三者根据服务目录中的运行时身份统一控制 systemd 服务或 Docker 容器，要求对应实例已开启“控制授权”，并通过同一非 root AIOps 身份调用固定 `aiops-service-control` 网关。
- `recreate_compose_service` 是独立高风险工具，用于应用 `.env` 插值和端口映射等必须重建容器才生效的配置。它只接受已登记且已授权的容器名；目标机 `aiops-compose-recreate` 从容器标签反查 Compose 项目，限定目标 service 为单副本并执行 `up -d --no-deps --force-recreate --no-build --pull never`，不接受路径或额外 flags。同一项目可以包含其他 service，它们不会随目标重建。
- `find_server_files` / `read_server_file` 只在服务实例已登记的文件范围内查找和读取不超过 64 KiB 的文本配置；目标机固定网关做真实路径校验，敏感字段按整行脱敏，私钥、shadow、sudoers 等路径始终禁止。
- `preview_server_file_update` 先重新读取当前文件并生成统一差异；`update_server_file` 是高风险写工具，要求精确文件授权、审批卡片与详情弹窗两步确认，并重新核对旧内容 SHA-256、预览摘要和完整 diff。目标机写网关保存 root 备份、保留原所有权/权限并原子替换；含敏感字段的文件不能用通用工具整文件覆盖。
- `preview_server_config_patch` / `patch_server_config` 补充字段级配置修改：dotenv 使用变量名、JSON 使用 JSON Pointer，只允许更新已存在的非敏感标量。预览和补丁都在目标机固定网关中完成，其他真实字段不进入模型；执行仍核对旧文件 SHA-256、审批摘要并备份原子替换。YAML 在固定的保格式解析器落地前保持拒绝。
- 文件工具只负责编辑文件，不执行配置校验、reload、服务重启或部署。若用户要求让配置生效，Agent 必须在文件修改成功后通过独立调查和服务变更工具继续，每个后续写操作使用自己的审批目标。
- 配置写入与运行时生效是两个证据阶段：若 Docker Compose 仅在创建容器时使用 `.env` 插值，普通容器 start/restart 不能替代 recreate。Agent 不应先尝试用旧容器配置启动，而应在文件补丁成功后直接提交 `recreate_compose_service` 审批，再核验新容器、端口和健康状态。
- 服务目录开关通过固定 `aiops-service-policy add/remove` 网关同步目标服务器 root 白名单；远端成功后才保存数据库状态，数据库保存失败时会尽力回滚远端。策略网关不执行启停、不接受文件路径或 Shell，服务发现和目录注册也不会自动调用它。
- 已授权实例不能直接修改服务器/运行时身份或删除，逻辑服务也不能级联删除仍授权的实例；必须先关闭授权，让目标服务器移除白名单后再修改目录。

- `create_alert_rule` 是中风险写工具：人工审批后复用预警规则页面相同的字段校验、PromQL 校验、数据库写入和 Prometheus 热重载流程。
- 工具执行节点会再次校验审批决议和高风险二次确认，不能只靠模型是否正确路由；后台 worker 设置 `allow_write_tools=False`，从模型绑定和执行节点两层禁止写工具。
- 当前所有 SSH 连接都使用 `known_hosts=None`，尚未校验目标主机指纹；这只适用于本地或受控测试环境，不能直接用于生产服务器。
- 当前仍缺少 API 鉴权、RBAC、正式密钥管理和不可篡改审计日志；服务控制工具只用于受控开发环境验证，不应直接暴露到生产网络。
- Chat Run 和事件重放目前保存在单个 FastAPI 进程内：浏览器断开不影响生成，但后端重启会中断运行；多进程或生产部署需要改用 Redis/持久化任务状态。

开发计划和未完成项见 [`docs/dev-plan.md`](../docs/dev-plan.md)。
