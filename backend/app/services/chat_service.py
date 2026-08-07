from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_openai import StreamChunkTimeoutError
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools.catalog import TOOLS_BY_NAME, WRITE_TOOL_NAMES
from app.db.session import AsyncSessionLocal
from app.models.message import Message
from app.services import (
    chat_run_record_service,
    conversation_service,
    message_service,
)


class ChatStreamEvent(TypedDict, total=False):
    type: str  # token/tool/approval/done/error
    seq: int  # ChatRunManager 写入的运行内事件序号，重连和审批续传使用
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
    approved: bool  # approval_resolved：人工裁决结果


@dataclass(frozen=True)
class PreparedChatTurn:
    """已经完成历史加载和用户消息落库、可以交给后台 Chat Run 执行的一轮输入。"""

    graph_input: dict
    thread_id: str
    run_id: uuid.UUID


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
    """把历史消息行还原成可安全喂给模型的 LangChain 消息序列。

    前端会拿到全部消息，包括失败 Run 的现场；这里的模型历史更严格：assistant 只要带
    tool_calls，就必须在紧随其后的 tool 行中找到全部且唯一的 tool_call_id，否则整段调用及其
    残留结果都跳过，避免旧失败现场再次触发 OpenAI 兼容接口的 400。
    """
    history = await message_service.list_messages(db, conversation_id)
    return _safe_history_messages(history)


def _safe_history_messages(history: list[Message]) -> list[BaseMessage]:
    """将数据库消息转换成协议完整的模型历史；不修改前端可见历史。"""
    messages: list[BaseMessage] = []
    index = 0
    while index < len(history):
        message = history[index]
        if message.role == "user":
            messages.append(HumanMessage(message.content))
            index += 1
            continue

        if message.role == "assistant" and not message.tool_calls:
            messages.append(AIMessage(content=message.content))
            index += 1
            continue

        if message.role == "assistant" and message.tool_calls:
            call_ids = [
                str(call.get("id") or "")
                for call in message.tool_calls
                if isinstance(call, dict)
            ]
            next_index = index + 1
            tool_rows: list[Message] = []
            while next_index < len(history) and history[next_index].role == "tool":
                tool_rows.append(history[next_index])
                next_index += 1

            result_ids = [row.tool_call_id or "" for row in tool_rows]
            complete = (
                len(call_ids) == len(message.tool_calls)
                and all(call_ids)
                and len(set(call_ids)) == len(call_ids)
                and len(result_ids) == len(call_ids)
                and set(result_ids) == set(call_ids)
            )
            if complete:
                messages.append(
                    AIMessage(
                        content=message.content,
                        tool_calls=message.tool_calls,
                    )
                )
                messages.extend(
                    ToolMessage(
                        content=row.content,
                        tool_call_id=row.tool_call_id or "",
                    )
                    for row in tool_rows
                )
            index = next_index
            continue

        # 孤立 tool 行和未知角色都不进入模型历史。
        index += 1
    return messages


def _current_turn_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    last_human = max(
        (i for i, message in enumerate(messages) if isinstance(message, HumanMessage)),
        default=-1,
    )
    return messages[last_human + 1 :]


