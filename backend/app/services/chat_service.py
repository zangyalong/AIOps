from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools.catalog import TOOLS_BY_NAME, WRITE_TOOL_NAMES
from app.db.session import AsyncSessionLocal
from app.services import conversation_service, message_service


class ChatStreamEvent(TypedDict, total=False):
    type: str  # "token" | "tool_start" | "tool_end" | "approval_required" | "done" | "error"
    content: str
    conversation_id: str  # 要经 json.dumps 发给前端，uuid.UUID 必须转成字符串
    title: str
    message: str
    tool: str  # tool_start 时带上工具名，前端渲染成"正在调用 XXX"
    id: str  # tool_start / tool_end 用同一个 run_id 配对，前端据此把某个工具标记成已完成
    thread_id: str  # approval_required：恢复请求要带回来的挂起线程标识
    actions: list[
        dict
    ]  # approval_required：待批的写操作（每项带 kind，前端按 kind 渲染）


def _business_tool_names(message_or_chunk: object) -> set[str]:
    """提取模型消息/流式块里的业务工具名，排除 Supervisor 的结构化输出等内部 tool call。"""
    calls = [
        *getattr(message_or_chunk, "tool_calls", []),
        *getattr(message_or_chunk, "tool_call_chunks", []),
    ]
    return {
        name
        for call in calls
        if isinstance(call, dict)
        and (name := call.get("name"))
        and name in TOOLS_BY_NAME
    }


async def _load_history_messages(
    db: AsyncSession, conversation_id: uuid.UUID
) -> list[BaseMessage]:
    """把历史消息行还原成 LangChain 消息序列（完整轨迹），直接喂回图。

    现在历史里 assistant 是带 tool_calls 的、后面跟着 tool 结果——是"调了工具才回答"的好样例，
    不会再造成"不调工具直接吐数据"的坏样例（那正是当初改用摘要的原因，现在 tool_calls 落库了、
    这条障碍没了）。数据新鲜度靠 PERSONA 铁律兜（上文历史里的工具结果是旧快照、不能当本轮结果）。

    注意配对：带 tool_calls 的 AIMessage 后面必须紧跟对应 tool_call_id 的 ToolMessage，否则
    DeepSeek 会 400。历史行是按调用顺序落库的，逐行还原即保持这个配对，不用额外处理。
    """
    history = await message_service.list_messages(db, conversation_id)
    messages: list[BaseMessage] = []
    for m in history:
        if m.role == "user":
            messages.append(HumanMessage(m.content))
        elif m.role == "assistant":
            messages.append(
                AIMessage(content=m.content, tool_calls=(m.tool_calls or []))
            )
        elif m.role == "tool":
            messages.append(
                ToolMessage(content=m.content, tool_call_id=m.tool_call_id or "")
            )
    return messages


async def _persist_turn(
    conversation_id: uuid.UUID, turn_messages: list[BaseMessage]
) -> None:
    """把本轮那段轨迹（最后一条 HumanMessage 之后的 assistant/tool 消息）逐条落库。

    turn_messages 已切在最后一条 HumanMessage 之后，只会是 assistant / tool——它之前都是历史、
    库里已有，不能重复存。每条单独 commit，靠各自不同的 created_at 保证顺序（顺序 await、事务
    开始时间递增）。最终综合回答只生成一次，因此图里的本轮轨迹可以直接按顺序保存。
    """
    async with AsyncSessionLocal() as db:
        for msg in turn_messages:
            if isinstance(msg, AIMessage):
                await message_service.create_message(
                    db,
                    conversation_id,
                    role="assistant",
                    content=str(msg.content),
                    tool_calls=(msg.tool_calls or None),
                )
            elif isinstance(msg, ToolMessage):
                await message_service.create_message(
                    db,
                    conversation_id,
                    role="tool",
                    content=str(msg.content),
                    tool_call_id=msg.tool_call_id,
                )
            # turn_messages 已切在最后一条 HumanMessage 之后，只会是 assistant / tool
        await conversation_service.touch_conversation(db, conversation_id)


