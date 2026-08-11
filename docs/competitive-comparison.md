# AIOMS 竞品对比与差距分析

> **目的**：方向一（真实数据接入 + 三支柱关联）、方向二（事件驱动主动诊断）做完后，跳出来看 AIOMS 和业界竞品的模块差距——我们已有的模块处在什么位置、哪些持平甚至领先、哪些是明显缺口。用来指导后续（方向三/四/五）该补什么。
>
> **与 [aiops-feature-survey.md](aiops-feature-survey.md) 的关系**：那份是对 Keep / OpenSRE 的**源码级**深调研；这份是更**广的竞品格局 + AIOMS 自身定位对比**，覆盖 AI-SRE 创业公司、可观测性大厂 AI、事件管理平台三大阵营。
>
> **方法与可信度**：
> - **「AIOMS 现状」部分**：读代码盘点，可靠。
> - **「竞品」部分**：部分来自实时网查官方文档/GitHub（已标注来源），部分来自既有认知；一批联网调研子 agent 撞上 API 限流，故个别版本级细节（融资额、EOL 日期、最新产品命名、厂商自报的准确率/MTTR 数字）**时效性打折、以官方页为准**。方向性的模块构成与对比结论是可靠的。
> - 初稿日期：2026-07；AIOMS 当前架构说明更新于 2026-08。

---

## 0. 一句话结论

**AIOMS 在「AI 诊断内核 + 事件流水线」这个内核上不单薄，反而比很多同类做得完整、有章法；单薄的是外面那层「运维平台外壳」**——通知/排班/审批/审计/看板/复盘/状态页。换句话说：**难而有技术含量的部分做扎实了，工程/流程/合规的壳子还很薄。**

---

## 1. AIOMS 现状盘点（13 维，以代码为准）

| # | 维度 | 状态 | 核心实现 |
|---|------|------|---------|
| 1 | 数据接入 | ✅ 有 | Prometheus/Loki/Alertmanager + Grafana 链接；模型通过统一 `investigate` 调查入口访问可观测性、服务目录、运行时探针和 root 只读宿主机证据 |
| 2 | 告警去重/关联 | ⚠️ 半 | dedup 有（按 fingerprint）；关联是 **CEL 规则引擎**（`correlation.py`，按 instance+alertname 归组）——是关联的**雏形**，非 ML/跨服务收敛 |
| 3 | 根因分析/自动诊断 | ✅ 有 | 事件触发 → arq worker 复用统一 Ops Agent（禁写）→ `investigate` 路由跨信号调查 → 诊断回填事件 |
| 4 | Agent 编排 | ✅ 有 | LangGraph StateGraph：`ops_agent → investigate/approval → ops_agent`；对模型只暴露调查和结构化变更两类能力 |
| 5 | 事件生命周期 | ✅ 有 | `incidents` 一等实体，`FIRING/ACKNOWLEDGED/RESOLVED` 状态机，告警全 resolved 时自动 resolve，ack/resolve API |
| 6 | 后台/事件驱动 | ✅ 有 | Alertmanager webhook → arq + Redis 队列 → 独立 worker 进程消费 |
| 7 | 写操作/自动化+审批 | ⚠️ 半 | systemd/Docker 控制、文件更新、日志配置和规则创建接入真审批；固定参数网关、双端校验和执行审计已落地，仍缺鉴权、RBAC 和不可篡改审计存储 |
| 8 | 知识/记忆/RAG | ❌ 无 | 无向量库/知识图谱/runbook 检索 |
| 9 | 可观测性/工程化 | ⚠️ 部分 | 已有后端单元测试、工具超时和 Chat Run 轨迹；仍无 Agent tracing/LangSmith 与系统化 eval 集 |
| 10 | 通知/协作 | ❌ 无 | 推 Slack/IM 明确缓做，诊断只落库 + `GET /alerts` 可查 |
| 11 | 鉴权/审计/RBAC/密钥 | ⚠️ 部分 | `operation_records` 记录调查与变更执行，审批身份由服务端配置；端点仍无正式 auth/RBAC，私钥仍以文件管理，审计日志尚不可篡改 |
| 12 | 前端 UI | ⚠️ 部分 | 已有对话、服务器、服务目录、预警规则和事件中心；审批卡片已接入，仍缺独立操作审计与 Agent 质量看板 |
| 13 | 数据库/迁移 | ⚠️ 部分 | SQLAlchemy + 启动 `create_all` + 手写脚本；**无 Alembic 等迁移工具**（方向四待办） |