async def _persist_run_messages(
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    run_messages: list[BaseMessage],
) -> None:
    """幂等保存某一 Run 新产生的 assistant/tool 消息。

    tool_call_id 是协议天然的幂等键；最终综合回答每个 Run 只有一条。审批恢复和终态快照都可再次
    调用本函数，不会复制已经落库的消息。
    """
    async with AsyncSessionLocal() as db:
        existing = await message_service.list_run_messages(db, run_id)
        persisted_call_ids = {
            str(call.get("id") or "")
            for row in existing
            if row.role == "assistant"
            for call in (row.tool_calls or [])
            if isinstance(call, dict)
        }
        persisted_result_ids = {
            row.tool_call_id
            for row in existing
            if row.role == "tool" and row.tool_call_id
        }
        has_final_answer = any(
            row.role == "assistant" and not row.tool_calls for row in existing
        )

        for message in run_messages:
            if isinstance(message, AIMessage) and message.tool_calls:
                call_ids = {
                    str(call.get("id") or "")
                    for call in message.tool_calls
                    if isinstance(call, dict) and call.get("id")
                }
                if call_ids and call_ids.issubset(persisted_call_ids):
                    continue
                await message_service.create_message(
                    db,
                    conversation_id,
                    run_id=run_id,
                    role="assistant",
                    content=str(message.content),
                    tool_calls=message.tool_calls,
                )
                persisted_call_ids.update(call_ids)
            elif isinstance(message, ToolMessage):
                if message.tool_call_id in persisted_result_ids:
                    continue
                await message_service.create_message(
                    db,
                    conversation_id,
                    run_id=run_id,
                    role="tool",
                    content=str(message.content),
                    tool_call_id=message.tool_call_id,
                )
                persisted_result_ids.add(message.tool_call_id)
            elif isinstance(message, AIMessage) and not has_final_answer:
                await message_service.create_message(
                    db,
                    conversation_id,
                    run_id=run_id,
                    role="assistant",
                    content=str(message.content),
                )
                has_final_answer = True
        await conversation_service.touch_conversation(db, conversation_id)


async def _persist_checkpoint_messages(
    graph: CompiledStateGraph,
    config: dict,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
) -> object:
    snapshot = await graph.aget_state(config)
    all_messages = snapshot.values.get("messages", [])
    await _persist_run_messages(
        conversation_id,
        run_id,
        _current_turn_messages(all_messages),
    )
    return snapshot


async def _set_run_status(
    run_id: uuid.UUID,
    status: chat_run_record_service.ChatRunStatus,
    *,
    error_message: str | None = None,
) -> None:
    async with AsyncSessionLocal() as db:
        chat_run = await chat_run_record_service.get_chat_run(db, run_id)
        if chat_run is None:
            raise LookupError(f"chat run {run_id} not found")
        await chat_run_record_service.set_status(
            db,
            chat_run,
            status,
            error_message=error_message,
        )


async def _persist_partial_answer(
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    chunks: list[str],
) -> None:
    content = "".join(chunks).strip()
    if content:
        await _persist_run_messages(
            conversation_id,
            run_id,
            [AIMessage(content)],
        )


async def _mark_failed(
    run_id: uuid.UUID,
    error_message: str,
) -> None:
    try:
        await _set_run_status(run_id, "failed", error_message=error_message)
    except Exception as exc:  # noqa: BLE001 - 不让状态落库失败遮蔽原始模型/工具错误
        print(f"[chat-run] 标记 {run_id} 失败时出错：{exc}")


async def _persist_node_output(
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    output: object,
) -> None:
    """保存专家、工具或综合节点真正写回图状态的新消息，顺序与图节点一致。"""
    if not isinstance(output, dict):
        return
    messages = output.get("messages")
    if not isinstance(messages, list):
        return
    new_messages = [message for message in messages if isinstance(message, BaseMessage)]
    if new_messages:
        await _persist_run_messages(conversation_id, run_id, new_messages)


