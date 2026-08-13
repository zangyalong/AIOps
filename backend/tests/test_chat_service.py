from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import StreamChunkTimeoutError

from app.models.message import Message
from app.operations.schemas import OperationResult
from app.services import chat_service


class _AsyncSessionContext:
    def __init__(self) -> None:
        self.session = AsyncMock()

    async def __aenter__(self) -> AsyncMock:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeGraph:
    """只提供 _drive_graph 用到的事件流和最终 checkpoint。"""

    def __init__(self) -> None:
        self.run_config: dict | None = None
        self.events = [
            {
                "event": "on_chat_model_stream",
                "name": "ops-model",
                "run_id": "ops-tool-call",
                "metadata": {"langgraph_node": "ops_agent"},
                "data": {
                    "chunk": SimpleNamespace(
                        content="我先查询服务器的实时指标。",
                        tool_calls=[],
                        tool_call_chunks=[],
                    )
                },
            },
            {
                "event": "on_chat_model_end",
                "name": "ops-model",
                "run_id": "ops-tool-call",
                "metadata": {"langgraph_node": "ops_agent"},
                "data": {
                    "output": AIMessage(
                        "",
                        tool_calls=[
                            {
                                "name": "investigate",
                                "args": {"area": "metrics", "query": "cpu"},
                                "id": "tool-run",
                                "type": "tool_call",
                            }
                        ],
                    )
                },
            },
            {
                "event": "on_tool_start",
                "name": "investigate",
                "run_id": "tool-run",
                "data": {"input": {"area": "metrics", "query": "cpu"}},
            },
            {
                "event": "on_tool_end",
                "name": "investigate",
                "run_id": "tool-run",
                "data": {
                    "output": OperationResult.success(
                        "CPU 使用率为 12%",
                        metadata={"evidence_source": "Prometheus"},
                    )
                },
            },
            {
                "event": "on_chat_model_stream",
                "name": "ops-model",
                "run_id": "ops-final",
                "metadata": {"langgraph_node": "ops_agent"},
                "data": {"chunk": AIMessage("一次生成的最终回答")},
            },
            {
                "event": "on_chat_model_end",
                "name": "ops-model",
                "run_id": "ops-final",
                "metadata": {"langgraph_node": "ops_agent"},
                "data": {"output": AIMessage("一次生成的最终回答")},
            },
        ]
        self.snapshot = SimpleNamespace(
            interrupts=[],
            values={
                "messages": [
                    HumanMessage("检查 CPU"),
                    AIMessage("一次生成的最终回答"),
                ]
            },
        )

    async def astream_events(self, graph_input: object, config: dict, version: str):
        self.run_config = config
        del graph_input, version
        for event in self.events:
            yield event

    async def aget_state(self, config: dict) -> SimpleNamespace:
        del config
        return self.snapshot


class _TimeoutGraph:
    async def astream_events(self, graph_input: object, config: dict, version: str):
        del graph_input, config, version
        if False:
            yield
        raise StreamChunkTimeoutError(
            120.0, model_name="deepseek-v4-flash", chunks_received=39
        )


class _ApprovalGraph:
    def __init__(self) -> None:
        self.snapshot = SimpleNamespace(
            interrupts=[
                SimpleNamespace(
                    value={"actions": [{"tool": "register_server_services"}]}
                )
            ],
            values={"messages": [HumanMessage("注册服务")]},
        )

    async def astream_events(self, graph_input: object, config: dict, version: str):
        del graph_input, config, version
        if False:
            yield

    async def aget_state(self, config: dict) -> SimpleNamespace:
        del config
        return self.snapshot


class _TruncatedToolLinkGraph(_FakeGraph):
    def __init__(self) -> None:
        super().__init__()
        original = (
            "http://localhost:3000/explore?schemaVersion=1&"
            "panes=%7B%22aioms%22%3A%7B%22datasource%22%3A%22prometheus%22%7D%7D"
            "&orgId=1"
        )
        self.original = original
        self.events[3]["data"]["output"] = OperationResult.success(
            f"已生成 Grafana 指标走势图链接：\n{original}"
        )
        truncated = (
            "已生成图表：[查看入口]"
            "(http://localhost:3000/explore?schemaVersion=1&panes=%7B...orgId=1)"
        )
        self.events[4]["data"]["chunk"] = AIMessage(truncated)
        self.events[5]["data"]["output"] = AIMessage(truncated)