**生产就绪度**：处于「原型 → 受控测试」之间。调查只读边界、审批和执行审计已成型，但正式鉴权、RBAC、主机指纹、密钥管理和不可篡改审计尚未完成，**不应直接暴露到生产网络**。

---

## 2. 竞品格局：三大阵营

业界把「AI 运维」大致分成三类玩家（分类框架参考 bobbytables 的 AI-SRE landscape）：

| 阵营 | 代表 | 从哪个「已拥有的资产」出发 | 强在哪 | 弱/缺 |
|---|---|---|---|---|
| **A. AI-SRE / LLM 原生**（创业公司 + 开源） | HolmesGPT、K8sGPT、Robusta、OpenSRE、Cleric、Parity、Deductive、NeuBird、Resolve.ai、Traversal | 从零做一个独立的「调查/诊断」层，向外接数据 | LLM+工具的根因调查、只读诊断 | 事件生命周期、去重关联、排班通知、鉴权审计 |
| **B. 可观测性大厂的 AI** | Datadog（Bits AI SRE）、Grafana（Sift/IRM） | 已经拥有**遥测数据**，向外接事件流程 | 数据面天然完整、下钻到图表 | AI 是近两年叠的新层 |
| **C. 事件管理 / AIOps 平台** | PagerDuty、Opsgenie、incident.io、Keep、BigPanda、Moogsoft | 已经拥有**告警/排班/事件流程**，向外接遥测 | 排班/升级/通知/复盘/合规（成熟） | AI 是叠上去的、深度参差 |

**AIOMS 横跨 A 和 C**：既有 A 的 AI 诊断内核，又有 C 的告警流水线（ingestion→关联→事件状态机）——这个组合在学习/自建项目里少见。

---

## 3. 竞品逐个速览

> 每个：定位 · 最强模块 · 读/写姿态 · 关键情报。★ = 与 AIOMS 直接可比的点。

### 阵营 A：AI-SRE / LLM 原生