async def _drive_graph(
    graph: CompiledStateGraph,
    conversation_id: uuid.UUID,
    graph_input: object,
    thread_id: str,
    run_id: uuid.UUID,
    new_title: str | None = None,
) -> AsyncIterator[ChatStreamEvent]:
    """驱动图跑一段，流式转发工具旁白、工具卡片和最终回答：

    - tool call / tool result 随运行逐步幂等落库，失败 Run 也能保留已经发生的完整现场；
    - 停在审批门：同步当前 checkpoint，更新 Run 为 waiting_approval，再发审批事件；
    - 正常结束：同步终态 checkpoint，更新 Run 为 completed，再发 done；
    - 异常：尽量同步 checkpoint 和已流出的部分最终回答，更新 Run 为 failed。

    thread_id 决定用哪个 checkpoint 线程；初次和它的恢复共用同一个 thread_id 才能接上同一次挂起。
    这段不持有任何 db 连接（落库另开短事务），流式期间不占着连接。
    """
    config = {"configurable": {"thread_id": thread_id}}
    # 专家最终内部报告和“准备调用工具”的旁白都来自模型文本流，开始时无法区分。先按模型 run_id
    # 缓冲；一旦同一响应出现真实业务 tool_call，才把旁白发给前端。没有 tool_call 的内部报告在
    # on_chat_model_end 时直接丢弃，避免把它和最终 synthesize 回答重复展示。
    pending_narration: dict[str, list[str]] = {}
    visible_narration_runs: set[str] = set()
    final_answer_chunks: list[str] = []
    try:
        await _set_run_status(run_id, "running")
        async for event in graph.astream_events(
            graph_input, config=config, version="v2"
        ):
            kind = event["event"]
            event_name = event.get("name", "")
            node = event.get("metadata", {}).get("langgraph_node")

            # 节点输出里的消息已经经过图内规范化：specialist 带 tool_calls，tools 带逐调用结果，
            # synthesize 带最终回答。按节点结束顺序落库，比直接存底层模型原始输出更可靠。
            if kind == "on_chain_end" and event_name in {
                "specialist",
                "tools",
                "synthesize",
            }:
                await _persist_node_output(
                    conversation_id,
                    run_id,
                    event.get("data", {}).get("output"),
                )

            # 工具调用的开始/结束：只转发只读工具的 start/end 让前端画工具行（TOOLS_BY_NAME 兜底
            # 过滤）。写工具（WRITE_TOOL_NAMES）不发——它由前端那条带按钮的审批行代表自己
            # （approval_required + 裁决后翻成"已批准/已拒绝"），再发工具行会重复成两条。
            # start/end 带同一个 run_id，前端据此标记"进行中→完成"。
            is_visible_tool = (
                event_name in TOOLS_BY_NAME and event_name not in WRITE_TOOL_NAMES
            )
            if kind == "on_tool_start" and is_visible_tool:
                await _persist_checkpoint_messages(
                    graph,
                    config,
                    conversation_id,
                    run_id,
                )
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
                        final_answer_chunks.append(content)
                        yield {"type": "token", "content": content}
                    continue

                model_run_id = event["run_id"]
                if content:
                    pending_narration.setdefault(model_run_id, []).append(content)
                if _business_tool_names(chunk):
                    visible_narration_runs.add(model_run_id)
                    buffered = "".join(pending_narration.pop(model_run_id, []))
                    if buffered:
                        yield {"type": "token", "content": buffered}
                elif model_run_id in visible_narration_runs and content:
                    # 极少数模型会在 tool_call chunk 后继续输出文字；确认可见后可以直接续流。
                    yield {"type": "token", "content": content}
            elif kind == "on_chat_model_end" and node != "synthesize":
                model_run_id = event["run_id"]
                output = event.get("data", {}).get("output")
                # 某些 OpenAI 兼容服务不在 stream chunk 里给完整工具名，只在最终 AIMessage 给；
                # 在模型结束事件再兜底识别一次，此时仍早于真正的 on_tool_start。
                if model_run_id not in visible_narration_runs and _business_tool_names(
                    output
                ):
                    buffered = "".join(pending_narration.pop(model_run_id, []))
                    if buffered:
                        yield {"type": "token", "content": buffered}
                pending_narration.pop(model_run_id, None)
                visible_narration_runs.discard(model_run_id)

        # 图停下来了：先看是不是停在审批门。带了 checkpointer，挂起状态能被 aget_state 读出来；
        # snapshot.interrupts 非空即为挂起，其 value 就是 approval_node 里 interrupt(...) 的入参。
        snapshot = await _persist_checkpoint_messages(
            graph,
            config,
            conversation_id,
            run_id,
        )
        if snapshot.interrupts:
            pending = snapshot.interrupts[0].value
            await _set_run_status(run_id, "waiting_approval")
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

        await _set_run_status(run_id, "completed")
        done_event: ChatStreamEvent = {
            "type": "done",
            "conversation_id": str(conversation_id),
        }
        if new_title is not None:
            done_event["title"] = new_title
        yield done_event
    except StreamChunkTimeoutError:
        message = "模型流式响应长时间没有返回新内容，请重试本次提问。"
        try:
            await _persist_checkpoint_messages(
                graph,
                config,
                conversation_id,
                run_id,
            )
            await _persist_partial_answer(
                conversation_id,
                run_id,
                final_answer_chunks,
            )
        except Exception as exc:  # noqa: BLE001 - 继续记录原始超时
            print(f"[chat-run] 保存 {run_id} 的超时现场时出错：{exc}")
        await _mark_failed(run_id, message)
        yield {
            "type": "error",
            "message": message,
        }
    except Exception as exc:  # noqa: BLE001 - 流式响应里要把错误也发给前端，不能直接 500
        message = str(exc)
        try:
            await _persist_checkpoint_messages(
                graph,
                config,
                conversation_id,
                run_id,
            )
            await _persist_partial_answer(
                conversation_id,
                run_id,
                final_answer_chunks,
            )
        except Exception as persist_exc:  # noqa: BLE001 - 继续记录原始异常
            print(f"[chat-run] 保存 {run_id} 的失败现场时出错：{persist_exc}")
        await _mark_failed(run_id, message)
        yield {"type": "error", "message": message}


