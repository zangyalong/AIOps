# 开发计划表

跟踪多步骤任务的进度。这不是项目路线图，只在用户明确列出一批任务时才新增/更新——规则见 [CLAUDE.md](../CLAUDE.md) 「与 Claude 的协作模式」第 9 条。

## 前端导航与对话可靠性

- [ ] **页面和历史对话 URL 路由化**：对话使用 `/chat/:conversationId`，事件中心和服务器页面使用独立地址；浏览器前进/后退、刷新和复制链接都能回到同一页面
  - Vue Router 和各页面地址已接入；侧边栏会记住最近的历史对话地址。代码与静态检查完成后仍保持未勾选，等用户运行时验收
- [ ] **AI 回复不依赖浏览器 SSE 连接**：Agent 在后端独立任务中继续执行；切页或关闭页面不会取消生成，返回正在生成的对话时可以重新订阅并恢复进度，完成后以数据库历史为准
  - 已新增进程内 Chat Run 管理器，后台任务与 SSE 订阅分离，并支持事件编号、重放和审批续传；仍保持未勾选，等用户实际验证切页/关闭页面场景
- [ ] **AI 消息 Markdown 渲染与横向溢出治理**：使用成熟解析器渲染标题、列表、链接、表格和代码；长 URL、代码块、表格不能撑破消息白框或产生页面横向滚动条
  - 已封装 `MarkdownContent.vue`，对话回复和事件中心 AI 诊断复用同一套 markdown-it + DOMPurify 安全渲染；消息项禁止 flex 纵向压缩，外层列表独立纵向滚动；代码与前端构建完成后仍保持未勾选，等用户运行时验收

## 诊断上下文与运维闭环

- [ ] **变更 / 发布关联**：记录服务发布、回滚、扩缩容和配置变更，并按服务与时间窗为事件诊断补充变更证据
  - 变更记录模型与 CRUD API / 管理页面：服务、环境、版本或 commit、操作类型、发生时间和说明
  - 新增只读工具 `query_recent_changes`；`correlate_incident` 自动带上同一服务、同一时间窗的变更
  - 事件详情展示告警发生前后的变更时间线；事实关联只按实体和时间窗完成，不把时间重合直接断言为根因
  - 代码、后端测试和前端构建完成后仍保持未勾选，等用户用真实记录完成运行时验收
- [ ] **服务目录与依赖拓扑**：建立服务、负责人、环境、监控标签、仓库 / 仪表盘链接，以及服务依赖和服务器归属关系
  - 提供服务管理与依赖图页面，以及 `query_service_dependencies`、`query_impacted_services`、`query_upstream_candidates` 只读工具
  - 已实现 `services`、`service_instances`、`service_dependencies` 三张规范化表与完整 CRUD；事件和自动 triage 优先按实例解析服务，旧 `monitor_labels` 兼容回退
  - 已实现可点击 SVG 拓扑和服务详情 URL；代码、后端测试和前端构建完成后仍保持未勾选，等用户运行时验收
- [ ] **值班报告与通知**：基于事件状态机生成确定性日报 / 周报，并为未认领、超时未解决和自动诊断完成事件发送通知
  - 复用 Redis / arq 调度任务；先接一个通知渠道，模型只负责基于统计事实生成摘要和建议

## 运维 Agent 能力升级

背景：现在的 chat 图（`plan → call_model ↔ call_tools → reflect`，见 `backend/app/agents/graphs/chat_graph.py`）只有 `query_servers` 一个工具、工具调用循环没有步数上限、前端看不到工具调用过程、也没有子 agent。这组任务把它往"能调多个运维工具、有专业子 agent"的方向推进。

- [X] **扩充运维工具 + 给工具调用循环加步数上限**
  - 照 `query_servers`（`backend/app/agents/tools/servers.py`）的样子，再写几个运维领域工具（日志检索、告警查询、白名单命令执行等）
  - `call_model ↔ call_tools` 这个循环现在没有步数上限——`reflect` 重试有 `MAX_ATTEMPTS` 兜底，但工具调用这一圈没有，模型如果一直坚持调工具会死循环，要补一个上限
  - 后续更名为只读的 `run_diagnostic_command`：从占位模拟升级成真实 asyncssh 执行，本地用 Docker 沙箱端到端验证过（对话触发、AI 正确执行命令并返回真实数据）
- [X] **前端展示工具调用过程**
  - 给 `chat_service.py` 的 SSE 事件流加 `tool_start`/`tool_end`（或类似）事件类型
  - 前端渲染成"正在调用 XXX 工具"这类过程提示，不再是纯黑盒等最终回答
- [ ] **运维领域子 agent —— 用 LangGraph 原生多 agent supervisor 模式实现**
  - 不做"子 agent 包成一个 tool"的简化版，用 supervisor / 多图 handoff 的标准写法——这是本项目的学习重点之一，不能图省事绕过去
  - 具体设计（几个子 agent、supervisor 怎么路由、状态怎么在子图间传递）留到真正开始做这一步时再展开
  - 已采用父级 Supervisor + metrics/logs/incident/infrastructure 四个独立子图；代码与静态检查完成后仍保持未勾选，等用户运行时验收再勾选