async def _drive_graph(
    graph: CompiledStateGraph,
    conversation_id: uuid.UUID,
    graph_input: object,
    thread_id: str,
    new_title: str | None = None,
) -> AsyncIterator[ChatStreamEvent]:
    """驱动图跑一段，流式转发工具旁白、工具卡片和最终回答：

    - 停在审批门（interrupt 挂起）：发 approval_required，带上 thread_id 和待批动作，本段到此为止、
      不落库（这一轮还没走完，恢复请求会接着把它跑完）。
    - 正常跑完：带业务工具调用的专家旁白会在工具卡片前显示；synthesize 生成的最终回答直接流给
      前端。图结束后把完整轨迹落库并发 done，因此刷新前后的展示顺序一致。

    thread_id 决定用哪个 checkpoint 线程；初次和它的恢复共用同一个 thread_id 才能接上同一次挂起。
    这段不持有任何 db 连接（落库另开短事务），流式期间不占着连接。
    """
    config = {"configurable": {"thread_id": thread_id}}
    # 专家最终内部报告和“准备调用工具”的旁白都来自模型文本流，开始时无法区分。先按模型 run_id
    # 缓冲；一旦同一响应出现真实业务 tool_call，才把旁白发给前端。没有 tool_call 的内部报告在
    # on_chat_model_end 时直接丢弃，避免把它和最终 synthesize 回答重复展示。
    pending_narration: dict[str, list[str]] = {}
    visible_narration_runs: set[str] = set()
    try:
        async for event in graph.astream_events(
            graph_input, config=config, version="v2"
        ):
            kind = event["event"]
            event_name = event.get("name", "")
            node = event.get("metadata", {}).get("langgraph_node")

            # 工具调用的开始/结束：只转发只读工具的 start/end 让前端画工具行（TOOLS_BY_NAME 兜底
            # 过滤）。写工具（WRITE_TOOL_NAMES）不发——它由前端那条带按钮的审批行代表自己
            # （approval_required + 裁决后翻成"已批准/已拒绝"），再发工具行会重复成两条。
            # start/end 带同一个 run_id，前端据此标记"进行中→完成"。
            is_visible_tool = (
                event_name in TOOLS_BY_NAME and event_name not in WRITE_TOOL_NAMES
            )
            if kind == "on_tool_start" and is_visible_tool:
                yield {
                    "type": "tool_start",
                    "id": event["run_id"],
                    "tool": event_name,
                }
            elif kind == "on_tool_end" and is_visible_tool:
                yield {"type": "tool_end", "id": event["run_id"]}
            elif kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                content = chunk.content if isinstance(chunk.content, str) else ""
                if node == "synthesize":
                    if content:
                        yield {"type": "token", "content": content}
                    continue

                run_id = event["run_id"]
                if content:
                    pending_narration.setdefault(run_id, []).append(content)
                if _business_tool_names(chunk):
                    visible_narration_runs.add(run_id)
                    buffered = "".join(pending_narration.pop(run_id, []))
                    if buffered:
                        yield {"type": "token", "content": buffered}
                elif run_id in visible_narration_runs and content:
                    # 极少数模型会在 tool_call chunk 后继续输出文字；确认可见后可以直接续流。
                    yield {"type": "token", "content": content}
            elif kind == "on_chat_model_end" and node != "synthesize":
                run_id = event["run_id"]
                output = event.get("data", {}).get("output")
                # 某些 OpenAI 兼容服务不在 stream chunk 里给完整工具名，只在最终 AIMessage 给；
                # 在模型结束事件再兜底识别一次，此时仍早于真正的 on_tool_start。
                if run_id not in visible_narration_runs and _business_tool_names(
                    output
                ):
                    buffered = "".join(pending_narration.pop(run_id, []))
                    if buffered:
                        yield {"type": "token", "content": buffered}
                pending_narration.pop(run_id, None)
                visible_narration_runs.discard(run_id)

        # 图停下来了：先看是不是停在审批门。带了 checkpointer，挂起状态能被 aget_state 读出来；
        # snapshot.interrupts 非空即为挂起，其 value 就是 approval_node 里 interrupt(...) 的入参。
        snapshot = await graph.aget_state(config)
        if snapshot.interrupts:
            pending = snapshot.interrupts[0].value
            approval_event: ChatStreamEvent = {
                "type": "approval_required",
                "thread_id": thread_id,
                "conversation_id": str(conversation_id),
                "actions": pending.get("actions", []),
            }
            if new_title is not None:
                approval_event["title"] = new_title
            yield approval_event
            return

        # 正常跑完：从最终快照取干净轨迹落库（snapshot.values 就是终态通道值）。只取最后一条
        # HumanMessage 之后的本轮部分——它之前都是历史、库里已有，不能重复存。
        all_messages = snapshot.values.get("messages", [])
        last_human = max(
            (i for i, m in enumerate(all_messages) if isinstance(m, HumanMessage)),
            default=-1,
        )
        await _persist_turn(conversation_id, all_messages[last_human + 1 :])

        done_event: ChatStreamEvent = {
            "type": "done",
            "conversation_id": str(conversation_id),
        }
        if new_title is not None:
            done_event["title"] = new_title
        yield done_event
    except Exception as exc:  # noqa: BLE001 - 流式响应里要把错误也发给前端，不能直接 500
        yield {"type": "error", "message": str(exc)}


