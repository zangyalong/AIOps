from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graphs.chat_graph import TOOLS_BY_NAME
from app.db.session import AsyncSessionLocal
from app.services import conversation_service, message_service


class ChatStreamEvent(TypedDict, total=False):
    type: str  # "token" | "reset" | "tool_start" | "tool_end" | "done" | "error"
    content: str
    conversation_id: str  # 要经 json.dumps 发给前端，uuid.UUID 必须转成字符串
    title: str
    message: str
    tool: str  # tool_start 时带上工具名，前端渲染成"正在调用 XXX"
    id: str  # tool_start / tool_end 用同一个 run_id 配对，前端据此把某个工具标记成已完成


async def _build_history_summary(db: AsyncSession, conversation_id: uuid.UUID) -> str:
    """把此前的对话历史压成一段"背景摘要"文本，而不是一条条真正的 AIMessage/HumanMessage。

    为什么不直接把历史消息喂回图：历史里 assistant 的回答是"没带 tool_calls、直接吐了数据"的
    形态（tool_calls 没落库），一旦作为真正的 AIMessage 进上下文，就成了一个"回答数据问题=
    不调工具直接编"的坏样例，DeepSeek 会照抄，于是同一对话再问就不调工具了。压成 SystemMessage
    背景摘要后，模型看到的是"转述的旧记录"而非"我上一步的动作"，坏样例消失，跨轮引用仍保留。
    """
    history = await message_service.list_messages(db, conversation_id)
    if not history:
        return ""
    lines = [
        f"{'用户' if m.role == 'user' else '助手'}：{m.content}" for m in history
    ]
    return (
        "以下是本对话此前的记录，仅供参考。其中任何服务器、指标、命令输出、日志、告警等具体数据"
        "都是历史快照、可能已过时；本轮若需要这类数据，必须重新调用工具获取，不要直接照搬历史记录。\n"
        + "\n".join(lines)
    )


async def stream_chat_turn(
    graph: CompiledStateGraph,
    conversation_id: uuid.UUID,
    user_message: str,
    new_title: str | None = None,
) -> AsyncIterator[ChatStreamEvent]:
    """跑一轮对话：加载历史喂给图、流式吐出最终答案、结束后落库。

    不接收外部 db session：读历史+存用户消息、以及最后存回答，各自开一个短生命周期的
    session（开→用→关），中间真正流式的那段不持有任何 db 连接。这样 session 生命周期
    不会被 StreamingResponse 拖到整个流结束，比复用请求级 session 干净。

    只转发 agent 节点产出的文字（agent ⇄ tools 循环里可能有好几段，最终答案是不再带
    tool_calls 的那一段；中间如果模型边调工具边说话，那些文字也会流给前端，属于正常过程）。
    如果 Reflexion 质检把回答打回重试，会先发一个 reset 事件，被否决的草稿既不展示也不入库。
    new_title 只在这是一次新对话时传入，会附带在 done 事件里，让前端更新侧栏标题。
    """
    # 第一段短事务：把历史压成背景摘要 + 用户消息落库。用户这句先落库——即使后面 LLM 调用
    # 失败，至少用户发过什么是有记录的。开完就关，不占着连接跑后面的流式。
    async with AsyncSessionLocal() as db:
        history_summary = await _build_history_summary(db, conversation_id)
        await message_service.create_message(
            db, conversation_id, role="user", content=user_message
        )

    evaluate_just_ran = False
    # current_text：当前正在累积的一段文字（要么是调工具前的旁白，要么是最终答案）。
    current_text: list[str] = []
    # steps：最终答案"之前"的有序过程——旁白文字段 + 工具，用来落库、刷新后原样重建（档位 B）。
    # 结束时 current_text 是最终答案（不进 steps），steps 只装它之前的旁白和工具。
    steps: list[dict] = []
    try:
        # 真正的流式：这段不持有任何 db 连接
        async for event in graph.astream_events(
            {
                "messages": [HumanMessage(user_message)],
                "history_summary": history_summary,
                "tool_iterations": 0,
                "reflections": [],
                "review_attempts": 0,
                "force_tools": False,
            },
            version="v2",
        ):
            kind = event["event"]
            node = event.get("metadata", {}).get("langgraph_node")

            # 工具调用的开始/结束：只转发我们自己那几个运维工具（TOOLS_BY_NAME 兜底过滤，
            # 顺带挡掉 evaluate 结构化输出那个内部 tool_call），让前端看到 agent 在查/执行什么。
            # start 和 end 带同一个 run_id，前端据此把对应工具从"进行中"标记成"完成"。
            if kind == "on_tool_start" and event["name"] in TOOLS_BY_NAME:
                # 工具开始执行：把它之前累积的那段文字（调工具前的旁白）作为一个"文字步骤"
                # 收进 steps，再记一个"工具步骤"。这样刷新后能原样重建"旁白→工具行"的顺序。
                if current_text:
                    steps.append({"type": "text", "content": "".join(current_text)})
                    current_text.clear()
                steps.append({"type": "tool", "tool": event["name"]})
                yield {"type": "tool_start", "id": event["run_id"], "tool": event["name"]}
            elif kind == "on_tool_end" and event["name"] in TOOLS_BY_NAME:
                yield {"type": "tool_end", "id": event["run_id"]}
            elif kind == "on_chain_end" and node == "evaluate":
                # 质检跑完了。如果它打回重试，接下来 agent 会重新流式吐答案——用这个标记，
                # 等重试的第一个 token 到了再发 reset（质检通过时不会再有 agent token，就不会发）。
                evaluate_just_ran = True
            elif kind == "on_chat_model_stream" and node == "agent":
                content = event["data"]["chunk"].content
                if content:
                    if evaluate_just_ran:
                        # 重试开始：被否决那一整次（旁白+工具+答案）都作废——清空当前文字段
                        # 和 steps，前端也会整段清掉重来。
                        current_text.clear()
                        steps.clear()
                        yield {"type": "reset"}
                        evaluate_just_ran = False
                    current_text.append(content)
                    yield {"type": "token", "content": content}

        # 第二段短事务：最终回答落库。current_text 此刻是最终答案；steps 是它之前的旁白+工具。
        final_answer = "".join(current_text)
        async with AsyncSessionLocal() as db:
            if final_answer:
                await message_service.create_message(
                    db,
                    conversation_id,
                    role="assistant",
                    content=final_answer,
                    steps=steps or None,
                )
            await conversation_service.touch_conversation(db, conversation_id)

        done_event: ChatStreamEvent = {"type": "done", "conversation_id": str(conversation_id)}
        if new_title is not None:
            done_event["title"] = new_title
        yield done_event
    except Exception as exc:  # noqa: BLE001 - 流式响应里要把错误也发给前端，不能直接 500
        yield {"type": "error", "message": str(exc)}