- **HolmesGPT**（robusta-dev，CNCF Sandbox 2025-10）：agentic tool-calling 的根因调查引擎，是 Robusta 的 AI 大脑。数据源极广、**内置工具全只读**。★ 和 AIOMS 的 ReAct 调查同路，但它是纯调查、无事件流水线。
- **K8sGPT**（CNCF Sandbox）：**不是 agent**——确定性 Go analyzer 扫 K8s + LLM 只调用一次做解释（"extraction-then-explanation"），无 ReAct、无 tool-calling、无关联、无事件生命周期，K8s-only。自愈实验性（写集群、**无人工确认**、回滚 TODO），有 Slack/Mattermost sink。★ **AIOMS 的 Agent 架构明显强过它**。
- **Robusta**：确定性 **playbook 引擎**（trigger→action，50+ action，可写 Python）+ HolmesGPT 做 AI；20+ 通知 sink。★ **OSS 版 remediation 有 delete pod/drain/任意 kubectl，却无审批门控**；真正的「写 opt-in + IAM 约束」是 SaaS 卖点。别照抄它 OSS 那点。
- **OpenSRE**（Tracer-Cloud，~8k★，Apache-2.0）：**单** ReAct agent + 确定性 stage 骨架（intake→plan→gather→diagnose），80+ 只读集成，有**打分式合成 RCA eval 套件**。★ guardrails 只有 redact/关键词匹配、**不区分读写、无审批**——反面教材；「Neo4j 知识图谱/记忆」是某个小 fork 的宣传、不是主项目能力。
- **Cleric**（$9.8M，Gartner Cool Vendor 2025）：只读、Slack 原生、"ambient" 后台调查 agent；**per-tenant 运营记忆** + 置信度分 + 证据链接；remediation = 交 PR/工单给人或 coding agent 执行（自动写在 roadmap）。SOC2 Type II。★ 官网"apply fixes directly"的宣传跑在它 read-only 文档前面。
- **Parity**（YC S24）：**K8s 专精**，2 agent（调查 + runbook 执行），**设计上只读**（写靠 flag opt-in），集群内 egress-only pod。
- **Deductive AI**（$7.5M seed，CRV）：**知识图谱 + 多 agent**，「引导式」remediation（人在环）。DoorDash 案例（省 1000+ 工时/年）。
- **NeuBird / Hawkeye**（~$44.5M，Mayfield/M12）：单 agent + 向量库 runbook 匹配，**从只读演进到带护栏的自动 remediation**（scale/rollback/restart），显式置信度%（如 94%）。
- **Resolve.ai**（$150M+，$1B 估值，ex-Splunk/OTel 创始人）：**最激进的自主派**（"agents run your software"，~80% 自主目标），但仍是**分级 human-in-loop**（默认只读、写按需授予），动态知识图谱 + 多 agent。类目里的独角兽标杆。
- **Traversal**（$48M，Sequoia/Kleiner，因果-ML 学者创始）：**只读 RCA 专精**，**因果 ML**（Production World Model 环境图 + Causal Search Engine 并行 10k 假设 + 多跳因果溯源），显式**放弃「fix/remediation 最后一公里」**。AmEx/PepsiCo 客户。

### 阵营 B：可观测性大厂 AI

- **Datadog — Bits AI SRE**（GA 2025-12）：**单**「深度研究 agent」（branching hypothesis tree + agent harness + MCP 工具），**只读调查** + Bits Code 提 PR（preview）；坐在自家 800+ 集成的遥测上；**Agent Trace + 假设树 + citations 是全场最强的信任呈现**；补了 Datadog On-Call；RBAC/HIPAA。★ 「诊断只读、修复交人」的姿态和 AIOMS 一致。
- **Grafana — IRM（OnCall + Incident + Sift）**：on-call 排班/升级 + 完整事件生命周期 + **Sift（ML 自动调查 checks：K8s crash/OOM/noisy-neighbor/recent-deploy）**；LLM 插件支持 **Anthropic/OpenAI 兼容/DeepSeek**（★ 和 AIOMS 用 DeepSeek 同路）；ServiceNow/Jira 双向同步；RBAC+审计+Insights。**OSS OnCall 已定 2026-03 归档**，转 Cloud IRM。

### 阵营 C：事件管理 / AIOps 平台