---

## 生产级演进路线（方向一~五）

背景：agent 骨架（ReAct + Reflexion）已成型，但仍偏 demo，两个根因——① 数据半 mock（`search_logs` 全假、`query_alerts` 是种子数据、无 metrics 工具）；② 纯被动（只有"用户问→答"，没有"告警来→自动诊断"的事件驱动闭环）。这五个方向把它往生产级推。设计取舍反复参考 [aiops-feature-survey.md](aiops-feature-survey.md)（Keep/OpenSRE 源码级调研）。

**数据栈已选定**：PLG 组合 —— Prometheus（Metrics）+ Loki（日志）+ Grafana（仪表盘）+ Alertmanager（告警分发）。云原生轻量全家桶，方向一按这个接。

**建议顺序**（仅建议，节奏自己把控）：方向一先切一小片（Prometheus metrics 工具 + 时间窗/实体关联原语）→ 方向二（告警自动诊断）；**只要打算加写操作工具，方向三的审计 + 真审批必须先落地**。

### 方向一：真实数据接入 + 三支柱关联（PLG）

把 mock 换成真实数据源，并建立"关联"能力——这是 AI SRE 和 chatbot 的分水岭。可对照调研过的 HolmesGPT 看它怎么组织关联。

- [x] **接 Prometheus**：新增 metrics 查询工具（PromQL 查 CPU/内存/延迟等时序）——当前 metrics 能力为零，最该补的洞
  - `query_metrics` 工具（cpu/memory/disk/up 模板 + PromQL 逃生口），数据源用 docker-compose 起的 Prometheus + node_exporter；踩过一个坑：本地 Prometheus 请求被环境 `HTTP_PROXY` 劫持，共享 httpx client 设 `trust_env=False` 解决
- [x] **接 Loki**：把 `search_logs`（`backend/app/agents/tools/logs.py`）从 mock 换成真实日志查询（LogQL）
  - 数据源用 docker-compose 起的 Loki + Promtail（采集容器日志）+ fake-app（造带级别的假日志）；顺带加了 `list_log_services` / `list_metric_instances` 两个发现工具，让 agent 能先查"有哪些服务/实例"再查具体数据
- [x] **接 Alertmanager**：`query_alerts`（`backend/app/agents/tools/alerts.py`）从种子数据换成查真实 firing 告警（先只读）
  - 数据源用 docker-compose 起的 Alertmanager + Prometheus 告警规则（`docker/alert_rules.yml`，3 条 demo）；`query_alerts` 查 `/api/v2/alerts`，可按 severity/instance 过滤。`alert_events` 表 + 种子脚本按选型 C.4 保留不动，留给方向二告警生命周期状态机
- [x] **Grafana 联动**（可选）：agent 能引用/生成 Grafana 面板链接，把结论落到统一仪表盘
  - `grafana_explore_link` 工具：按查询 + 时间窗拼 Grafana Explore 链接（纯字符串，不访问网络），支持 metrics（PromQL）/ logs（LogQL）/ alerts（Prometheus 的 ALERTS 指标）三种。docker-compose 加 grafana + 数据源 provisioning（固定 uid）。后续引入前端 md 渲染即可让链接可点击
- [x] **关联原语**：实体模型（服务→实例→依赖拓扑）+ 时间窗对齐——给定告警/时间点，自动拉同一服务、同一窗口的 metrics + logs（+ traces）一起看
  - `correlate_incident` 工具：给定业务服务器名 + 时刻，`asyncio.gather` 并发拉齐指标(range)+日志+告警+变更，同一时间窗对齐成横切面报告。实体映射优先使用规范化服务实例，旧 `servers.monitor_labels` 作为兼容回退。经对抗式审查加固（异常兜底、window 校验、自适应 step）。traces 暂缺（单机 PLG 无 Tempo）
- [ ] **读工具 / 写工具边界划分**（survey 启发 #2）：把纯读诊断工具和有破坏力的写工具分成两类抽象，别混在一起
  - 已新增集中式 `catalog.py`，读工具与写工具分开注册且禁止重叠；原 SSH 白名单命令明确改为只读诊断工具，不再触发审批
  - 已新增真实写工具 `restart_service`：人工审批 + systemd 服务白名单双重限制，worker 不绑定写工具，执行节点也会拒绝缺少审批决议的调用
  - 单元测试、Ruff 和前端构建已通过；按计划表规则暂不勾选，等用户在运行中的前后端验证读诊断和写审批流程

### 方向二：从被动问答到事件驱动的主动诊断

CLAUDE.md 项目定位里"值班/告警诊断"的核心闭环。

- [x] **告警 ingestion 层**：webhook 端点接收 Alertmanager 推送 → 落 `alert_events` 表
  - `POST /alerts/webhook` 接收 Alertmanager 推送，按 fingerprint upsert 落库（firing/resolved 更新同一行，天然去重）；`GET /alerts` 列出。`alert_events` 表重塑成真实告警结构（fingerprint/alertname/severity/status/summary/labels/instance/starts_at/ends_at）。alertmanager.yml 的 receiver 从 null 改成 webhook（host.docker.internal:8010）。旧 mock 结构 + seed_alerts.py 废弃