class ChatStreamTest(unittest.IsolatedAsyncioTestCase):
    async def test_streams_tool_narration_and_single_agent_answer(self) -> None:
        """单 Agent 的工具旁白、调查状态和最终回答按真实顺序发送。"""
        graph = _FakeGraph()
        conversation_id = uuid.uuid4()
        run_id = uuid.uuid4()
        persist_checkpoint = AsyncMock(return_value=graph.snapshot)
        set_status = AsyncMock()

        with (
            patch.object(
                chat_service,
                "_persist_checkpoint_messages",
                persist_checkpoint,
            ),
            patch.object(chat_service, "_persist_node_output", AsyncMock()),
            patch.object(chat_service, "_set_run_status", set_status),
        ):
            events = [
                event
                async for event in chat_service._drive_graph(
                    graph,  # type: ignore[arg-type] - 测试替身只实现运行时所需协议
                    conversation_id,
                    graph_input={},
                    thread_id="single-answer-test",
                    run_id=run_id,
                )
            ]

        self.assertEqual(
            [event["type"] for event in events],
            ["narration", "tool_start", "tool_end", "token", "done"],
        )
        self.assertEqual(events[0]["content"], "我先查询服务器的实时指标。")
        self.assertEqual(events[1]["args"], {"area": "metrics", "query": "cpu"})
        self.assertEqual(events[2]["result"], "CPU 使用率为 12%")
        self.assertFalse(events[2]["failed"])
        self.assertEqual(events[3]["content"], "一次生成的最终回答")
        self.assertIsNotNone(graph.run_config)
        assert graph.run_config is not None
        self.assertEqual(
            graph.run_config["recursion_limit"],
            chat_service.graph_run_config("ignored")["recursion_limit"],
        )
        persist_checkpoint.assert_awaited()
        self.assertEqual(
            [call.args[1] for call in set_status.await_args_list],
            ["running", "completed"],
        )

    async def test_stream_restores_truncated_link_from_tool_result(self) -> None:
        graph = _TruncatedToolLinkGraph()
        with (
            patch.object(
                chat_service,
                "_persist_checkpoint_messages",
                AsyncMock(return_value=graph.snapshot),
            ),
            patch.object(chat_service, "_persist_node_output", AsyncMock()),
            patch.object(chat_service, "_set_run_status", AsyncMock()),
        ):
            events = [
                event
                async for event in chat_service._drive_graph(
                    graph,  # type: ignore[arg-type]
                    uuid.uuid4(),
                    graph_input={},
                    thread_id="restore-tool-link",
                    run_id=uuid.uuid4(),
                )
            ]

        final_event = next(event for event in events if event["type"] == "token")
        self.assertEqual(
            final_event["content"],
            f"已生成图表：[打开原始链接]({graph.original})",
        )

    def test_compact_narration_keeps_only_first_progress_sentence(self) -> None:
        narration = chat_service._compact_narration(
            "我先核对 web-01 上目标单元及当前状态。"
            "然后我需要重新审视所有可能路径并逐项权衡。"
        )

        self.assertEqual(narration, "我先核对 web-01 上目标单元及当前状态。")

    def test_compact_narration_has_hard_length_limit(self) -> None:
        narration = chat_service._compact_narration("核对目标" * 80)

        self.assertLessEqual(len(narration), 96)
        self.assertTrue(narration.endswith("…"))

    def test_write_tool_result_keeps_tool_call_id_and_error_status(self) -> None:
        output = {
            "messages": [
                ToolMessage(
                    "端口冲突，容器未启动",
                    tool_call_id="write-call",
                    name="start_service",
                    status="error",
                )
            ]
        }

        messages = chat_service._write_tool_results(output)
        self.assertEqual([message.tool_call_id for message in messages], ["write-call"])
        result, failed = chat_service._stream_tool_result(messages[0])
        self.assertEqual(result, "端口冲突，容器未启动")
        self.assertTrue(failed)

    async def test_stream_timeout_returns_readable_error(self) -> None:
        run_id = uuid.uuid4()
        mark_failed = AsyncMock()
        with (
            patch.object(
                chat_service,
                "_persist_checkpoint_messages",
                AsyncMock(),
            ),
            patch.object(chat_service, "_persist_partial_answer", AsyncMock()),
            patch.object(chat_service, "_set_run_status", AsyncMock()),
            patch.object(chat_service, "_mark_failed", mark_failed),
        ):
            events = [
                event
                async for event in chat_service._drive_graph(
                    _TimeoutGraph(),  # type: ignore[arg-type] - 测试替身只模拟超时
                    uuid.uuid4(),
                    graph_input={},
                    thread_id="timeout-test",
                    run_id=run_id,
                )
            ]

        self.assertEqual(
            events,
            [
                {
                    "type": "error",
                    "message": "模型流式响应长时间没有返回新内容，请重试本次提问。",
                }
            ],
        )
        mark_failed.assert_awaited_once_with(
            run_id,
            "模型流式响应长时间没有返回新内容，请重试本次提问。",
        )

    async def test_approval_updates_persisted_run_status(self) -> None:
        graph = _ApprovalGraph()
        run_id = uuid.uuid4()
        set_status = AsyncMock()
        with (
            patch.object(
                chat_service,
                "_persist_checkpoint_messages",
                AsyncMock(return_value=graph.snapshot),
            ),
            patch.object(chat_service, "_set_run_status", set_status),
        ):
            events = [
                event
                async for event in chat_service._drive_graph(
                    graph,  # type: ignore[arg-type] - 测试替身只实现运行时协议
                    uuid.uuid4(),
                    graph_input={},
                    thread_id="approval-thread",
                    run_id=run_id,
                )
            ]

        self.assertEqual([event["type"] for event in events], ["approval_required"])
        self.assertEqual(
            [call.args[1] for call in set_status.await_args_list],
            ["running", "waiting_approval"],
        )

    async def test_progressive_message_persistence_is_idempotent(self) -> None:
        conversation_id = uuid.uuid4()
        run_id = uuid.uuid4()
        existing = [
            Message(
                conversation_id=conversation_id,
                run_id=run_id,
                role="assistant",
                content="准备查询",
                tool_calls=[
                    {
                        "name": "query_servers",
                        "args": {},
                        "id": "existing-call",
                        "type": "tool_call",
                    }
                ],
            ),
            Message(
                conversation_id=conversation_id,
                run_id=run_id,
                role="tool",
                content="已有结果",
                tool_call_id="existing-call",
            ),
        ]
        create_message = AsyncMock()
        session_context = _AsyncSessionContext()
        with (
            patch.object(
                chat_service,
                "AsyncSessionLocal",
                return_value=session_context,
            ),
            patch.object(
                chat_service.message_service,
                "list_run_messages",
                AsyncMock(return_value=existing),
            ),
            patch.object(
                chat_service.message_service,
                "create_message",
                create_message,
            ),
            patch.object(
                chat_service.conversation_service,
                "touch_conversation",
                AsyncMock(),
            ),
        ):
            await chat_service._persist_run_messages(
                conversation_id,
                run_id,
                [
                    AIMessage(
                        "准备查询",
                        tool_calls=[
                            {
                                "name": "query_servers",
                                "args": {},
                                "id": "existing-call",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    ToolMessage(
                        "已有结果",
                        tool_call_id="existing-call",
                    ),
                    AIMessage("最终回答"),
                ],
            )

        create_message.assert_awaited_once()
        self.assertEqual(create_message.await_args.kwargs["role"], "assistant")
        self.assertEqual(create_message.await_args.kwargs["content"], "最终回答")
        self.assertEqual(create_message.await_args.kwargs["run_id"], run_id)

    def test_model_history_keeps_only_complete_tool_pairs(self) -> None:
        run_id = uuid.uuid4()
        history = [
            Message(
                conversation_id=uuid.uuid4(),
                run_id=run_id,
                role="user",
                content="先查一下",
            ),
            Message(
                conversation_id=uuid.uuid4(),
                run_id=run_id,
                role="assistant",
                content="准备查询",
                tool_calls=[
                    {
                        "name": "query_servers",
                        "args": {},
                        "id": "complete-call",
                        "type": "tool_call",
                    }
                ],
            ),
            Message(
                conversation_id=uuid.uuid4(),
                run_id=run_id,
                role="tool",
                content="查询完成",
                tool_call_id="complete-call",
            ),
            Message(
                conversation_id=uuid.uuid4(),
                run_id=run_id,
                role="assistant",
                content="第一轮回答",
            ),
        ]

        messages = chat_service._safe_history_messages(history)

        self.assertEqual(
            [type(message) for message in messages],
            [HumanMessage, AIMessage, ToolMessage, AIMessage],
        )
        self.assertEqual(messages[2].tool_call_id, "complete-call")

    def test_model_history_drops_incomplete_tool_batch_but_keeps_scene_neighbors(
        self,
    ) -> None:
        conversation_id = uuid.uuid4()
        failed_run_id = uuid.uuid4()
        history = [
            Message(
                conversation_id=conversation_id,
                run_id=failed_run_id,
                role="user",
                content="检查 web01",
            ),
            Message(
                conversation_id=conversation_id,
                run_id=failed_run_id,
                role="assistant",
                content="准备执行两个工具",
                tool_calls=[
                    {
                        "name": "query_servers",
                        "args": {},
                        "id": "returned-call",
                        "type": "tool_call",
                    },
                    {
                        "name": "query_server_services",
                        "args": {},
                        "id": "missing-call",
                        "type": "tool_call",
                    },
                ],
            ),
            Message(
                conversation_id=conversation_id,
                run_id=failed_run_id,
                role="tool",
                content="只返回了一项",
                tool_call_id="returned-call",
            ),
            Message(
                conversation_id=conversation_id,
                run_id=failed_run_id,
                role="user",
                content="重新试一次",
            ),
        ]

        messages = chat_service._safe_history_messages(history)

        self.assertEqual(
            [message.content for message in messages],
            ["检查 web01", "重新试一次"],
        )