- **PagerDuty**：on-call 事实标准（schedule/escalation/多渠道，无出其右）；AIOps ML 智能分组；Runbook Automation（收购 Rundeck）；内置 postmortem；**SRE Agent**（多 agent 家族 + 共享 memory API，建议 + **人工点按钮才执行**自动化）；RBAC/审计/Analytics；750+ 集成。
- **Opsgenie**：on-call/escalation + Jira/JSM 打通。**产品在日落**，能力并入 Jira Service Management + Compass。
- **incident.io**：**Slack 原生事件响应**（`/inc` 拉事件、角色、状态机 Triage→Active→Post-incident）+ 自研 on-call 模块 + Status Pages + Workflows + **自动 postmortem** + AI SRE（多 agent 调查、人工 gated PR）；RBAC/SSO/审计/Insights；60+ 集成。★ **AIOMS「on-call/通知」缺口的最佳蓝本**。
- **Keep**（开源，~12k★，MIT + ee/ 专有；**2025-05 被 Elastic 收购**）：**100+ providers（双向）** + **YAML/GitOps workflow 引擎**（"GitHub Actions for monitoring"）；dedup（fingerprint）+ **CEL 关联规则**（`cel-python`）+ 拓扑关联 + AI 关联（企业版）；correlation 可产出「incident-candidate 待人工批准」；**on-call 基本没有**；AI 后端支持 **DeepSeek**。★ **与 AIOMS 最像的开源参照物**——它有的（provider 模型、workflow-as-code、CEL、DeepSeek）值得抄，它缺的（on-call）也和你一样。
- **BigPanda**：**Open Box ML** 关联（透明、确定性、**关联模式可人工编辑**）+ Biggy GenAI copilot；**不做 on-call**（转 PagerDuty/ServiceNow）；强 RBAC/审计/SOC2/Unified Analytics。
- **Moogsoft**（2023 被 Dell 收购，现 Dell APEX AIOps）：**多算法 Sigalisers**（Cookbook/Tempus 时间社区检测/Nexus/Speedbird k-means/ACE/Feedback）+ 专利 Vertex Entropy 降噪 + 神经网络 PRC 根因；**不做 on-call**。类目里关联算法最硬核。

---

## 4. 贯穿全场的关键轴：只读调查 vs. 有门控的执行

这是整个赛道最一致的分界线，也是理解每个产品定位的钥匙。**成熟度曲线**：只读洞察 → 建议动作 → 审批式 remediation → 带护栏的自主。

| 姿态 | 谁在这 |
|---|---|
| **纯只读调查**（把答案/草稿交给人） | Cleric、Parity、Traversal、K8sGPT（分析侧）、Datadog Bits（调查侧） |
| **建议 + 人工 gated 执行**（AI 起草，人点按钮/合 PR） | incident.io、PagerDuty SRE Agent、Datadog Bits Code、NeuBird、Deductive |
| **激进自主（仍分级 human-in-loop）** | Resolve.ai（~80% 目标） |
| **写但缺门控**（反面） | K8sGPT 自愈、Robusta **OSS** remediation |

**AIOMS 在哪**：读诊断与写操作已拆开；systemd 启停/重启、服务目录注册、日志配置应用和预警规则创建都必须经过 LangGraph 人工审批。它进入了「建议 + 人工 gated 执行」阶段，但鉴权、RBAC、正式密钥管理和不可篡改审计尚未补齐。

**两条对 AIOMS 的启示**：
1. **连业界领头羊都把「写」用审批/IAM 关起来**——所以「破坏性操作必须人工二次确认」不是过度谨慎，是行业共识（也是你 CLAUDE.md 红线）。
2. **开源工具（K8sGPT、Robusta OSS）恰恰在「写但无审批」这点上薄弱**——方向三如果把「真审批 + 审计 + 有测试验证没确认就走不通」做扎实，是个能立住的差异化点。

---

## 5. 逐维度对比矩阵

✅ 有/强 · ⚠️ 半/部分 · ❌ 无