- [x] **后台任务 runner**：当前是纯请求-响应，需要 background/scheduled 能力（告警触发的诊断、定时值班报告都要它）
  - 用 arq + Redis 搭任务队列：webhook 落库时把「新触发」的告警入队，独立的 arq worker 进程（`app.worker.WorkerSettings`）消费。以后定时值班报告也挂这个 runner
- [x] **告警自动 triage**：告警落库后自动触发 agent 调查（拉 metrics/logs）→ 诊断结果推 Slack/IM
  - worker 的 `triage_incident` 复用 Supervisor 专家图：载入事件 → 按 `service_instances.metric_instance` 解析服务与服务器（旧 monitor_labels 回退）→ agent 调拓扑和 correlate 工具调查 → 诊断回填到事件。**推 Slack/IM 这步先缓**（诊断已落库、`GET /incidents` 可查，通知渠道以后单接）
- [x] **去重/关联层用规则引擎**（CEL 或等价），不用 LLM——survey 启发 #1，Keep 最成熟的实现就是纯规则，快/可解释/好测；这是 agent 之前的确定性预处理
  - `app/services/correlation.py`：用 CEL（celpy）写匹配条件 + group_by 归组，算出 `group_key`（同 key = 同一事件）。当前规则 by-instance：带 instance 的 critical/warning 告警按 `[instance, alertname]` 归组（同机同种告警去重、不同种各成事件）。ingest 时算 group_key 落库，**只有事件的第一条 firing 才入队诊断**，同事件其余静默归入——治好「同台机多条告警重复诊断」。dedup（按 fingerprint）本就有
- [x] **事件生命周期状态机**：`alert_events` 从平铺一条记录升级成 `FIRING/ACK/RESOLVED` 状态机（谁认领、后续如何）——survey 模块 6 / 启发 #6
  - 新建 `incidents` 一等实体（status firing/acknowledged/resolved、acked_by/acked_at、resolved_at、代表信息、diagnosis），`alert_events` 挂 `incident_id`（诊断从告警搬到事件级）。ingest 按 group_key 创建/关联活跃事件，告警全 resolved 时事件**自动 resolve**；triage 改成诊断事件（`triage_incident`）。API：`GET /incidents`、`POST /{id}/ack`、`POST /{id}/resolve`。经对抗式审查（并发建重复事件的低概率竞态已加 TODO 注释标注）
- [ ] **事件中心前端**：列表展示事件、状态筛选、诊断详情、认领与人工解决
  - 前端代码与静态检查完成后仍保持未勾选，等用户用运行中的前后端和真实事件完成验收再勾选

### 方向三：生产红线（审计 / 鉴权 / 真审批）

对照 survey 模块 5（调研最大的收获：Keep/OpenSRE 的"审批"都是假的、字段声明了没接线）。**只要打算加写操作工具，这一块必须先落地。**

- [ ] **审计日志**：每次工具调用/命令记录 谁/何时/哪台/结果，不可篡改——`run_diagnostic_command` 和 `restart_service` 现在真连 SSH 执行却零审计，这是生产红线
- [ ] **API 鉴权**：`/chat` 等端点加认证，知道"谁在用 agent"（当前无 auth）
- [ ] **写操作真审批网关**：LangGraph `interrupt` 卡在等人工确认的状态；并**专门写测试验证"没确认就走不通"**（survey 启发 #3，不能只是工具描述里写一句、或一个没人读取的 flag）
  - interrupt、恢复接口和安全测试已经接线；仍按验收规则保持未勾选，等真实前后端完成“未批准不执行、批准只执行一次”的运行时验证
- [ ] **密钥管理 + RBAC**：单 SSH key 放文件 → Vault/secrets manager；谁能在哪台跑哪些命令

### 方向四：agent 本身的工程化

- [ ] **可观测性**：tracing（LangSmith 之类）+ token 成本/延迟/工具成功率（当前只有 `print` 日志）
- [ ] **工具调用超时/熔断**：一个卡住的 SSH 不该把整个 agent 拖死
- [ ] **评估集（eval harness）**：一组运维场景的离线测试，改 prompt/换模型时能量化回归（当前验证全靠手跑）
- [ ] **迁移工具 + 测试套件**：多环境同步时把 Alembic（或等价）补回来；补后端测试套件

### 方向五：知识 / RAG（不急）

- [ ] **runbook / 历史事件 / postmortem 检索**：让 agent 用组织内部知识回答"我们通常怎么修 X"——向量库 pgvector/Chroma 后期决定
  - 注：survey 启发 #4，别因为"别人好像有"焦虑——两个调研项目真做出向量检索的一个都没有，工程量比宣传大，等真需要跨事件检索再做

> **关联**：上面「运维领域子 agent（supervisor）」那条属于方向一/二的"专业化"（日志分析/指标分析/事件指挥官子 agent）。但 survey 模块 7 提醒：图框架不是必选，子 agent 的价值在"专业化 + 真实数据"——没有真实数据，子 agent 之间也没什么可分工。