async def prepare_chat_turn(
    conversation_id: uuid.UUID,
    user_message: str,
) -> PreparedChatTurn:
    """先持久化用户消息并准备图输入，确保 API 返回 run_id 时历史中已经能看到用户提问。

    每轮用一个全新 thread_id：图带了 checkpointer 后，fresh thread 保证 checkpointer 每轮从空
    开始，和"照旧把完整历史喂进来"不冲突（否则会和上一轮残留状态叠加成重复消息）。命中审批门时，
    这个 thread_id 随 approval_required 发给前端，恢复请求带着它回来才能接上同一次挂起。
    """
    thread_id = uuid.uuid4().hex
    # 短事务：加载历史（还原成真消息序列）+ 用户消息落库。用户这句先落库——即使后面 LLM 调用失败，
    # 至少用户发过什么是有记录的。开完就关，不占着连接跑后面的流式。
    async with AsyncSessionLocal() as db:
        history_messages = await _load_history_messages(db, conversation_id)
        chat_run = await chat_run_record_service.create_chat_run(
            db,
            conversation_id=conversation_id,
            langgraph_thread_id=thread_id,
        )
        try:
            await message_service.create_message(
                db,
                conversation_id,
                run_id=chat_run.id,
                role="user",
                content=user_message,
            )
        except Exception:
            await chat_run_record_service.set_status(
                db,
                chat_run,
                "failed",
                error_message="用户消息落库失败，Chat Run 未启动。",
            )
            raise

    return PreparedChatTurn(
        thread_id=thread_id,
        run_id=chat_run.id,
        graph_input={
            "messages": [*history_messages, HumanMessage(user_message)],
            "work_messages": [],
            "reports": {},
            "specialist_instruction": "",
            "tool_iterations": 0,
            "supervisor_rounds": 0,
            "allow_write_tools": True,
            "approval": None,
            "operation_run_id": str(chat_run.id),
        },
    )


async def run_prepared_chat_turn(
    graph: CompiledStateGraph,
    conversation_id: uuid.UUID,
    prepared: PreparedChatTurn,
    new_title: str | None = None,
) -> AsyncIterator[ChatStreamEvent]:
    """在后台 Chat Run 中执行已经准备好的一轮输入。"""
    async for event in _drive_graph(
        graph,
        conversation_id,
        prepared.graph_input,
        prepared.thread_id,
        prepared.run_id,
        new_title=new_title,
    ):
        yield event


async def resume_chat_turn(
    graph: CompiledStateGraph,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    thread_id: str,
    approved: bool,
    second_confirmation: bool = False,
    reason: str | None = None,
) -> AsyncIterator[ChatStreamEvent]:
    """恢复一次挂在审批门上的对话：把用户裁决用 Command(resume=) 传回图，从挂起点接着往下跑
    （批准→执行写工具 / 拒绝→回喂已拒绝消息 → agent 收尾 → 落库）。不新增用户消息——它只是上一段的
    延续。thread_id 必须和当初 approval_required 里发出去的那个一致，才能接上同一次挂起。
    """
    decision = {
        "approved": approved,
        "second_confirmation": second_confirmation,
        "reason": reason or "",
    }
    async for event in _drive_graph(
        graph,
        conversation_id,
        Command(resume=decision),
        thread_id,
        run_id,
        new_title=None,
    ):
        yield event
