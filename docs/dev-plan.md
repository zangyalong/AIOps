# 开发计划表

跟踪多步骤任务的进度。这不是项目路线图，只在用户明确列出一批任务时才新增/更新——规则见 [CLAUDE.md](../CLAUDE.md) 「与 Claude 的协作模式」第 9 条。

## 运维 Agent 能力升级

背景：现在的 chat 图（`plan → call_model ↔ call_tools → reflect`，见 `backend/app/agents/graphs/chat_graph.py`）只有 `query_servers` 一个工具、工具调用循环没有步数上限、前端看不到工具调用过程、也没有子 agent。这组任务把它往"能调多个运维工具、有专业子 agent"的方向推进。

- [X] **扩充运维工具 + 给工具调用循环加步数上限**
  - 照 `query_servers`（`backend/app/agents/tools/servers.py`）的样子，再写几个运维领域工具（日志检索、告警查询、白名单命令执行等）
  - `call_model ↔ call_tools` 这个循环现在没有步数上限——`reflect` 重试有 `MAX_ATTEMPTS` 兜底，但工具调用这一圈没有，模型如果一直坚持调工具会死循环，要补一个上限
  - `run_whitelisted_command` 后来又从占位模拟升级成真实 asyncssh 执行，本地用 Docker 沙箱端到端验证过（对话触发、AI 正确执行命令并返回真实数据）
- [X] **前端展示工具调用过程**
  - 给 `chat_service.py` 的 SSE 事件流加 `tool_start`/`tool_end`（或类似）事件类型
  - 前端渲染成"正在调用 XXX 工具"这类过程提示，不再是纯黑盒等最终回答
- [ ] **运维领域子 agent —— 用 LangGraph 原生多 agent supervisor 模式实现**
  - 不做"子 agent 包成一个 tool"的简化版，用 supervisor / 多图 handoff 的标准写法——这是本项目的学习重点之一，不能图省事绕过去
  - 具体设计（几个子 agent、supervisor 怎么路由、状态怎么在子图间传递）留到真正开始做这一步时再展开

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
- [ ] **Grafana 联动**（可选）：agent 能引用/生成 Grafana 面板链接，把结论落到统一仪表盘
- [ ] **关联原语**：实体模型（服务→实例→依赖拓扑）+ 时间窗对齐——给定告警/时间点，自动拉同一服务、同一窗口的 metrics + logs（+ traces）一起看
- [ ] **读工具 / 写工具边界划分**（survey 启发 #2）：把纯读诊断工具和有破坏力的写工具分成两类抽象，别混在一起

### 方向二：从被动问答到事件驱动的主动诊断

CLAUDE.md 项目定位里"值班/告警诊断"的核心闭环。

- [ ] **告警 ingestion 层**：webhook 端点接收 Alertmanager 推送 → 落 `alert_events` 表
- [ ] **后台任务 runner**：当前是纯请求-响应，需要 background/scheduled 能力（告警触发的诊断、定时值班报告都要它）
- [ ] **告警自动 triage**：告警落库后自动触发 agent 调查（拉 metrics/logs）→ 诊断结果推 Slack/IM
- [ ] **去重/关联层用规则引擎**（CEL 或等价），不用 LLM——survey 启发 #1，Keep 最成熟的实现就是纯规则，快/可解释/好测；这是 agent 之前的确定性预处理
- [ ] **事件生命周期状态机**：`alert_events` 从平铺一条记录升级成 `FIRING/ACK/RESOLVED` 状态机（谁认领、后续如何）——survey 模块 6 / 启发 #6

### 方向三：生产红线（审计 / 鉴权 / 真审批）

对照 survey 模块 5（调研最大的收获：Keep/OpenSRE 的"审批"都是假的、字段声明了没接线）。**只要打算加写操作工具，这一块必须先落地。**

- [ ] **审计日志**：每次工具调用/命令记录 谁/何时/哪台/结果，不可篡改——`run_whitelisted_command` 现在真连 SSH 执行却零审计，这是生产红线
- [ ] **API 鉴权**：`/chat` 等端点加认证，知道"谁在用 agent"（当前无 auth）
- [ ] **写操作真审批网关**：LangGraph `interrupt` 卡在等人工确认的状态；并**专门写测试验证"没确认就走不通"**（survey 启发 #3，不能只是工具描述里写一句、或一个没人读取的 flag）
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