| 维度 | AIOMS | A：AI-SRE 工具 | B/C：大厂 + 事件平台 |
|---|---|---|---|
| 数据接入（读） | ✅ PLG 三支柱（深） | ✅ 多、侧重 K8s/云 | ✅✅ 上百集成（广） |
| 告警 dedup | ✅ fingerprint | ⚠️ 多数没有 | ✅ |
| **告警关联（收敛成事件）** | ⚠️ CEL 归组（雏形） | ❌ 多数没有 | ✅✅ ML/图论（BigPanda/Moogsoft 招牌） |
| **根因分析（AI）** | ✅ 统一 Ops Agent + 多源真实证据 | ✅ 各家核心 | ⚠️ 新叠 AI（Bits/Sift 已很强） |
| Agent 编排 | ✅ 单 Agent LangGraph 工具/审批状态机 | ⚠️ 多为顺序/单 ReAct，K8sGPT 甚至非 agent | ⚠️ 部分多 agent（incident.io/PD/Resolve） |
| 三支柱关联原语 | ✅ correlate_incident | ⚠️ 部分（Holmes/Traversal 因果图） | ⚠️ 靠看板/Sift |
| **事件生命周期** | ✅ FIRING/ACK/RESOLVED + 自动恢复 | ❌ 多数没有（OpenSRE 都没有） | ✅✅ 最成熟（合并/时间线/角色） |
| 事件驱动/后台队列 | ✅ webhook+arq+Redis | ⚠️ | ✅ |
| 写操作 + **审批门控** | ⚠️ 固定写网关 + 真审批 + 执行审计，仍无正式鉴权/RBAC | ⚠️ 窄，且审批常缺/假（OSS 尤甚） | ✅ 真 workflow + 审批（PD/incident.io） |
| **On-call / 排班 / 升级 / 通知路由** | ❌ **零** | ❌（少数有 sink 通知，如 K8sGPT/Robusta） | ✅✅ **命根子**（PD/Opsgenie/incident.io/Grafana） |
| 复盘 / postmortem / 学习闭环 | ❌ | ❌（个别有记忆宣传） | ✅（incident.io/PD 强） |
| 知识 / RAG / runbook | ❌ | ⚠️ 多为宣传（实测常是半成品） | ⚠️ 挂链接为主 |
| **Agent 工程化（tracing/eval）** | ⚠️ 只有 print | ⚠️（OpenSRE 有 eval 套件、Cleric 有闭环度量） | ✅（Datadog 有 LLM judge eval 平台） |
| UI / 看板 / SLA / 报表 | ⚠️ Chat+Servers+Services+Alerts+Incidents，无 SLA/审计看板 | ❌ 多为 CLI/Slack | ✅✅ 重 UI + MTTA/MTTR/Insights |
| 对外 Status Page | ❌ | ❌ | ✅（incident.io 等） |
| 鉴权 / 审计 / RBAC / 密钥 | ⚠️ 有操作审计，缺 auth/RBAC/正式密钥管理 | ❌（Cleric/大厂有 SOC2） | ✅✅ 合规必备 |

---

## 6. AIOMS 领先 / 持平的地方（别妄自菲薄）

- **诊断内核有章法**：统一 Ops Agent 只通过 `investigate` 获取真实证据，后端负责数据源路由和证据标注；最终回答由同一个 Agent 基于本轮工具结果生成，内部执行器不泄露给模型和用户。
- **关联用规则不用 LLM**：上了 **CEL**（`cel-python`），正是 Keep 同款、survey 启发#1 的正解——很多打「AI 关联」旗号的开源版其实是死代码或企业版专享。
- **事件生命周期状态机**：OpenSRE 这种专门的 AI-SRE **根本没有**；AIOMS 有一等 `incidents` + 三态 + 自动恢复。
- **correlate_incident 三支柱对齐**：正是 survey 说的「AI SRE 和 chatbot 的分水岭」。
- **架构踩在行业共识上**：业界公认的骨架是「agentic 假设循环 + 调可观测工具（渐渐走 MCP）+ 只读姿态」——AIOMS 全中，还额外补了纯 AI-SRE 工具跳过的**告警流水线 + 事件实体**。

---

## 7. 真正让它「单薄」的缺口（按价值排序）

