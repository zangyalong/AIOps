# AIOps Agent Memory 设计

> 调研与实现日期：2026-08-13。本文区分“聊天记录”“LangGraph 工作记忆”和“跨会话长期记忆”，避免把一张消息表笼统称为 memory。

## 1. 调研结论

### LangGraph

LangGraph 把记忆分成两层：

- **短期记忆**：线程范围内的图状态，由 checkpointer 持久化；适合中断恢复和人工审批。
- **长期记忆**：跨线程、按 namespace 隔离的事实/经验；语义记忆、情景记忆、程序记忆应分开看待。

这与 AIOps 现有架构最匹配。项目原来已经用 `MemorySaver` 保存审批断点，但只存在后端进程内；完整历史则每轮从 `messages` 表重新加载，既不能跨重启恢复审批，也会让模型上下文无限增长。

### Mem0

Mem0 的关键价值不是某个向量数据库，而是长期记忆生命周期：

1. 从新对话提取少量稳定事实；
2. 与已有事实比较；
3. 执行 `ADD / UPDATE / DELETE / NONE`，避免同一事实不断重复；
4. 下一轮只检索少量相关记忆注入 Prompt。

Mem0 OSS 默认仍需要 LLM + embedding + vector store。AIOps 当前只有 DeepSeek Chat Completions 配置，DeepSeek 官方端点没有可直接复用的 embedding API；直接安装 Mem0 会额外引入 OpenAI/Ollama embedding 和 Qdrant/pgvector 等部署依赖。因此本项目复用其生命周期设计，数据仍进入现有 PostgreSQL，检索器保留以后替换 pgvector 的边界。

### Graphiti / Zep

Graphiti 的时间知识图谱适合处理“事实何时生效、何时失效”和实体关系，尤其适合动态基础设施。但开源版需要自带第三方图数据库，用户/会话管理和检索装配也要自行实现。AIOps 第一版不为 memory 增加一套图数据库运维面；如果以后要做跨事件因果图、拓扑随时间变化和 point-in-time 查询，再评估 Graphiti。

### Letta / MemGPT

Letta 用可编辑 memory blocks、对话摘要和外部记忆构建完整的 stateful agent runtime。它更像替换现有 Agent 宿主，而不是给 LangGraph 应用增加一个模块。AIOps 已有审批状态机、工具目录和 Chat Run 生命周期，因此只借鉴“始终可见的小型核心记忆 + 可检索外部记忆”的分层思想，不替换运行时。

### AIOps 同类项目

源码调研见 [`aiops-feature-survey.md`](aiops-feature-survey.md)：Keep 没有长期 Agent memory；OpenSRE 当时的所谓 episodic memory 实际只是 JSONL 会话持久化，没有跨 session 的抽取、合并和检索。说明 AIOps 不能只把已有 `messages` 表改名成 memory 就宣称完成。

## 2. 最终架构

```text
messages（完整、可审计聊天记录）
    │
    ├─ 每轮开始：会话滚动摘要 + 近期完整轮次 ──────────┐
    │                                                   │
    └─ 成功 Run 结束：                                   │
           ├─ 只取 user + 最终 assistant → 长期记忆整理  │
           └─ 超预算旧完整轮次 → 滚动会话摘要             │
                    │                                     ▼
                    ▼                           memories 两级相关性检索
             DeepSeek JSON 整理                           │
                    │                                     │
          ADD / UPDATE / DELETE                           │
                    │                                     │
                    └──── PostgreSQL memories ────────────┤
                                                         ▼
                                      <conversation_summary>
                                      <long_term_memories>
                                                         │
                                                         ▼
                                                   Ops Agent Prompt

LangGraph 图状态 ── AsyncPostgresSaver ── PostgreSQL checkpoint tables
                                   └─ 只恢复 waiting_approval
```

三种数据职责不同：

| 数据 | 作用域 | 是否完整 | 用途 |
| --- | --- | --- | --- |
| `messages` | 单个 conversation | 是 | UI 历史、审计、失败现场；压缩不删除原文 |
| `conversations.memory_summary` | 单个 conversation | 否，滚动摘要 | 旧摘要 + 被淘汰完整轮次；与近期原文共同进入模型上下文 |
| LangGraph checkpoint | 单个 Chat Run/thread | 图执行状态 | 审批中断和跨重启恢复；终态后删除 |
| `memories` 全局作用域 | 当前可信 actor，跨 conversation / server | 否，只存提炼事实 | 用户偏好、通用约定、跨服务器决策和经验 |
| `memories` 服务器作用域 | 当前可信 actor + 已纳管服务器 UUID | 否，只存提炼事实 | 单台服务器的稳定环境线索、决策和经验 |

