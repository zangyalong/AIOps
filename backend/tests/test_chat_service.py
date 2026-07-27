from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import StreamChunkTimeoutError

from app.services import chat_service


class _FakeGraph:
    """只提供 _drive_graph 用到的事件流和最终 checkpoint。"""

    def __init__(self) -> None:
        self.events = [
            {
                "event": "on_chat_model_stream",
                "name": "specialist-model",
                "run_id": "specialist-tool-call",
                "metadata": {"langgraph_node": "metrics_agent"},
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
                "name": "specialist-model",
                "run_id": "specialist-tool-call",
                "metadata": {"langgraph_node": "metrics_agent"},
                "data": {
                    "output": AIMessage(
                        "",
                        tool_calls=[
                            {
                                "name": "query_metrics",
                                "args": {"metric": "cpu"},
                                "id": "tool-run",
                                "type": "tool_call",
                            }
                        ],
                    )
                },
            },
            {
                "event": "on_tool_start",
                "name": "query_metrics",
                "run_id": "tool-run",
            },
            {
                "event": "on_tool_end",
                "name": "query_metrics",
                "run_id": "tool-run",
            },
            {
                "event": "on_chat_model_stream",
                "name": "specialist-model",
                "run_id": "specialist-1",
                "metadata": {"langgraph_node": "metrics_agent"},
                "data": {"chunk": AIMessage("不应展示的专家内部报告")},
            },
            {
                "event": "on_chat_model_end",
                "name": "specialist-model",
                "run_id": "specialist-1",
                "metadata": {"langgraph_node": "metrics_agent"},
                "data": {"output": AIMessage("不应展示的专家内部报告")},
            },
            {
                "event": "on_chat_model_stream",
                "name": "synthesis-model",
                "run_id": "synthesis-1",
                "metadata": {"langgraph_node": "synthesize"},
                "data": {"chunk": AIMessage("一次生成的最终回答")},
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
        del graph_input, config, version
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


class ChatStreamTest(unittest.IsolatedAsyncioTestCase):
    async def test_streams_tool_narration_and_hides_specialist_report(self) -> None:
        """工具旁白、工具卡片和最终回答实时发送，专家内部报告仍不发送。"""
        graph = _FakeGraph()
        conversation_id = uuid.uuid4()
        persist = AsyncMock()

        with patch.object(chat_service, "_persist_turn", persist):
            events = [
                event
                async for event in chat_service._drive_graph(
                    graph,  # type: ignore[arg-type] - 测试替身只实现运行时所需协议
                    conversation_id,
                    graph_input={},
                    thread_id="single-answer-test",
                )
            ]

        self.assertEqual(
            [event["type"] for event in events],
            ["token", "tool_start", "tool_end", "token", "done"],
        )
        self.assertEqual(events[0]["content"], "我先查询服务器的实时指标。")
        self.assertEqual(events[3]["content"], "一次生成的最终回答")
        self.assertNotIn("专家内部报告", str(events))
        persist.assert_awaited_once()

    async def test_stream_timeout_returns_readable_error(self) -> None:
        events = [
            event
            async for event in chat_service._drive_graph(
                _TimeoutGraph(),  # type: ignore[arg-type] - 测试替身只模拟超时
                uuid.uuid4(),
                graph_input={},
                thread_id="timeout-test",
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