1. **通知 / On-call / 升级**（❌零）——PagerDuty/Opsgenie/incident.io **整个产品的核心**；连 K8sGPT/Robusta 都有通知 sink。一个「事件管理」系统没有「通知到人 / 排班 / 升级」，是最扎眼的缺口。**蓝本：incident.io 的 On-call 模块**（schedule + follow-the-sun + escalation 带 P1/P2 分支和工作时间条件 + 5 渠道通知 + DND 绕过）。
2. **鉴权 / RBAC / 不可篡改审计**（⚠️部分）——真审批和数据库执行审计已经具备，下一步缺口是可信登录身份、角色授权、密钥托管和不可篡改存储；这仍是商业平台的合规底座。
3. **复盘 / 时间线 / 学习闭环**（❌）——事件解决后的沉淀，成熟平台标配（incident.io 自动生成 postmortem）。
4. **Agent 可观测性 / eval 集**（⚠️）——**这是让 AI「可靠」的关键**。Cleric 的话最扎心：「调查 agent 是个周末项目，让它可靠的那套系统才不是」。Datadog 专门建了带 LLM judge 的 eval 平台。AIOMS 现在只有 print，改 prompt/换模型无法量化回归（方向四）。
5. **UI 看板**（⚠️）——无告警页/事件页/时间线，运维「看不见」全局。
6. **集成广度**（深而不广）——PLG 够学习，离真实环境的上百连接器还远（可借 Keep 的 provider 模型）。
7. **知识 / RAG / runbook**、**对外 Status Page**、**报表/SLA/MTTR**（均 ❌）——次优先。

---

## 8. 一份来自「怀疑派」的清醒剂（值得内化）

赛道里有一股很有分量的批判声音（很多来自从业者甚至 AI-SRE 创始人自己）：

- **全自主是错的目标**：技术细节的**幻觉率约 23%**；Stanford-CMU 研究——**人主导 + AI 增强的工作流，比全自主 agent 好 68.7%**；活跃事件里一次自信的错误诊断代价约 **$9,000/分钟**。
- **AI 不是替代 SRE，而是在「去技能化」**（Bainbridge《自动化的反讽》）：自动化越多，人在少数失效时刻越关键、却越缺练习；对新人是「never-skilling」。处方是 **human-in-the-loop, not human-on-the-side**。
- **宣传 vs 能力有差距**：很多工具标榜自主 remediation，实际要大量针对性配置；厂商自报的准确率/MTTR 数字**未经独立审计**。

**这些恰好印证 AIOMS 的路子是对的**：human-in-the-loop、真实工具证据约束、CLAUDE.md 安全红线——不是保守，是踩在赛道的共识与教训上。

---

## 9. 结论与下一步建议

**内核（AI 诊断 + 事件流水线）扎实甚至领先；外壳（通知/排班/审批/审计/看板/复盘/状态页）单薄。** 要让它不「单薄」，性价比最高的下一步**不是继续加 AI**，而是补外壳里价值最高的两块：

1. **On-call / 通知**——让诊断结果真能「通知到人」，且这是行业标配你恰好全缺。以 **incident.io / Grafana IRM / PagerDuty** 为蓝本做最小可用。
2. **方向三：鉴权 + 审计 + 真审批**——让系统敢碰真实系统，也是你已规划、且卡着「能不能真用」的关键；因开源同行普遍薄弱，是能立住的差异化点。

其后：**Agent eval 集**（方向四）——业界把「可测量」当成 AI 可靠的前提，这是让内核从「demo 好用」走向「敢信」的必经之路。

---

## 附：主要来源（择要）

- **格局/分析**：bobbytables AI-SRE landscape；Cleric《State of AI SRE》；incident.io《What is AI SRE 2026》；Forbes / SigNoz（怀疑派）。
- **开源/AI-SRE**：github.com/robusta-dev/holmesgpt、k8sgpt-ai/k8sgpt、robusta-dev/robusta、Tracer-Cloud/opensre、keephq/keep；cleric.ai、tryparity.com、deductive.ai、neubird.ai、resolve.ai、traversal.com（官方 docs/blog）。
- **大厂/平台**：datadoghq.com/blog（Bits AI SRE）、grafana.com/docs（IRM/OnCall/Sift）、pagerduty.com、incident.io/docs、bigpanda.io、docs.moogsoft.com。
- **AIOMS 自身**：本仓库 `backend/app/`、`docs/dev-plan.md`、`docs/aiops-feature-survey.md`。

> 注：厂商自报数字（融资额、准确率、MTTR 降幅、集成数量）与「宣传 vs 已发布能力」的差异，正文已就地标注；引用前建议以官方页最终核对。