每轮始终检索全局记忆。只有界面默认目标，或用户正文唯一命中服务器目录中的名称/IP 时，才追加
该服务器记忆；正文同时命中多台服务器时不猜测。服务器被删除后，其记忆由外键级联删除，不会提升
为全局记忆。

## 3. 记忆类别和时效性边界

- `preference`：回答风格、交互方式、工作偏好，可跨主题注入。
- `environment`：稳定资源身份、拓扑和命名约定，只能作为调查线索。
- `decision`：用户明确确认的长期技术或流程决策。
- `lesson`：已经确认的历史排障经验，必须以历史经验表达。

以下内容禁止入长期记忆：凭据、Token、API Key、私钥；日志/命令长原文；当前指标值；服务此刻状态；正在 firing 的告警；临时错误；模型猜测；未经确认的根因。

即使是允许的 `environment` 或 `lesson`，Prompt 也明确标记为不可信历史线索。当前服务器、服务、配置、告警、指标或日志事实仍必须重新调用只读调查工具核验，记忆不能扩大工具权限或跳过审批。

## 4. 安全与失败语义

- 整理器只读取本轮用户正文和最终回答，不读取 ToolMessage、日志和其它工具原始输出，降低日志 Prompt 注入进入长期记忆的风险。
- 整理器输出经过 JSON、Pydantic、稳定键、类别、长度、敏感词和瞬时状态校验后才落库。
- 记忆使用服务端 `operation_actor_id` 作为 owner scope，前端不能自报用户身份；服务器 scope 只接受后端目录中的 UUID。
- 删除使用软停用，保留纠正来源与审计语义；服务器本身被删除时，其服务器记忆级联删除。
- `/memories` 支持按全局/服务器查看、手动新增、单条遗忘和按作用域清空；手动新增同样执行敏感信息与瞬时状态校验。
- 记忆整理发生在 `done` 之后；失败只记日志，不会把已经完成的 Chat Run 改成失败。
- 会话摘要也在 `done` 后按需生成，只读取用户正文和最终回答，不复制工具原文；摘要失败不会影响 Chat Run，原始消息永不改写。
- PostgreSQL checkpoint 使用 LangGraph 安全类型白名单反序列化。
- 进程重启只恢复尚未执行的 `waiting_approval`。执行中的 Run 和 Operation 仍失败关闭，绝不自动重放可能有副作用的工具。
- checkpoint 和 `operation_records` 的待批操作 ID 必须完全一致才允许恢复，否则 Run 直接失败。

## 5. 当前限制和演进点

1. 当前是本地单用户模式，scope 使用 `operation_actor_id`；接入鉴权后应换成真实、不可伪造的用户/租户 ID。
2. 关键词相关性检索适合当前数据量和中文运维实体名，但不等价于语义向量检索。配置独立 embedding 后可在 `memory_service.search_memories()` 内替换为 pgvector。
3. 后台整理目前依附 FastAPI 进程内 Chat Run；进程恰好在回答完成后、整理前退出时，本轮记忆会延迟缺失。若需要强投递保证，可把同一个幂等服务挂到 arq 作业。
4. 第一版没有让 Agent 自由调用“记住/遗忘”工具，避免工具选择干扰运维调查；用户可以通过明确纠正语句触发整理，或在 Memory 页面手动新增/遗忘。
5. 还没有 memory eval 数据集。需要持续收集“应该记 / 不该记 / 应该更新 / 检索是否相关”的真实样本，再调 Prompt 和阈值。

## 6. 参考资料

- [LangGraph Memory overview](https://docs.langchain.com/oss/python/concepts/memory)
- [LangGraph Add memory](https://docs.langchain.com/oss/python/langgraph/add-memory)
- [LangGraph PostgreSQL checkpoint reference](https://reference.langchain.com/python/langgraph/checkpoints)
- [Mem0 GitHub](https://github.com/mem0ai/mem0)
- [Mem0 paper](https://arxiv.org/abs/2504.19413)
- [Graphiti GitHub](https://github.com/getzep/graphiti)
- [Letta GitHub](https://github.com/letta-ai/letta)
