# 开发计划表

跟踪多步骤任务的进度。这不是项目路线图，只在用户明确列出一批任务时才新增/更新——规则见 [CLAUDE.md](../CLAUDE.md) 「与 Claude 的协作模式」第 9 条。

## 运维 Agent 能力升级

背景：现在的 chat 图（`plan → call_model ↔ call_tools → reflect`，见 `backend/app/agents/graphs/chat_graph.py`）只有 `query_servers` 一个工具、工具调用循环没有步数上限、前端看不到工具调用过程、也没有子 agent。这组任务把它往"能调多个运维工具、有专业子 agent"的方向推进。

- [ ] **扩充运维工具 + 给工具调用循环加步数上限**
  - 照 `query_servers`（`backend/app/agents/tools/servers.py`）的样子，再写几个运维领域工具（日志检索、告警查询、白名单命令执行等）
  - `call_model ↔ call_tools` 这个循环现在没有步数上限——`reflect` 重试有 `MAX_ATTEMPTS` 兜底，但工具调用这一圈没有，模型如果一直坚持调工具会死循环，要补一个上限
- [ ] **前端展示工具调用过程**
  - 给 `chat_service.py` 的 SSE 事件流加 `tool_start`/`tool_end`（或类似）事件类型
  - 前端渲染成"正在调用 XXX 工具"这类过程提示，不再是纯黑盒等最终回答
- [ ] **运维领域子 agent —— 用 LangGraph 原生多 agent supervisor 模式实现**
  - 不做"子 agent 包成一个 tool"的简化版，用 supervisor / 多图 handoff 的标准写法——这是本项目的学习重点之一，不能图省事绕过去
  - 具体设计（几个子 agent、supervisor 怎么路由、状态怎么在子图间传递）留到真正开始做这一步时再展开