async def stream_chat_turn(
    graph: CompiledStateGraph,
    conversation_id: uuid.UUID,
    user_message: str,
    new_title: str | None = None,
) -> AsyncIterator[ChatStreamEvent]:
    """跑一轮新的对话输入：存用户消息 + 加载历史喂给图，之后交给 _drive_graph 流式吐结果、落库。

    每轮用一个全新 thread_id：图带了 checkpointer 后，fresh thread 保证 checkpointer 每轮从空
    开始，和"照旧把完整历史喂进来"不冲突（否则会和上一轮残留状态叠加成重复消息）。命中审批门时，
    这个 thread_id 随 approval_required 发给前端，恢复请求带着它回来才能接上同一次挂起。
    new_title 只在这是一次新对话时传入，会附带在终结事件（done 或 approval_required）里。
    """
    thread_id = uuid.uuid4().hex
    # 短事务：加载历史（还原成真消息序列）+ 用户消息落库。用户这句先落库——即使后面 LLM 调用失败，
    # 至少用户发过什么是有记录的。开完就关，不占着连接跑后面的流式。
    async with AsyncSessionLocal() as db:
        history_messages = await _load_history_messages(db, conversation_id)
        await message_service.create_message(
            db, conversation_id, role="user", content=user_message
        )

    graph_input = {
        "messages": [*history_messages, HumanMessage(user_message)],
        "work_messages": [],
        "reports": {},
        "specialist_instruction": "",
        "tool_iterations": 0,
        "supervisor_rounds": 0,
        "allow_write_tools": True,
        "approval": None,
    }
    async for event in _drive_graph(
        graph, conversation_id, graph_input, thread_id, new_title=new_title
    ):
        yield event


async def resume_chat_turn(
    graph: CompiledStateGraph,
    conversation_id: uuid.UUID,
    thread_id: str,
    approved: bool,
    reason: str | None = None,
) -> AsyncIterator[ChatStreamEvent]:
    """恢复一次挂在审批门上的对话：把用户裁决用 Command(resume=) 传回图，从挂起点接着往下跑
    （批准→执行写工具 / 拒绝→回喂已拒绝消息 → agent 收尾 → 落库）。不新增用户消息——它只是上一段的
    延续。thread_id 必须和当初 approval_required 里发出去的那个一致，才能接上同一次挂起。
    """
    decision = {"approved": approved, "reason": reason or ""}
    async for event in _drive_graph(
        graph, conversation_id, Command(resume=decision), thread_id, new_title=None
    ):
        yield event
